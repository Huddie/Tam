"""The date x ticker cross-sectional price matrix -- the actual foundational
primitive every other tam.basket module builds on. The hard, tedious part is
getting FROM per-symbol DataRepository storage TO one aligned cross-sectional
DataFrame (looping tickers, ingesting what's missing, aligning onto one date
index); which RETURN DEFINITION you then compute from it (overnight,
intraday, close-to-close, weekly, N-day, whatever) is one line of plain
pandas on top -- deliberately not a named function per use case, so you're
never locked into this module's idea of which return matters to you:

    opens = price_matrix(repository, tickers, start, end, column=OPEN)
    closes = price_matrix(repository, tickers, start, end, column=CLOSE)

    overnight_returns = opens.shift(-1) / closes - 1   # buy close, sell next open
    intraday_returns = closes / opens - 1              # buy open, sell same close
    close_to_close = closes.pct_change()               # the classic daily return
    weekly_returns = closes.resample("W").last().pct_change()
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date

import pandas as pd

from ..data.repository import DataRepository
from ..data.schema import CLOSE


def price_matrix(
    repository: DataRepository, tickers: Iterable[str], start: date, end: date, column: str = CLOSE
) -> pd.DataFrame:
    """date-indexed, one column per ticker, values from `column` (one of
    tam.data.schema's OPEN/HIGH/LOW/CLOSE/ADJ_CLOSE/VOLUME). Ingests each
    ticker first (via DataRepository.ingest -- only fetches what's missing),
    so this is safe to call without ingesting yourself first. A ticker with
    no data in range is silently omitted, not filled with NaN/zero -- its
    column just doesn't exist in the result."""
    columns = {}
    for ticker in tickers:
        repository.ingest([ticker], start, end)
        df = repository.query(ticker, start, end)
        if df.empty:
            continue
        columns[ticker] = df[column]
    return pd.DataFrame(columns)
