"""A DataProvider backed by this repo's own self-service EOD lake
(tam.Symbol / tam.marketdata) instead of a third-party vendor -- the exact
data tam-data-explorer browses and eod_bars() queries
(tam/marketdata/duckdb_query.py's eod_bars(sym) macro reads straight from
tam.data's own end-of-day Parquet files, not a separate dataset),
authenticated with the lightweight, read-only TAM_PAT personal token
(see tam.marketdata.connection's own docstring) rather than admin R2 keys.

Registering this under Registry(DataProvider, "marketdata_eod") means any
config-driven backtest can select it exactly the way it already selects
"yfinance"/"fmp" (`data.provider: marketdata_eod`), and DataRepository.ingest()
still only fetches gaps not already covered by the local cache
(tam/data/repository.py's _missing_ranges()) -- calling this once for a
ticker/date range already backfilled costs nothing.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from ..data.providers import DataProvider
from ..data.schema import DATE, OHLCV_COLUMNS, empty_ohlcv_frame
from ..registry import Registry
from .connection import thread_local_connection


@Registry.register(DataProvider, "marketdata_eod")
class MarketDataEodProvider(DataProvider):
    """Wraps `Symbol(...).eod_bars()`. Uses `thread_local_connection()`
    (not the process-shared `default_connection()` `Symbol` falls back to
    by default) since `DataRepository.ingest()` fans `fetch_eod()` calls
    out across its own thread pool (`max_workers=8` by default,
    `tam/data/repository.py`) -- a single DuckDB connection object isn't
    safe to call from multiple threads at once (confirmed live: this
    crashed the whole process with no catchable exception before this fix)."""

    def fetch_eod(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        from ..symbol import Symbol

        df = Symbol(symbol, con=thread_local_connection()).eod_bars(start=start, end=end)
        if df.empty:
            return empty_ohlcv_frame()
        return df.set_index(DATE)[OHLCV_COLUMNS]
