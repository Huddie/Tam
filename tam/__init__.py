"""tam -- top-level convenience API.

Examples::

    import tam

    # Get a registered chart by base type + name:
    c = tam.get(TearsheetChart, "cumulative_returns")

    # Or instantiate directly and call with data:
    from tam.backtest.tearsheet import DrawdownChart
    c = DrawdownChart()
    c(my_series).show()

    # Chain charts into one composite Plotly figure:
    c1(series) | c2(series) | c3(series)

    # Plot raw series (price + indicator overlays, a FRED series, ...) --
    # see tam.charting.timeseries for the full pattern:
    from tam.charting import timeseries
    timeseries([close, sma_20]) | timeseries(rsi_14, title="RSI")

    # Resolve a third-party secret (env var, or a Colab secret) without
    # hardcoding it in a notebook cell -- see tam.secrets for the full
    # resolution order:
    from fredapi import Fred as _FredApi
    fred = _FredApi(api_key=tam.Secrets["FRED_API_KEY"])

    # Or skip fredapi entirely -- tam.Fred wraps it, resolving the API key
    # via tam.Secrets internally:
    dgs10 = tam.Fred.get(tam.Fred.Datasets.TREASURY_10Y)

    # One object per ticker (or several), backed by the market-data/reference-
    # data/SEC lakes at once -- see tam.symbol's own docstring:
    from tam import Symbol, CIK
    aapl = Symbol("AAPL")
    aapl.minute_bars(start="2024-01-01")
    aapl.splits()
    Symbol(CIK(320193)).splits()   # same AAPL, identified by its SEC CIK instead

    # Raw SQL, no ticker object needed -- the lower-level tier Symbol itself
    # is built on:
    tam.query("SELECT * FROM daily_bars('AAPL') ORDER BY day")

    # Optional caching (opt-in everywhere -- omit `cache=` to always hit the
    # connection, exactly like before this existed) -- construct once, reuse
    # across notebook cells so a re-run doesn't re-fetch:
    cache = tam.ManualCache()
    Symbol("AAPL", cache=cache).minute_bars()

    # engine=/Engine -- pandas (the default) or polars, discoverable instead
    # of guessing the right string to pass:
    Symbol("AAPL").splits(engine=tam.Engine.POLARS)

    # columns= -- a subset instead of every column:
    Symbol("AAPL").splits(columns=["ticker", "execution_date"])
"""

from __future__ import annotations

from .cache import Cache, LRUCache, ManualCache, TTLCache
from .engine import Engine
from .query import query
from .registry import Registry
from .research.data.fred import Fred
from .secrets import Secrets
from .symbol import CIK, Symbol

__all__ = [
    "CIK",
    "Cache",
    "Engine",
    "Fred",
    "LRUCache",
    "ManualCache",
    "Registry",
    "Secrets",
    "Symbol",
    "TTLCache",
    "get",
    "query",
]


def get(base_type, name: str | None = None):
    """Convenience wrapper around Registry.get / plain instantiation.

    Two call styles::

        tam.get(TearsheetChart, "cumulative_returns")
            -> Registry.get(TearsheetChart, "cumulative_returns")
               (cached singleton of the registered class)

        tam.get(SomeChartClass)
            -> SomeChartClass()
               (fresh instance via no-arg constructor)
    """
    if name is not None:
        return Registry.get(base_type, name)
    # base_type is a concrete class -- instantiate it directly.
    return base_type()
