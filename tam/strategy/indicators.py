"""Thin pandas-friendly wrappers around tulipy indicator functions.

Isolates the third-party indicator library behind a Series-in/Series-out contract,
so strategies depend on this module's shape, not tulipy's raw-array API directly.
"""
from __future__ import annotations

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
