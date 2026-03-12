from __future__ import annotations

import json, struct, threading, time
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional, Tuple, Any

import websocket
from config import SETTINGS
from logger_setup import get_logger

REQ_CONNECT_FEED = 11
REQ_SUB_TICKER   = 15
REQ_SUB_FULL     = 21
RESP_TICKER      = 2
RESP_FULL        = 8


@dataclass
class MarketEvent:
    security_id:    int
    kind:           str
    ltp:            float
    ltt_epoch:      int
    cum_day_volume: Optional[float] = None


def _u8(b, o):   return b[o]
def _u16(b, o):  return struct.unpack_from("<H", b, o)[0]
def _u32(b, o):  return struct.unpack_from("<I", b, o)[0]
def _f32(b, o):  return float(struct.unpack_from("<f", b, o)[0])


def iter_packets(frame: bytes) -> Iterable[Dict[str, Any]]:
    i, n = 0, len(frame)
    while i + 8 <= n:
        ml = _u16(frame, i + 1)
        if ml < 8 or i + ml > n: break
        yield {"resp": _u8(frame, i), "sid": int(_u32(frame, i + 4)),
               "payload": frame[i + 8: i + ml]}
        i += ml


def _parse_ticker(p):
    return {"ltp": _f32(p, 0), "ltt": int(_u32(p, 4))} if len(p) >= 8 else None

def _parse_full(p):
    return {"ltp": _f32(p, 0), "ltt": int(_u32(p, 6)),
            "cum_vol": float(_u32(p, 14))} if len(p) >= 18 else None


class DhanMarketFeed:
    def __init__(self, on_event: Callable[[MarketEvent], None]):
        self.log      = get_logger("MarketFeed")
        self.on_event = on_event
        self.ws:      Optional[websocket.WebSocketApp] = None
        self._thread: Optional[threading.Thread]       = None
        self._stop    = threading.Event()

    def start(self, instruments: List[Tuple[int, str]]) -> None:
        """instruments: list of (security_id, exchange_segment)"""
        if self._thread and self._thread.is_alive(): return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, args=(instruments,), daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        try:
            if self.ws: self.ws.close()
        except Exception: pass

    def _run(self, instruments):
        tok, cid = SETTINGS.dhan_access_token, SETTINGS.dhan_client_id
        url  = f"wss://api-feed.dhan.co?version=2&token={tok}&clientId={cid}&authType=2"
        subs = [{"ExchangeSegment": seg, "SecurityId": str(sid)} for sid, seg in instruments]
        self.log.info("WS connecting. instruments=%d", len(subs))

        def on_open(ws):
            ws.send(json.dumps({"RequestCode": REQ_CONNECT_FEED}))
            time.sleep(0.2)
            ws.send(json.dumps({"RequestCode": REQ_SUB_TICKER,
                                "InstrumentCount": len(subs), "InstrumentList": subs}))
            ws.send(json.dumps({"RequestCode": REQ_SUB_FULL,
                                "InstrumentCount": len(subs), "InstrumentList": subs}))
            self.log.info("Subscribed TICKER+FULL for %d instruments.", len(subs))

        def on_message(ws, msg):
            if not isinstance(msg, (bytes, bytearray)):
                if isinstance(msg, str): self.log.warning("WS TEXT: %s", msg)
                return
            for pkt in iter_packets(msg):
                r, sid, pay = pkt["resp"], pkt["sid"], pkt["payload"]
                if r == RESP_TICKER:
                    d = _parse_ticker(pay)
                    if d: self.on_event(MarketEvent(sid, "TICKER", d["ltp"], d["ltt"]))
                elif r == RESP_FULL:
                    d = _parse_full(pay)
                    if d: self.on_event(MarketEvent(sid, "FULL", d["ltp"], d["ltt"],
                                                    d["cum_vol"]))

        self.ws = websocket.WebSocketApp(
            url, on_open=on_open, on_message=on_message,
            on_error=lambda ws, e: self.log.warning("WS error: %s", e),
            on_close=lambda ws, c, m: self.log.warning("WS closed: %s %s", c, m),
        )
        while not self._stop.is_set():
            self.ws.run_forever(ping_interval=20, ping_timeout=10)
            if self._stop.is_set(): break
            self.log.warning("WS disconnected. Reconnecting in 2s...")
            time.sleep(2)
