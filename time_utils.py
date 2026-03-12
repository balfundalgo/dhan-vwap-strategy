from __future__ import annotations

import time
from datetime import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
_MARKET_OPEN_IST_MIN = 9 * 60 + 15  # 555


def now_ist() -> datetime:
    return datetime.now(IST)

def hhmm_to_minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)

def now_minutes() -> int:
    n = now_ist()
    return n.hour * 60 + n.minute

def round_to_nearest_50(value: float) -> int:
    return int(round(value / 50.0) * 50)


def normalize_dhan_epoch(ts: int) -> int:
    ts   = int(ts)
    diff = ts - int(time.time())
    if int(4.5 * 3600) <= diff <= int(6.5 * 3600):
        ts -= 19800
    return ts


def minute_bucket_epoch(epoch_sec: int) -> int:
    epoch_sec = normalize_dhan_epoch(int(epoch_sec))
    return epoch_sec - (epoch_sec % 60)


def two_minute_bucket_epoch(epoch_sec: int) -> int:
    epoch_sec      = normalize_dhan_epoch(int(epoch_sec))
    m              = epoch_sec - (epoch_sec % 60)
    ist_min        = ((m + 19800) % 86400) // 60
    if ist_min >= _MARKET_OPEN_IST_MIN:
        slot_offset    = ((ist_min - _MARKET_OPEN_IST_MIN) // 2) * 2
        bucket_ist_min = _MARKET_OPEN_IST_MIN + slot_offset
    else:
        bucket_ist_min = (ist_min // 2) * 2
    return m - (ist_min - bucket_ist_min) * 60
