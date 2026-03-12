from __future__ import annotations

import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Settings:
    mode: str = os.getenv("TRADING_MODE", "PAPER").strip().upper()  # PAPER / LIVE
    dhan_client_id: str = os.getenv("DHAN_CLIENT_ID", "").strip()
    dhan_access_token: str = os.getenv("DHAN_ACCESS_TOKEN", "").strip()

    # Trading window (Asia/Kolkata)
    market_open_hhmm: str = "09:15"
    atm_reference_hhmm: str = "09:17"          # reference available at 09:17 from 09:16 candle close
    first_trade_allowed_hhmm: str = "09:21"    # no trades / setups before this time
    stop_new_entries_hhmm: str = "14:45"
    square_off_hhmm: str = "15:15"

    # Strategy
    max_trades_per_leg: int = 3
    min_sl_points: float = 10.0
    max_sl_points: float = 20.0
    vwap_price_mode: str = "HLC3"  # (H+L+C)/3 * vol cumulative

    # Dhan instruments
    nifty_index_security_id: str = "13"   # NIFTY 50 index on Dhan IDX_I
    nifty_index_exchange_segment: str = "IDX_I"
    option_exchange_segment: str = "NSE_FNO"

    instrument_csv_url: str = "https://images.dhan.co/api-data/api-scrip-master-detailed.csv"
    rest_base_url: str = "https://api.dhan.co/v2"
    market_feed_ws_url: str = (
        "wss://api-feed.dhan.co?version=2"
        f"&token={os.getenv('DHAN_ACCESS_TOKEN', '').strip()}"
        f"&clientId={os.getenv('DHAN_CLIENT_ID', '').strip()}&authType=2"
    )
    order_update_ws_url: str = "wss://api-order-update.dhan.co"

    # Execution behavior
    poll_interval_sec: float = 0.25
    state_file: str = "strategy_state.json"

    # For paper mode only
    slippage_points_paper: float = 0.0


SETTINGS = Settings()
