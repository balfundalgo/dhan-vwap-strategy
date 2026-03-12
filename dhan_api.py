from __future__ import annotations

import io
from typing import Any, Dict, List, Optional

import pandas as pd
import requests

from config import SETTINGS
from logger_setup import get_logger


class DhanAPI:
    def __init__(self):
        self.log = get_logger("DhanAPI")
        self.base = SETTINGS.rest_base_url
        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json",
            "access-token": SETTINGS.dhan_access_token,
            "client-id": SETTINGS.dhan_client_id,
            "dhanClientId": SETTINGS.dhan_client_id,
        })

    def _request(self, method: str, path: str, *, json_data: Optional[dict] = None, params: Optional[dict] = None) -> Any:
        url = f"{self.base}{path}"
        resp = self.session.request(method, url, json=json_data, params=params, timeout=20)
        resp.raise_for_status()
        if not resp.text.strip():
            return {}
        return resp.json()

    def profile(self) -> dict:
        return self._request("GET", "/profile")

    def load_instrument_master(self) -> pd.DataFrame:
        self.log.info("Fetching Dhan instrument master...")
        r = requests.get(SETTINGS.instrument_csv_url, timeout=30)
        r.raise_for_status()
        df = pd.read_csv(io.StringIO(r.text), low_memory=False)
        df.columns = [str(c).strip() for c in df.columns]
        for col in df.columns:
            if df[col].dtype == object:
                df[col] = df[col].astype(str).str.strip()
        return df

    def intraday_minute_data(self, security_id: str, exchange_segment: str, from_dt: str, to_dt: str, interval: int = 1) -> dict:
        payload = {
            "securityId": str(security_id),
            "exchangeSegment": exchange_segment,
            "instrument": "INDEX" if exchange_segment == "IDX_I" else "OPTIDX",
            "interval": interval,
            "fromDate": from_dt,
            "toDate": to_dt,
        }
        return self._request("POST", "/charts/intraday", json_data=payload)

    def place_order(self, payload: dict) -> dict:
        return self._request("POST", "/orders", json_data=payload)

    def modify_order(self, order_id: str, payload: dict) -> dict:
        return self._request("PUT", f"/orders/{order_id}", json_data=payload)

    def cancel_order(self, order_id: str) -> dict:
        return self._request("DELETE", f"/orders/{order_id}")

    def get_orders(self) -> Any:
        return self._request("GET", "/orders")

    def get_order(self, order_id: str) -> Any:
        return self._request("GET", f"/orders/{order_id}")

    def get_order_by_correlation(self, correlation_id: str) -> Any:
        return self._request("GET", f"/orders/external/{correlation_id}")

    def get_trades(self) -> Any:
        return self._request("GET", "/trades")

    def get_trade(self, order_id: str) -> Any:
        return self._request("GET", f"/trades/{order_id}")

    def get_positions(self) -> Any:
        return self._request("GET", "/positions")

    def exit_all_positions(self) -> Any:
        return self._request("DELETE", "/positions")
