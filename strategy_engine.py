from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from candle_engine import Candle, SessionVWAP, TwoMinuteAggregator
from config import SETTINGS
from executors import BaseExecutor, Position
from logger_setup import get_logger
from time_utils import IST, hhmm_to_minutes, now_minutes


@dataclass
class TriggerState:
    trigger_candle:              Optional[Candle]   = None
    pending_entry_for_next_open: bool               = False
    entry_ready_from_trigger:    Optional[Candle]   = None
    open_position:               Optional[Position] = None
    trades_taken:                int                = 0
    last_completed_2m:           Optional[Candle]   = None
    session_2m:                  List[Candle]       = field(default_factory=list)


class OptionLegStrategy:
    def __init__(self, symbol: str, security_id: str, qty: int, executor: BaseExecutor):
        self.symbol       = symbol
        self.security_id  = str(security_id)
        self.qty          = int(qty)
        self.executor     = executor
        self.log          = get_logger(f"Strat-{symbol}")
        self.state        = TriggerState()
        self.agg          = TwoMinuteAggregator()
        self.session_vwap = SessionVWAP()

    def seed_history_1m(self, candles_1m: List[Candle]) -> None:
        self.agg.buffer_1m = None
        self.state.session_2m.clear()
        self.state.last_completed_2m = None
        self.session_vwap.reset()
        for c1 in candles_1m:
            self.session_vwap.seed_from_candle(c1)
            c2 = self.agg.on_completed_1m(c1)
            if c2:
                self.state.last_completed_2m = c2
                self.state.session_2m.append(c2)
        if self.state.session_2m:
            self.log.info("%s seeded: %d 2m candles. Bootstrap VWAP=%.2f",
                          self.symbol, len(self.state.session_2m), self.session_vwap.value)

    def on_full_tick(self, ltp: float, vol_delta: float) -> None:
        self.session_vwap.add_tick(ltp, vol_delta)

    def on_ltp(self, ltp: float, ltt_epoch: int) -> None:
        if self.state.open_position and self.state.open_position.is_open:
            closed = self.executor.on_ltp(self.security_id, ltp, ltt_epoch)
            if closed and not closed.is_open:
                self.log.info("%s SL hit: exit=%.2f", self.symbol, closed.exit_price or 0.0)
                self.state.open_position = None

    def on_completed_1m(self, candle_1m: Candle) -> None:
        c2 = self.agg.on_completed_1m(candle_1m)
        if c2 is None:
            return
        self.state.last_completed_2m = c2
        self.state.session_2m.append(c2)
        vwap         = self.session_vwap.value
        candle_label = datetime.fromtimestamp(c2.bucket, IST).strftime("%H:%M")
        self.log.info("%s 2m [%s] O=%.2f H=%.2f L=%.2f C=%.2f V=%.0f VWAP=%.2f",
                      self.symbol, candle_label,
                      c2.open, c2.high, c2.low, c2.close, c2.volume, vwap)

        if self.state.open_position and self.state.open_position.is_open:
            return

        cur_min = now_minutes()
        if cur_min < hhmm_to_minutes(SETTINGS.first_trade_allowed_hhmm):
            self.state.trigger_candle = None
            self.state.pending_entry_for_next_open = False
            self.state.entry_ready_from_trigger    = None
            return
        if cur_min >= hhmm_to_minutes(SETTINGS.stop_new_entries_hhmm):
            return
        if self.state.trades_taken >= SETTINGS.max_trades_per_leg:
            return

        # Phase 1 — fire pending entry
        if self.state.pending_entry_for_next_open and self.state.entry_ready_from_trigger:
            trig        = self.state.entry_ready_from_trigger
            entry_price = c2.open
            sl          = self._compute_sl(entry_price, trig.high)
            pos = self.executor.enter_short_with_sl(
                symbol=self.symbol, security_id=self.security_id,
                qty=self.qty, entry_price_hint=entry_price, sl_price=sl,
            )
            self.state.open_position               = pos
            self.state.trades_taken               += 1
            self.state.pending_entry_for_next_open = False
            self.state.entry_ready_from_trigger    = None
            return

        if math.isnan(vwap):
            return

        current_is_trigger = c2.close < vwap

        # Phase 2 — look for trigger
        if self.state.trigger_candle is None:
            if current_is_trigger:
                self.state.trigger_candle = c2
                self.log.info("%s trigger SET: low=%.2f high=%.2f", self.symbol, c2.low, c2.high)
            return

        # Phase 3 — look for confirmation
        trig = self.state.trigger_candle
        if c2.close < trig.low:
            self.state.pending_entry_for_next_open = True
            self.state.entry_ready_from_trigger    = trig
            self.log.info("%s CONFIRMED. SELL at next open.", self.symbol)
            self.state.trigger_candle = c2 if current_is_trigger else None
            return

        self.log.info("%s trigger CANCELLED.", self.symbol)
        self.state.trigger_candle = c2 if current_is_trigger else None

    def square_off_if_open(self, price_hint: float) -> None:
        if self.state.open_position and self.state.open_position.is_open:
            self.executor.square_off(self.state.open_position, price_hint, "EOD_1515")
            self.state.open_position = None

    def _compute_sl(self, entry_price: float, trigger_high: float) -> float:
        dist = float(trigger_high) - entry_price
        if dist <= 0:                          return entry_price + SETTINGS.min_sl_points
        if dist < SETTINGS.min_sl_points:      return entry_price + SETTINGS.min_sl_points
        if dist > SETTINGS.max_sl_points:      return entry_price + SETTINGS.max_sl_points
        return float(trigger_high)
