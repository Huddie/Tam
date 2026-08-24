"""The date x ticker cross-sectional return matrix -- the foundational
artifact every other tam.basket module builds on. Turns "one ticker's price
history" into "every ticker's return on every date," which is what makes
cross-sectional scoring/clustering/weighting possible at all.
"""
from __future__ import annotations

from datetime import date
from typing import Iterable

import pandas as pd

from ..data.repository import DataRepository
from ..data.schema import CLOSE, OPEN


def overnight_return_matrix(repository: DataRepository, tickers: Iterable[str], start: date, end: date) -> pd.DataFrame:
    """date-indexed, one column per ticker: Open[t+1]/Close[t] - 1 -- the
    "buy at today's close, sell at tomorrow's open" (BCSO) return, indexed by
    the entry (close) date. The last row is always NaN (no next day's open
    yet in range) -- left in place rather than dropped, since which row that
    is differs per ticker if their histories don't all end on the same date.
    Ingests each ticker first (via DataRepository.ingest -- only fetches
    what's missing), so this is safe to call without ingesting yourself first."""
    columns = {}
    for ticker in tickers:
        repository.ingest([ticker], start, end)
        df = repository.query(ticker, start, end)
        if df.empty:
            continue
        columns[ticker] = df[OPEN].shift(-1) / df[CLOSE] - 1
    return pd.DataFrame(columns)


def intraday_return_matrix(repository: DataRepository, tickers: Iterable[str], start: date, end: date) -> pd.DataFrame:
    """date-indexed, one column per ticker: Close[t]/Open[t] - 1 -- the
    "buy at today's open, sell at today's close" return, for comparison
    against overnight_return_matrix() (same tickers/dates, opposite session)."""
    columns = {}
    for ticker in tickers:
        repository.ingest([ticker], start, end)
        df = repository.query(ticker, start, end)
        if df.empty:
            continue
        columns[ticker] = df[CLOSE] / df[OPEN] - 1
    return pd.DataFrame(columns)
