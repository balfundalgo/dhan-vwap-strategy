from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Dict, Optional

from config import SETTINGS
from dhan_api import DhanAPI
from logger_setup import get_logger


@dataclass
class Position:
    side: str  # SHORT only here
    qty: int
    entry_price: float
    sl_price: float
    symbol: str
    security_id: str
    entry_order_id: str
    sl_order_id: str
    is_open: bool = True
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    exit_time_epoch: Optional[int] = None


class BaseExecutor:
    def enter_short_with_sl(self, *, symbol: str, security_id: str, qty: int, entry_price_hint: float, sl_price: float) -> Position:
        raise NotImplementedError

    def on_ltp(self, security_id: str, ltp: float, ltt_epoch: int) -> Optional[Position]:
        raise NotImplementedError

    def square_off(self, pos: Position, price_hint: float, reason: str) -> Position:
        raise NotImplementedError


class PaperExecutor(BaseExecutor):
    def __init__(self):
        self.log = get_logger("PaperExecutor")
        self.positions: Dict[str, Position] = {}

    def enter_short_with_sl(self, *, symbol: str, security_id: str, qty: int, entry_price_hint: float, sl_price: float) -> Position:
        order_id = f"PAPER-{uuid.uuid4().hex[:10]}"
        pos = Position(
            side="SHORT",
            qty=qty,
            entry_price=entry_price_hint - SETTINGS.slippage_points_paper,
            sl_price=sl_price,
            symbol=symbol,
            security_id=security_id,
            entry_order_id=order_id,
            sl_order_id=f"SL-{order_id}",
        )
        self.positions[security_id] = pos
        self.log.info("PAPER SELL %s qty=%s entry=%.2f sl=%.2f", symbol, qty, pos.entry_price, sl_price)
        return pos

    def on_ltp(self, security_id: str, ltp: float, ltt_epoch: int) -> Optional[Position]:
        pos = self.positions.get(security_id)
        if not pos or not pos.is_open:
            return None
        if ltp >= pos.sl_price:
            pos.is_open = False
            pos.exit_price = pos.sl_price
            pos.exit_reason = "SL"
            pos.exit_time_epoch = ltt_epoch
            self.log.info("PAPER SL HIT %s at %.2f", pos.symbol, pos.exit_price)
            return pos
        return None

    def square_off(self, pos: Position, price_hint: float, reason: str) -> Position:
        if pos.is_open:
            pos.is_open = False
            pos.exit_price = price_hint
            pos.exit_reason = reason
            pos.exit_time_epoch = int(time.time())
            self.log.info("PAPER EXIT %s at %.2f reason=%s", pos.symbol, price_hint, reason)
        return pos


class LiveExecutor(BaseExecutor):
    def __init__(self, api: DhanAPI):
        self.api = api
        self.log = get_logger("LiveExecutor")
        self.positions: Dict[str, Position] = {}

    def enter_short_with_sl(self, *, symbol: str, security_id: str, qty: int, entry_price_hint: float, sl_price: float) -> Position:
        corr_entry = f"ENT-{uuid.uuid4().hex[:12]}"
        entry_payload = {
            "dhanClientId": SETTINGS.dhan_client_id,
            "correlationId": corr_entry,
            "transactionType": "SELL",
            "exchangeSegment": SETTINGS.option_exchange_segment,
            "productType": "INTRADAY",
            "orderType": "MARKET",
            "validity": "DAY",
            "securityId": str(security_id),
            "quantity": int(qty),
        }
        entry_resp = self.api.place_order(entry_payload)
        entry_order_id = str(entry_resp.get("orderId", corr_entry))

        # Broker-side protective SL order. Exact accepted enum may vary by account/product;
        # if your account expects SL / STOP_LOSS naming, change orderType accordingly.
        corr_sl = f"SL-{uuid.uuid4().hex[:12]}"
        sl_payload = {
            "dhanClientId": SETTINGS.dhan_client_id,
            "correlationId": corr_sl,
            "transactionType": "BUY",
            "exchangeSegment": SETTINGS.option_exchange_segment,
            "productType": "INTRADAY",
            "orderType": "STOP_LOSS_MARKET",
            "validity": "DAY",
            "securityId": str(security_id),
            "quantity": int(qty),
            "triggerPrice": float(sl_price),
        }
        sl_resp = self.api.place_order(sl_payload)
        sl_order_id = str(sl_resp.get("orderId", corr_sl))

        pos = Position(
            side="SHORT",
            qty=qty,
            entry_price=entry_price_hint,
            sl_price=sl_price,
            symbol=symbol,
            security_id=str(security_id),
            entry_order_id=entry_order_id,
            sl_order_id=sl_order_id,
        )
        self.positions[str(security_id)] = pos
        self.log.info("LIVE SELL %s qty=%s sl=%.2f entry_order=%s sl_order=%s", symbol, qty, sl_price, entry_order_id, sl_order_id)
        return pos

    def on_ltp(self, security_id: str, ltp: float, ltt_epoch: int) -> Optional[Position]:
        # Live SL is broker-side; this method is mainly for optional local monitoring.
        return None

    def square_off(self, pos: Position, price_hint: float, reason: str) -> Position:
        if not pos.is_open:
            return pos
        try:
            self.api.cancel_order(pos.sl_order_id)
        except Exception as exc:
            self.log.warning("SL cancel failed for %s: %s", pos.symbol, exc)

        exit_payload = {
            "dhanClientId": SETTINGS.dhan_client_id,
            "correlationId": f"SQ-{uuid.uuid4().hex[:12]}",
            "transactionType": "BUY",
            "exchangeSegment": SETTINGS.option_exchange_segment,
            "productType": "INTRADAY",
            "orderType": "MARKET",
            "validity": "DAY",
            "securityId": str(pos.security_id),
            "quantity": int(pos.qty),
        }
        self.api.place_order(exit_payload)
        pos.is_open = False
        pos.exit_price = price_hint
        pos.exit_reason = reason
        pos.exit_time_epoch = int(time.time())
        self.log.info("LIVE EXIT %s reason=%s", pos.symbol, reason)
        return pos
