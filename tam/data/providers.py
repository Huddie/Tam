"""Data providers: fetch raw end-of-day OHLCV history for a symbol from an external source."""
from __future__ import annotations

import logging
import os
import threading
from abc import ABC, abstractmethod
from datetime import date, timedelta

import pandas as pd
import requests

from ..registry import Registry
from .schema import ADJ_CLOSE, CLOSE, DATE, HIGH, LOW, OHLCV_COLUMNS, OPEN, VOLUME, empty_ohlcv_frame

# yfinance is well documented to NOT be safe to call concurrently from
# multiple threads for different tickers -- it keeps shared, ticker-keyed
# global state internally (yfinance.shared._DFS, used by its own "threads="
# multi-ticker batching) that a caller doing its OWN external concurrency
# (DataRepository.ingest()'s thread pool, fetching several DIFFERENT symbols
# at once) can race on, observed in practice as one symbol's fetch silently
# coming back with a DIFFERENT symbol's data -- no exception, no warning,
# just wrong numbers written straight into that symbol's cache. Serializing
# every yf.download() call process-wide behind one lock costs nothing when
# ingest() is only fetching yfinance data sequentially anyway, and keeps the
# thread pool free to still parallelize disk I/O / other providers.
_YFINANCE_LOCK = threading.Lock()

# yfinance logs its OWN "possibly delisted; no price data found" ERROR
# (via Python's logging module, independent of anything we raise) for the
# exact same "no data for this range" case DataRepository.ingest() already
# surfaces as one clear UserWarning per symbol -- e.g. requesting today's
# bar before Yahoo has posted it fails this way for every ticker, every
# call, and the wall of ERROR:yfinance: lines drowns out our own, more
# useful warning. Quieted here rather than left to every caller to
# rediscover and silence themselves.
logging.getLogger("yfinance").setLevel(logging.CRITICAL)


class DataProvider(ABC):
    """Fetches OHLCV history for one symbol, indexed by date ascending."""

    @abstractmethod
    def fetch_eod(self, symbol: str, start: date, end: date) -> pd.DataFrame: ...


@Registry.register(DataProvider, "fmp")
class FMPProvider(DataProvider):
    """Financial Modeling Prep end-of-day history. Requires FMP_API_KEY (or api_key=).

    Works against both the current /stable endpoint (flat JSON array, no adjClose)
    and the legacy /v3 endpoint (dict with a "historical" list, has adjClose).
    """

    BASE_URL = "https://financialmodelingprep.com/stable/historical-price-eod/full"

    def __init__(self, api_key: str | None = None, session: requests.Session | None = None):
        self._api_key = api_key or os.environ.get("FMP_API_KEY")
        if not self._api_key:
            raise ValueError("FMP API key required: set FMP_API_KEY env var or pass api_key=")
        self._session = session or requests.Session()

    def fetch_eod(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        response = self._session.get(
            self.BASE_URL,
            params={
                "symbol": symbol,
                "apikey": self._api_key,
                "from": start.isoformat(),
                "to": end.isoformat(),
            },
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        rows = payload.get("historical", []) if isinstance(payload, dict) else payload
        if not rows:
            return empty_ohlcv_frame()

        df = pd.DataFrame(rows).rename(columns={"adjClose": ADJ_CLOSE})
        if ADJ_CLOSE not in df.columns:
            df[ADJ_CLOSE] = df[CLOSE]
        df[DATE] = pd.to_datetime(df["date"])
        df = df.set_index(DATE).sort_index()
        return df[OHLCV_COLUMNS]


@Registry.register(DataProvider, "yfinance")
class YFinanceProvider(DataProvider):
    """Yahoo Finance end-of-day history. No API key required.

    `adjust=False` (default, unchanged behavior): raw OHLC exactly as
    printed, `adj_close` as a separate dividend/split-adjusted-close column
    -- correct for anything that trades at the raw print (matches a real
    broker fill) but WRONG for a calculation that spans a split/dividend
    boundary using open/high/low/close directly -- e.g. a buy-close/
    sell-open overnight return: a split between those two prints shows up as
    a fake gap that has nothing to do with the real overnight return.

    `adjust=True`: every OHLC column comes back split/dividend-adjusted
    (yfinance's own `auto_adjust=True`) -- `adj_close` is then just `close`
    again (there's nothing further to adjust). Use `Registry.get(DataProvider,
    "yfinance_adjusted")` for a zero-arg-constructible, config-referenceable
    version of this, or `Registry.create(DataProvider, "yfinance", adjust=True)`
    directly.

    IMPORTANT: DataStore/DataRepository cache by (symbol, store root) only --
    NOT by which provider or `adjust` value produced the data. Point `adjust=True`
    at a different `store` root than any existing `adjust=False` cache for the
    same symbols (e.g. "data/eod_adjusted" instead of "data/eod"), or
    DataRepository.ingest() will treat already-cached raw bars as if they
    already covered the requested range and silently serve stale, wrongly-
    adjusted data instead of re-fetching.
    """

    def __init__(self, adjust: bool = False):
        self._adjust = adjust

    def fetch_eod(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        import yfinance as yf

        # yfinance's own ticker convention uses a hyphen for share classes
        # (e.g. "BRK-B"), not the dot notation most index/vendor data uses
        # (Wikipedia's S&P 500 table, pitindex, ... all say "BRK.B"/"BF.B").
        # Translate only for the actual API call -- everything else (the
        # repository's storage key, the universe's own ticker list, a
        # price_matrix column) keeps using whatever string the caller passed
        # in, so results still come back under that same symbol.
        yf_symbol = symbol.replace(".", "-")

        # yf.download's `end` is EXCLUSIVE (a well-known yfinance quirk), but this
        # provider's contract (like FMPProvider's) treats `end` as inclusive -- pad
        # by one day and then defensively re-clip, rather than trust the exact
        # off-by-one behavior of whatever yfinance version is installed.
        with _YFINANCE_LOCK:
            df = yf.download(yf_symbol, start=start, end=end + timedelta(days=1), progress=False, auto_adjust=self._adjust)
        if df.empty:
            return empty_ohlcv_frame()

        if isinstance(df.columns, pd.MultiIndex):
            # Newer yfinance returns (field, ticker) columns even for a single symbol.
            df.columns = df.columns.get_level_values(0)

        df = df.rename(
            columns={
                "Open": OPEN,
                "High": HIGH,
                "Low": LOW,
                "Close": CLOSE,
                "Adj Close": ADJ_CLOSE,
                "Volume": VOLUME,
            }
        )
        # Some yfinance versions/auto_adjust combinations return a duplicate-
        # named column after the rename above (observed: two "close" columns
        # with adjust=True) -- `df[CLOSE]` would then be a DataFrame, not a
        # Series, and `df[ADJ_CLOSE] = df[CLOSE]` below raises a confusing
        # pandas ValueError. Keep the first occurrence of any duplicate name
        # rather than try to predict every yfinance version's exact quirk.
        df = df.loc[:, ~df.columns.duplicated()]
        if ADJ_CLOSE not in df.columns:
            # auto_adjust=True: yfinance doesn't return a separate "Adj Close"
            # column at all -- "close" IS already the adjusted close.
            df[ADJ_CLOSE] = df[CLOSE]
        df.index.name = DATE
        df = df[df.index <= pd.Timestamp(end)]
        return df[OHLCV_COLUMNS]


@Registry.register(DataProvider, "yfinance_adjusted")
class YFinanceAdjustedProvider(YFinanceProvider):
    """YFinanceProvider(adjust=True) -- zero-arg-constructible so it's usable
    via Registry.get(...) and referenceable by name from config
    (`data.provider: yfinance_adjusted`), e.g. for BCSO or anything else that
    spans a split/dividend boundary using open/high/low/close directly. See
    YFinanceProvider's own docstring for the separate-cache-root caveat."""

    def __init__(self):
        super().__init__(adjust=True)
