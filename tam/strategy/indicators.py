"""Thin pandas-friendly wrappers around tulipy indicator functions.

Isolates the third-party indicator library behind a Series-in/Series-out contract,
so strategies depend on this module's shape, not tulipy's raw-array API directly.
"""
from __future__ import annotations

from typing import Tuple

import pandas as pd
import tulipy as ti


def sma(values: pd.Series, period: int) -> pd.Series:
    """Simple moving average, re-indexed onto the tail of `values`'s own index."""
    raw = values.to_numpy(dtype="float64")
    result = ti.sma(raw, period)
    return pd.Series(result, index=values.index[-len(result):], name=f"sma_{period}")


def rsi(values: pd.Series, period: int) -> pd.Series:
    """Relative Strength Index, re-indexed onto the tail of `values`'s own index."""
    raw = values.to_numpy(dtype="float64")
    result = ti.rsi(raw, period)
    return pd.Series(result, index=values.index[-len(result):], name=f"rsi_{period}")


def macd_histogram(values: pd.Series, short_period: int, long_period: int, signal_period: int) -> pd.Series:
    """MACD histogram (the MACD line minus its own signal line), re-indexed onto
    the tail of `values`'s own index."""
    raw = values.to_numpy(dtype="float64")
    _, _, histogram = ti.macd(raw, short_period, long_period, signal_period)
    return pd.Series(histogram, index=values.index[-len(histogram):], name="macd_histogram")


def bbands(values: pd.Series, period: int, stddev: float) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """Bollinger Bands (lower, middle, upper), each re-indexed onto the tail of
    `values`'s own index."""
    raw = values.to_numpy(dtype="float64")
    lower, middle, upper = ti.bbands(raw, period, stddev)
    index = values.index[-len(lower):]
    return (
        pd.Series(lower, index=index, name=f"bbands_lower_{period}"),
        pd.Series(middle, index=index, name=f"bbands_middle_{period}"),
        pd.Series(upper, index=index, name=f"bbands_upper_{period}"),
    )
