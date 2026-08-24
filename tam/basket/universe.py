"""Point-in-time universe membership -- WHICH tickers existed/qualified as of
a given date, not just today's list. Backtesting today's S&P 500 back to
2005 introduces severe survivorship bias (delisted/removed constituents never
show up); a UniverseProvider is how tam.strategy.basket_overnight (and any
other cross-sectional strategy) avoids that.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
from pathlib import Path
from typing import List

import pandas as pd

from ..registry import Registry


class UniverseProvider(ABC):
    @abstractmethod
    def constituents(self, as_of: date) -> List[str]:
        """The tickers that qualified as of `as_of` -- only ever backward-looking
        (a caller resolving this for day T must not see additions/removals that
        happen after T, or selection built on it leaks the future)."""


@Registry.register(UniverseProvider, "static")
class StaticUniverse(UniverseProvider):
    """One fixed ticker list, `as_of` ignored -- today's config-driven backtest
    behavior (`backtest.tickers`), kept as the default so nothing existing
    needs a UniverseProvider to keep working."""

    def __init__(self, tickers: List[str]):
        self._tickers = list(tickers)

    def constituents(self, as_of: date) -> List[str]:
        return list(self._tickers)


@Registry.register(UniverseProvider, "csv")
class CsvUniverse(UniverseProvider):
    """Reads a user-supplied point-in-time membership file: columns `date`,
    `ticker`, `action` ("add"/"remove"), one row per membership change --
    e.g. exported from a vendor's historical-constituents endpoint (Sharadar,
    FMP's `historical-sp500-constituent`, ...) or hand-maintained. Not tied to
    any specific vendor -- register your own UniverseProvider for a live API
    instead of a static file if you have one."""

    def __init__(self, path: str | Path):
        df = pd.read_csv(path, parse_dates=["date"])
        unknown = set(df["action"].unique()) - {"add", "remove"}
        if unknown:
            raise ValueError(f"unknown action(s) {sorted(unknown)} in {path} -- expected 'add'/'remove'")
        self._events = df.sort_values("date")

    def constituents(self, as_of: date) -> List[str]:
        events = self._events[self._events["date"] <= pd.Timestamp(as_of)]
        members = set()
        for action, ticker in zip(events["action"], events["ticker"]):
            if action == "add":
                members.add(ticker)
            else:
                members.discard(ticker)
        return sorted(members)
