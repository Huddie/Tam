"""tam -- top-level convenience API.

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
    # see tam.backtest.tearsheet.timeseries for the full pattern:
    from tam.backtest.tearsheet import timeseries
    timeseries([close, sma_20]) | timeseries(rsi_14, title="RSI")

    # Resolve a third-party secret (env var, or a Colab secret) without
    # hardcoding it in a notebook cell -- see tam.secrets for the full
    # resolution order:
    from fredapi import Fred as _FredApi
    fred = _FredApi(api_key=tam.Secrets["FRED_API_KEY"])

    # Or skip fredapi entirely -- tam.Fred wraps it, resolving the API key
    # via tam.Secrets internally:
    dgs10 = tam.Fred.get(tam.Fred.Datasets.TREASURY_10Y)
"""
from __future__ import annotations

from .fred import Fred
from .registry import Registry
from .secrets import Secrets

__all__ = ["Fred", "Registry", "Secrets", "get"]


def get(base_type, name: str | None = None):
    """Convenience wrapper around Registry.get / plain instantiation.

    Two call styles:

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
