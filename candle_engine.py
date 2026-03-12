# candle_engine.py
from __future__ import annotations

import time as _time
from dataclasses import dataclass
from typing import List, Optional

from time_utils import minute_bucket_epoch, normalize_dhan_epoch, two_minute_bucket_epoch

MAX_LTT_LAG_SEC = 90


@dataclass
class Candle:
    bucket: int
    open:   float
    high:   float
    low:    float
    close:  float
    volume: float = 0.0


class OneMinuteBuilder:
    def __init__(self):
        self.current:         Optional[Candle] = None
        self.completed:       List[Candle]     = []
        self.last_cum_volume: Optional[float]  = None

    def on_tick(self, ltp: float, ltt_epoch: int) -> Optional[Candle]:
        ltt_norm  = normalize_dhan_epoch(int(ltt_epoch))
        wall_now  = int(_time.time())
        lag       = wall_now - ltt_norm
        effective = wall_now if lag > MAX_LTT_LAG_SEC else ltt_norm
        bucket    = minute_bucket_epoch(effective)
        ltp       = float(ltp)

        if self.current is None:
            self.current = Candle(bucket=bucket, open=ltp, high=ltp, low=ltp, close=ltp)
            return None

        if bucket == self.current.bucket:
            if ltp > self.current.high: self.current.high = ltp
            if ltp < self.current.low:  self.current.low  = ltp
            self.current.close = ltp
            return None

        if bucket > self.current.bucket:
            done         = self.current
            self.completed.append(done)
            self.current = Candle(bucket=bucket, open=ltp, high=ltp, low=ltp, close=ltp)
            return done

        return None  # out-of-order — ignore

    def seed_current_candle(self, candle: Candle) -> None:
        self.current = Candle(
            bucket=candle.bucket, open=candle.open, high=candle.high,
            low=candle.low, close=candle.close, volume=0.0,
        )

    def on_full_tick(self, ltp: float, cum_day_volume: float) -> float:
        if self.last_cum_volume is None:
            self.last_cum_volume = cum_day_volume
            return 0.0
        delta = max(0.0, cum_day_volume - self.last_cum_volume)
        if self.current is not None:
            self.current.volume += delta
        self.last_cum_volume = cum_day_volume
        return delta


class TwoMinuteAggregator:
    def __init__(self):
        self.buffer_1m: Optional[Candle] = None

    def on_completed_1m(self, c1: Candle) -> Optional[Candle]:
        b2 = two_minute_bucket_epoch(c1.bucket)
        if self.buffer_1m is None:
            self.buffer_1m = c1
            return None
        b2_buf = two_minute_bucket_epoch(self.buffer_1m.bucket)
        if b2_buf == b2:
            c0, self.buffer_1m = self.buffer_1m, None
            return Candle(bucket=b2, open=c0.open, high=max(c0.high, c1.high),
                          low=min(c0.low, c1.low), close=c1.close,
                          volume=c0.volume + c1.volume)
        self.buffer_1m = c1
        return None


class SessionVWAP:
    def __init__(self):
        self._pv = 0.0
        self._vol = 0.0

    def seed_from_candle(self, c: Candle) -> None:
        if c.volume > 0:
            self._pv  += ((c.high + c.low + c.close) / 3.0) * c.volume
            self._vol += c.volume

    def add_tick(self, price: float, vol_delta: float) -> None:
        if vol_delta > 0:
            self._pv  += price * vol_delta
            self._vol += vol_delta

    @property
    def value(self) -> float:
        return float("nan") if self._vol <= 0 else self._pv / self._vol

    def reset(self) -> None:
        self._pv = self._vol = 0.0


def vwap_from_session(candles: List[Candle]) -> float:
    pv = vol = 0.0
    for c in candles:
        if c.volume > 0:
            pv  += ((c.high + c.low + c.close) / 3.0) * c.volume
            vol += c.volume
    return candles[-1].close if vol <= 0 else pv / vol
