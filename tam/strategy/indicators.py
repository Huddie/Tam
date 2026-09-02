"""Thin pandas-friendly wrappers around TA-Lib indicator functions.

Isolates the third-party indicator library behind a Series-in/Series-out contract,
so strategies depend on this module's shape, not the underlying library's raw-array
API directly -- this is also why swapping libraries (as happened once already, from
`tulipy`) only ever touches this one file.

TA-Lib (PyPI package `TA-Lib`, imported as `talib`), not `tulipy`: `tulipy`'s only
prebuilt Windows wheel targets Python 3.7 (`cp37`), below this project's own floor
(Python >=3.10, see pyproject.toml) -- every Windows install on a supported Python
version was silently falling back to building from source, which needs the
Microsoft Visual C++ Build Tools (a real C compiler toolchain, not something
`pip install` can provide, and not something a locked-down corporate laptop
typically has). Confirmed live: this broke `pip install tam-quant` outright for a
Windows user. TA-Lib's own Python wrapper now ships prebuilt wheels bundling the
underlying C library for Windows/macOS/Linux across Python 3.9-3.13 (confirmed via
its PyPI file listing), so no separate compiler or C library install is needed on
any of those. It's also the long-standing industry-standard technical-analysis
library, not a newer/less-vetted alternative.

TA-Lib's functions return an array the SAME LENGTH as the input, with a leading run
of NaN for whatever warmup period that indicator needs (unlike tulipy, which
returned an already-shorter, NaN-free array) -- `.dropna()` below restores the
exact same "shorter Series, no NaN, indexed on the tail" contract every caller
already depends on, so nothing outside this module needed to change.
"""

from __future__ import annotations

import pandas as pd
import talib


def sma(values: pd.Series, period: int) -> pd.Series:
    """Simple moving average, re-indexed onto the tail of `values`'s own index."""
    raw = values.to_numpy(dtype="float64")
    result = talib.SMA(raw, timeperiod=period)
    return pd.Series(result, index=values.index, name=f"sma_{period}").dropna()


def rsi(values: pd.Series, period: int) -> pd.Series:
    """Relative Strength Index, re-indexed onto the tail of `values`'s own index."""
    raw = values.to_numpy(dtype="float64")
    result = talib.RSI(raw, timeperiod=period)
    return pd.Series(result, index=values.index, name=f"rsi_{period}").dropna()


def macd_histogram(values: pd.Series, short_period: int, long_period: int, signal_period: int) -> pd.Series:
    """MACD histogram (the MACD line minus its own signal line), re-indexed onto
    the tail of `values`'s own index."""
    raw = values.to_numpy(dtype="float64")
    _, _, histogram = talib.MACD(raw, fastperiod=short_period, slowperiod=long_period, signalperiod=signal_period)
    return pd.Series(histogram, index=values.index, name="macd_histogram").dropna()


def bbands(values: pd.Series, period: int, stddev: float) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Bollinger Bands (lower, middle, upper), each re-indexed onto the tail of
    `values`'s own index."""
    raw = values.to_numpy(dtype="float64")
    # talib.BBANDS returns (upper, middle, lower) -- the OPPOSITE order of this
    # function's own (lower, middle, upper) return, so the two are unpacked into
    # differently-ordered local names here rather than risking a silent upper/
    # lower swap at the return statement below.
    upper, middle, lower = talib.BBANDS(raw, timeperiod=period, nbdevup=stddev, nbdevdn=stddev, matype=0)
    return (
        pd.Series(lower, index=values.index, name=f"bbands_lower_{period}").dropna(),
        pd.Series(middle, index=values.index, name=f"bbands_middle_{period}").dropna(),
        pd.Series(upper, index=values.index, name=f"bbands_upper_{period}").dropna(),
    )
