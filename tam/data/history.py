"""Cached per-symbol OHLCV history with point-in-time accessors, so a symbol's
data is read from disk once per DataRepository/session rather than on every
price lookup or lookback window a strategy asks for.
"""

from __future__ import annotations

from datetime import date

import pandas as pd


class SymbolHistory:
    def __init__(self, frame: pd.DataFrame):
        self._frame = frame  # sorted ascending by DatetimeIndex (DataStore.read guarantees this)

    @property
    def frame(self) -> pd.DataFrame:
        return self._frame

    def price_at(self, as_of: date, column: str) -> float:
        """The most recent bar's `column` value on or before `as_of`."""
        idx = self._frame.index.searchsorted(pd.Timestamp(as_of), side="right") - 1
        if idx < 0:
            raise LookupError(f"no price data on or before {as_of}")
        return float(self._frame.iloc[idx][column])

    def window_ending(self, as_of: date, n: int) -> pd.DataFrame:
        """The last `n` bars on or before `as_of` (fewer if not enough history)."""
        idx = self._frame.index.searchsorted(pd.Timestamp(as_of), side="right")
        return self._frame.iloc[max(0, idx - n) : idx].copy()
