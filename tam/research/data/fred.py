"""Fred: a thin wrapper around fredapi.Fred -- resolves the API key via
tam.Secrets["FRED_API_KEY"] internally (nothing to construct/pass yourself),
adds a small human-readable-name lookup on top of fredapi's raw
get_series(), and a Datasets enum of commonly-used series ids so you don't
need to look up "DGS10" from memory/docs every time.

    from tam import Fred

    dgs10 = Fred.get(Fred.Datasets.TREASURY_10Y)   # or Fred.get("DGS10") -- same thing
    dgs10.name                                      # "10-Year Treasury Yield", not "DGS10"

The underlying fredapi.Fred client (and therefore the FRED_API_KEY lookup)
is built LAZILY on first use, not at import time -- `from tam import Fred`
or referencing `Fred.Datasets` never requires an API key to be configured;
only actually calling `.get(...)` does.

Lives under tam.research.data (not tam.data/tam.marketdata, which are
specifically equity/security price data) -- FRED is macro/economic data,
a different category, with room for e.g. an eventual tam.research.data.sec
alongside it.
"""
from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Optional, Union

import pandas as pd

from ...secrets import Secrets


class _Datasets(str, Enum):
    """A few commonly-used FRED series ids -- not exhaustive (FRED has
    tens of thousands of series); pass any other id straight to Fred.get()
    as a plain string, this enum is just a memory aid for the common ones."""

    TREASURY_3MO = "DGS3MO"
    TREASURY_2Y = "DGS2"
    TREASURY_10Y = "DGS10"
    TREASURY_30Y = "DGS30"
    FED_FUNDS_RATE = "FEDFUNDS"
    FED_FUNDS_EFFECTIVE = "DFF"
    SOFR = "SOFR"
    CPI = "CPIAUCSL"
    UNEMPLOYMENT_RATE = "UNRATE"
    YIELD_CURVE_10Y_2Y = "T10Y2Y"


_NAMES = {
    _Datasets.TREASURY_3MO: "3-Month Treasury Yield",
    _Datasets.TREASURY_2Y: "2-Year Treasury Yield",
    _Datasets.TREASURY_10Y: "10-Year Treasury Yield",
    _Datasets.TREASURY_30Y: "30-Year Treasury Yield",
    _Datasets.FED_FUNDS_RATE: "Effective Federal Funds Rate",
    _Datasets.FED_FUNDS_EFFECTIVE: "Federal Funds Effective Rate (Daily)",
    _Datasets.SOFR: "Secured Overnight Financing Rate",
    _Datasets.CPI: "Consumer Price Index",
    _Datasets.UNEMPLOYMENT_RATE: "Unemployment Rate",
    _Datasets.YIELD_CURVE_10Y_2Y: "10Y-2Y Treasury Spread",
}


class _Fred:
    """Singleton accessor -- see module docstring."""

    Datasets = _Datasets

    def __init__(self) -> None:
        self._client = None  # lazy fredapi.Fred, built on first get()

    def _resolve_client(self):
        if self._client is None:
            from fredapi import Fred as _FredApi

            self._client = _FredApi(api_key=Secrets["FRED_API_KEY"])
        return self._client

    def name_for(self, series_id: Union[str, _Datasets]) -> str:
        """The human-readable label for `series_id` if it's a known
        Fred.Datasets member, else the raw id itself unchanged."""
        return _NAMES.get(series_id, series_id.value if isinstance(series_id, _Datasets) else series_id)

    def get(
        self,
        series_id: Union[str, _Datasets],
        *,
        start: Optional[Union[str, date]] = None,
        end: Optional[Union[str, date]] = None,
    ) -> pd.Series:
        """Fetches `series_id` (a raw FRED id like "DGS10", or a
        Fred.Datasets member) as a pandas Series indexed by date, named
        with its human-readable label when known (so it flows straight
        into tam.backtest.tearsheet.timeseries()/plots with a readable
        legend entry instead of a raw FRED code). `start`/`end` are passed
        straight through to fredapi (omit either for "no bound" -- FRED's
        own default is the series' full available history)."""
        raw_id = series_id.value if isinstance(series_id, _Datasets) else series_id
        client = self._resolve_client()
        series = client.get_series(raw_id, observation_start=start, observation_end=end)
        series.name = self.name_for(series_id)
        series.index.name = "date"
        return series


Fred = _Fred()
