from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

import pandas as pd

from config import SETTINGS
from dhan_api import DhanAPI
from logger_setup import get_logger
from time_utils import round_to_nearest_50


@dataclass
class ResolvedOption:
    symbol: str
    security_id: str
    lot_size: int
    exchange_segment: str = SETTINGS.option_exchange_segment


class InstrumentResolver:
    def __init__(self, api: DhanAPI):
        self.api = api
        self.log = get_logger("InstrumentResolver")
        self.df: Optional[pd.DataFrame] = None

    def _ensure_master(self):
        if self.df is None:
            self.df = self.api.load_instrument_master()

    def _col(self, *names: str, required: bool = True) -> Optional[str]:
        assert self.df is not None
        cols = {c.upper(): c for c in self.df.columns}
        for name in names:
            if name.upper() in cols:
                return cols[name.upper()]
        if required:
            raise KeyError(f"Missing required column from Dhan master: one of {names}")
        return None

    def _get_underlying_col(self) -> Optional[str]:
        return self._col("UNDERLYING_SYMBOL", "SEM_UNDERLYING_SYMBOL", required=False)

    def _filter_nifty_optidx(self, df: pd.DataFrame) -> pd.DataFrame:
        c_seg = self._col("SEGMENT")
        c_exch = self._col("EXCH_ID")
        c_instr = self._col("INSTRUMENT")
        c_under = self._get_underlying_col()
        c_sym = self._col("SYMBOL_NAME", "SM_SYMBOL_NAME")

        base = df[
            (df[c_exch].astype(str).str.upper() == "NSE") &
            (df[c_seg].astype(str).str.upper() == "D") &
            (df[c_instr].astype(str).str.upper() == "OPTIDX")
        ].copy()

        if c_under:
            under_match = base[c_under].astype(str).str.upper().str.strip() == "NIFTY"
            if under_match.any():
                return base[under_match].copy()

        sym = base[c_sym].astype(str).str.upper().str.strip()
        fallback = base[
            sym.eq("NIFTY") |
            sym.str.contains("NIFTY", na=False)
        ].copy()
        return fallback

    def nearest_weekly_expiry(self) -> pd.Timestamp:
        self._ensure_master()
        df = self.df.copy()

        c_exp = self._col("SM_EXPIRY_DATE", "SEM_EXPIRY_DATE")
        c_exp_flag = self._col("EXPIRY_FLAG", "SEM_EXPIRY_FLAG", required=False)

        df[c_exp] = pd.to_datetime(df[c_exp], errors="coerce").dt.normalize()
        today = pd.Timestamp.now().normalize()

        sub = self._filter_nifty_optidx(df)
        sub = sub[sub[c_exp].notna() & (sub[c_exp] >= today)].copy()

        if sub.empty:
            raise ValueError("No live NIFTY option expiry found in Dhan instrument master")

        if c_exp_flag and c_exp_flag in sub.columns:
            sub["_is_weekly"] = sub[c_exp_flag].astype(str).str.upper().eq("W")
            if sub["_is_weekly"].any():
                sub = sub[sub["_is_weekly"]].copy()

        sub = sub.sort_values(c_exp)

        exp = pd.Timestamp(sub.iloc[0][c_exp]).normalize()
        self.log.info("Using expiry: %s", exp.date())
        return exp

    def resolve_nifty_atm_options(self, nifty_close: float) -> Tuple[ResolvedOption, ResolvedOption, int]:
        self._ensure_master()
        atm = round_to_nearest_50(float(nifty_close))
        expiry = self.nearest_weekly_expiry()

        df = self.df.copy()
        c_exp = self._col("SM_EXPIRY_DATE", "SEM_EXPIRY_DATE")
        c_sid = self._col("SECURITY_ID")
        c_disp = self._col("DISPLAY_NAME", "SEM_CUSTOM_SYMBOL", "TRADING_SYMBOL")
        c_lot = self._col("LOT_SIZE", "SEM_LOT_UNITS")
        c_opt = self._col("OPTION_TYPE", "SEM_OPTION_TYPE")
        c_strike = self._col("STRIKE_PRICE", "SEM_STRIKE_PRICE")

        df[c_exp] = pd.to_datetime(df[c_exp], errors="coerce").dt.normalize()
        df[c_strike] = pd.to_numeric(df[c_strike], errors="coerce")
        df[c_lot] = pd.to_numeric(df[c_lot], errors="coerce")

        sub = self._filter_nifty_optidx(df)
        sub = sub[(sub[c_exp] == expiry)].copy()

        # exact match first
        exact = sub[sub[c_strike].round(2) == float(atm)].copy()

        # fallback: some masters store strike scaled differently
        if exact.empty:
            scaled_candidates = [float(atm), float(atm) * 100, float(atm) * 100.0]
            for val in scaled_candidates:
                exact = sub[sub[c_strike].round(2) == val].copy()
                if not exact.empty:
                    break

        if exact.empty:
            # final fallback: nearest strike
            sub["_strike_diff"] = (sub[c_strike] - float(atm)).abs()
            if sub["_strike_diff"].notna().any():
                nearest_val = sub.sort_values("_strike_diff").iloc[0][c_strike]
                exact = sub[sub[c_strike] == nearest_val].copy()

        if exact.empty:
            raise ValueError(f"No NIFTY ATM options found for strike {atm}")

        def pick(opt_type: str) -> ResolvedOption:
            s = exact[exact[c_opt].astype(str).str.upper() == opt_type].copy()
            if s.empty:
                raise ValueError(f"Missing {opt_type} for strike {atm}")
            row = s.iloc[0]
            return ResolvedOption(
                symbol=str(row[c_disp]),
                security_id=str(row[c_sid]).split(".")[0],
                lot_size=int(float(row[c_lot])),
            )

        ce = pick("CE")
        pe = pick("PE")
        lot = ce.lot_size or pe.lot_size

        self.log.info(
            "ATM=%s | CE=%s (%s) | PE=%s (%s) | Lot=%s",
            atm, ce.symbol, ce.security_id, pe.symbol, pe.security_id, lot
        )
        return ce, pe, lot