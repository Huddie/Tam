"""Self-service, one-ticker-at-a-time 1-minute bar access -- the read side
for a Factor (or any notebook code) that needs minute-level data, e.g. a
realized-vol-in-the-last-30-minutes-before-close feature, distinct from
tam.data's daily EOD grain (tam/marketdata/eod_provider.py).

NOT the same thing as tam.marketdata.providers.MinuteBarProvider: that one
is the bulk-admin/backfill side (one whole market's day at a time, vendor
S3 flat-file credentials, used by the ingestion pipeline that first
populates R2) -- wrong shape and wrong credentials for a notebook. This is
the self-service *read* side, over the same TAM_PAT token
tam.marketdata.eod_provider/tam.Symbol already use, registered so a second
source (e.g. yfinance's own recent-history minute bars) can slot in later
with no code change elsewhere.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

import pandas as pd

from ..engine import Engine
from ..registry import Registry
from .connection import thread_local_connection


class MinuteBarSource(ABC):
    """One ticker's 1-minute OHLCV bars over [start, end]."""

    @abstractmethod
    def fetch_minute_bars(
        self, symbol: str, start: date, end: date, *, engine: str = Engine.PANDAS
    ) -> pd.DataFrame: ...


@Registry.register(MinuteBarSource, "marketdata")
class MarketDataMinuteBarSource(MinuteBarSource):
    """Wraps `Symbol(...).minute_bars()`, same `thread_local_connection()`
    fix `MarketDataEodProvider` uses and for the identical reason -- a
    caller that fans this out across a thread pool must never share one
    DuckDB connection across threads."""

    def fetch_minute_bars(self, symbol: str, start: date, end: date, *, engine: str = Engine.PANDAS) -> pd.DataFrame:
        from ..symbol import Symbol

        return Symbol(symbol, con=thread_local_connection()).minute_bars(start=start, end=end, engine=engine)
