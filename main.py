from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from candle_engine import Candle, OneMinuteBuilder
from config import SETTINGS
from dhan_api import DhanAPI
from executors import LiveExecutor, PaperExecutor
from instrument_resolver import InstrumentResolver
from logger_setup import get_logger
from market_feed import DhanMarketFeed, MarketEvent
from strategy_engine import OptionLegStrategy
from time_utils import IST, hhmm_to_minutes, now_ist, now_minutes

log = get_logger("Main")


# ─── helpers ─────────────────────────────────────────────────────────────────

def _to_ist_dt(ts) -> Optional[datetime]:
    if ts is None: return None
    if isinstance(ts, (int, float)) or (isinstance(ts, str) and str(ts).isdigit()):
        v = int(ts)
        if v > 10_000_000_000: v //= 1000
        return datetime.fromtimestamp(v, IST)
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return d.astimezone(IST) if d.tzinfo else d.replace(tzinfo=IST)
    except Exception:
        try: return datetime.strptime(str(ts), "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
        except Exception: return None


def _rows_from_intraday(data) -> List[dict]:
    if isinstance(data, dict):
        if isinstance(data.get("data"), list):
            return data["data"]
        if all(k in data for k in ("open", "high", "low", "close")):
            n   = len(data.get("close", []))
            ts  = data.get("timestamp") or data.get("time") or data.get("start_Time") or data.get("startTime") or []
            vol = data.get("volume") or []
            return [{"timestamp": ts[i] if i < len(ts) else None,
                     "open": data["open"][i], "high": data["high"][i],
                     "low": data["low"][i],   "close": data["close"][i],
                     "volume": vol[i] if i < len(vol) else 0} for i in range(n)]
    if isinstance(data, list): return data
    return []


def _parse_hist_1m(rows: List[dict]) -> List[Candle]:
    out = []
    for r in rows:
        ts = (r.get("timestamp") or r.get("time") or r.get("start_Time")
              or r.get("startTime") or r.get("datetime") or r.get("dateTime"))
        dt = _to_ist_dt(ts)
        if not dt: continue
        try:
            out.append(Candle(bucket=int(dt.timestamp()),
                              open=float(r["open"]),  high=float(r["high"]),
                              low=float(r["low"]),    close=float(r["close"]),
                              volume=float(r.get("volume", 0) or 0)))
        except Exception: continue
    out.sort(key=lambda c: c.bucket)
    return out


def _find_nifty_0916_close(api: DhanAPI) -> float:
    today = now_ist().date()
    data  = api.intraday_minute_data(
        security_id=SETTINGS.nifty_index_security_id,
        exchange_segment=SETTINGS.nifty_index_exchange_segment,
        from_dt=f"{today} 09:14:00", to_dt=f"{today} 09:18:00", interval=1,
    )
    for c in _parse_hist_1m(_rows_from_intraday(data)):
        dt = datetime.fromtimestamp(c.bucket, IST)
        if dt.hour == 9 and dt.minute == 16:
            log.info("ATM ref: 09:16 close=%.2f", c.close)
            return float(c.close)
    raise RuntimeError("Could not find NIFTY 09:16 candle for ATM reference")


def _seed_leg(api: DhanAPI, sid: int, seg: str, leg: OptionLegStrategy) -> Tuple[int, int]:
    today = now_ist().date()
    end   = (now_ist().replace(second=0, microsecond=0) - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
    data  = api.intraday_minute_data(int(sid), seg, f"{today} 09:15:00", end, 1)
    c1s   = _parse_hist_1m(_rows_from_intraday(data))
    if not c1s:
        log.warning("No historical 1m candles for sid=%s", sid)
        return (0, 0)
    leg.seed_history_1m(c1s)
    return (len(c1s), len(leg.state.session_2m))


def _fetch_partial_1m(api: DhanAPI, sid: int, seg: str) -> Optional[Candle]:
    now_dt = now_ist()
    start  = (now_dt.replace(second=0, microsecond=0) - timedelta(minutes=2)).strftime("%Y-%m-%d %H:%M:%S")
    end    = now_dt.strftime("%Y-%m-%d %H:%M:%S")
    try:
        c = _parse_hist_1m(_rows_from_intraday(api.intraday_minute_data(int(sid), seg, start, end, 1)))
        return c[-1] if c else None
    except Exception as ex:
        log.warning("Partial candle fetch failed sid=%s: %s", sid, ex)
        return None


@dataclass
class LegRuntime:
    sid:     int
    seg:     str
    builder: OneMinuteBuilder
    strat:   OptionLegStrategy


# ─── App ──────────────────────────────────────────────────────────────────────

class App:
    def __init__(self):
        self.api      = DhanAPI()
        self.resolver = InstrumentResolver(self.api)
        self.executor = (LiveExecutor(self.api) if SETTINGS.mode.upper() == "LIVE"
                         else PaperExecutor())
        self.feed:    Optional[DhanMarketFeed] = None
        self.ce:      Optional[LegRuntime]     = None
        self.pe:      Optional[LegRuntime]     = None
        self._builders: Dict[int, OneMinuteBuilder] = {}
        self.nifty_builder  = OneMinuteBuilder()
        self.nifty_ref:     Optional[float] = None
        self.running:       bool            = False
        self.status_msg:    str             = "Idle"

    def setup_day(self):
        self.status_msg = "Fetching ATM reference..."
        self.nifty_ref  = _find_nifty_0916_close(self.api)

        self.status_msg = "Resolving instruments..."
        ce_opt, pe_opt, lot = self.resolver.resolve_nifty_atm_options(self.nifty_ref)
        ce_sid, pe_sid = int(ce_opt.security_id), int(pe_opt.security_id)

        ce_strat = OptionLegStrategy(ce_opt.symbol, str(ce_sid), lot, self.executor)
        pe_strat = OptionLegStrategy(pe_opt.symbol, str(pe_sid), lot, self.executor)

        self.ce = LegRuntime(ce_sid, SETTINGS.option_exchange_segment, OneMinuteBuilder(), ce_strat)
        self.pe = LegRuntime(pe_sid, SETTINGS.option_exchange_segment, OneMinuteBuilder(), pe_strat)
        self._builders = {
            ce_sid: self.ce.builder,
            pe_sid: self.pe.builder,
            int(SETTINGS.nifty_index_security_id): self.nifty_builder,
        }

        self.status_msg = "Seeding history..."
        for lr in (self.ce, self.pe):
            n1, n2 = _seed_leg(self.api, lr.sid, lr.seg, lr.strat)
            log.info("Seeded %s: %d 1m → %d 2m", lr.strat.symbol, n1, n2)

        self.status_msg = "Pre-seeding current candle..."
        for lr in (self.ce, self.pe):
            p = _fetch_partial_1m(self.api, lr.sid, lr.seg)
            if p:
                lr.builder.seed_current_candle(p)
                log.info("Orphan candle seeded for %s O=%.2f", lr.strat.symbol, p.open)

        log.info("Day init complete. Mode=%s ATM=%.2f", SETTINGS.mode.upper(), self.nifty_ref)
        self.status_msg = f"Running — ATM {round(self.nifty_ref / 50) * 50}"

    def on_event(self, e: MarketEvent):
        sid = int(e.security_id)
        b   = self._builders.get(sid)
        if b is None: return

        if sid == int(SETTINGS.nifty_index_security_id):
            if e.kind == "TICKER": b.on_tick(e.ltp, e.ltt_epoch)
            return

        leg = self.ce if (self.ce and sid == self.ce.sid) else \
              self.pe if (self.pe and sid == self.pe.sid) else None
        if leg is None: return

        if e.kind == "TICKER":
            leg.strat.on_ltp(e.ltp, e.ltt_epoch)
            done = b.on_tick(e.ltp, e.ltt_epoch)
            if done: leg.strat.on_completed_1m(done)
        elif e.kind == "FULL" and e.cum_day_volume is not None:
            delta = b.on_full_tick(e.ltp, e.cum_day_volume)
            if delta > 0: leg.strat.on_full_tick(e.ltp, delta)

    def _square_off_all(self):
        for lr in (self.ce, self.pe):
            if lr is None: continue
            ph = lr.strat.state.last_completed_2m.close \
                 if lr.strat.state.last_completed_2m else 0.0
            lr.strat.square_off_if_open(ph)

    def get_status(self) -> Dict[str, Any]:
        """Snapshot of current state for GUI polling."""
        def leg_snap(lr: Optional[LegRuntime]) -> Dict[str, Any]:
            if lr is None:
                return {}
            st  = lr.strat.state
            pos = st.open_position
            c2  = st.last_completed_2m
            vwap = lr.strat.session_vwap.value
            return {
                "symbol":     lr.strat.symbol,
                "vwap":       vwap,
                "candle":     {"bucket": c2.bucket, "open": c2.open, "high": c2.high,
                               "low": c2.low, "close": c2.close, "volume": c2.volume} if c2 else None,
                "trigger":    bool(st.trigger_candle),
                "pending":    st.pending_entry_for_next_open,
                "trades":     st.trades_taken,
                "position":   {
                    "entry": pos.entry_price, "sl": pos.sl_price,
                    "is_open": pos.is_open,
                    "exit":  pos.exit_price, "reason": pos.exit_reason,
                } if pos else None,
            }
        return {
            "status_msg": self.status_msg,
            "mode":       SETTINGS.mode.upper(),
            "nifty_ref":  self.nifty_ref,
            "ce":         leg_snap(self.ce),
            "pe":         leg_snap(self.pe),
        }

    def run(self):
        self.running = True
        try:
            self.api.profile()
        except Exception as ex:
            log.warning("Profile check failed: %s", ex)
        self.setup_day()
        instruments = [
            (int(SETTINGS.nifty_index_security_id), SETTINGS.nifty_index_exchange_segment),
        ]
        if self.ce: instruments.append((self.ce.sid, self.ce.seg))
        if self.pe: instruments.append((self.pe.sid, self.pe.seg))
        self.feed = DhanMarketFeed(self.on_event)
        self.feed.start(instruments)
        sq_min = hhmm_to_minutes(SETTINGS.square_off_hhmm)
        while self.running:
            time.sleep(1)
            if now_minutes() >= sq_min:
                log.info("15:15 square-off triggered.")
                self._square_off_all()
                self.status_msg = "Squared off — EOD"
                self.running = False
        if self.feed: self.feed.stop()
        log.info("Strategy run complete.")

    def stop(self):
        self.running = False
        self._square_off_all()
        if self.feed: self.feed.stop()
        self.status_msg = "Stopped"


if __name__ == "__main__":
    App().run()
