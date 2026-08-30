"""Point-in-time universe membership -- WHICH tickers existed/qualified as of
a given date, not just today's list. Backtesting today's S&P 500 back to
2005 introduces severe survivorship bias (delisted/removed constituents never
show up); a UniverseProvider is how tam.strategy.basket_overnight (and any
other cross-sectional strategy) avoids that.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable
from datetime import date
from pathlib import Path

import pandas as pd

from ..registry import Registry


class UniverseProvider(ABC):
    @abstractmethod
    def constituents(self, as_of: date) -> list[str]:
        """The tickers that qualified as of `as_of` -- only ever backward-looking
        (a caller resolving this for day T must not see additions/removals that
        happen after T, or selection built on it leaks the future)."""


@Registry.register(UniverseProvider, "static")
class StaticUniverse(UniverseProvider):
    """One fixed ticker list, `as_of` ignored -- today's config-driven backtest
    behavior (`backtest.tickers`), kept as the default so nothing existing
    needs a UniverseProvider to keep working."""

    def __init__(self, tickers: list[str]):
        self._tickers = list(tickers)

    def constituents(self, as_of: date) -> list[str]:
        return list(self._tickers)


class _EventsUniverse(UniverseProvider):
    """Shared replay logic for any {date, ticker, action} event log,
    however it was obtained -- a file (CsvUniverse) or a live fetch
    (WikipediaUniverse) both just need to set self._events (sorted by
    date) in their own __init__ and get constituents(as_of) for free."""

    _events: pd.DataFrame

    def constituents(self, as_of: date) -> list[str]:
        events = self._events[self._events["date"] <= pd.Timestamp(as_of)]
        members = set()
        for action, ticker in zip(events["action"], events["ticker"]):
            if action == "add":
                members.add(ticker)
            else:
                members.discard(ticker)
        return sorted(members)


@Registry.register(UniverseProvider, "csv")
class CsvUniverse(_EventsUniverse):
    """Reads a user-supplied point-in-time membership file: columns `date`,
    `ticker`, `action` ("add"/"remove"), one row per membership change --
    e.g. exported from a vendor's historical-constituents endpoint (Sharadar,
    FMP's `historical-sp500-constituent`, ...), `fetch_sp500_membership()`
    below, or hand-maintained. Not tied to any specific vendor -- register
    your own UniverseProvider for a live API instead of a static file if you
    have one (see WikipediaUniverse/PitIndexUniverse below for two)."""

    def __init__(self, path: str | Path):
        df = pd.read_csv(path, parse_dates=["date"])
        unknown = set(df["action"].unique()) - {"add", "remove"}
        if unknown:
            raise ValueError(f"unknown action(s) {sorted(unknown)} in {path} -- expected 'add'/'remove'")
        self._events = df.sort_values("date")


def build_membership_events(
    current_members: Iterable[str],
    changes: pd.DataFrame,
    fallback_date: date | None = None,
) -> pd.DataFrame:
    """Turn (today's current members, a change log of `date`/`added_ticker`/
    `removed_ticker` rows -- either ticker column may be null per row) into
    the {date, ticker, action} shape CsvUniverse reads. Generic -- not tied
    to Wikipedia or the S&P 500 specifically; this is the reusable part for
    ANY index's change log with this shape (`fetch_sp500_from_wikipedia()`
    below is the only S&P-500-specific piece, and it's just a fetch).

    A member in `current_members` that never appears as an `added_ticker`
    (added before the change log's own earliest record) gets an "add" event
    dated `fallback_date` (defaults to one day before the change log's
    earliest date) -- an acknowledged approximation for "we don't know this
    member's true add date, only that it predates our change log," good
    enough for `constituents(as_of)` as long as `as_of` isn't earlier than
    that fallback."""
    changes = changes.assign(date=pd.to_datetime(changes["date"]))

    events = []
    for _, row in changes.iterrows():
        if pd.notna(row.get("added_ticker")):
            events.append({"date": row["date"], "ticker": row["added_ticker"], "action": "add"})
        if pd.notna(row.get("removed_ticker")):
            events.append({"date": row["date"], "ticker": row["removed_ticker"], "action": "remove"})

    ever_added = {e["ticker"] for e in events}
    if fallback_date is None:
        fallback_date = changes["date"].min() - pd.Timedelta(days=1) if len(changes) else pd.Timestamp.today()
    else:
        fallback_date = pd.Timestamp(fallback_date)
    for ticker in current_members:
        if ticker not in ever_added:
            events.append({"date": fallback_date, "ticker": ticker, "action": "add"})

    return pd.DataFrame(events).sort_values("date").reset_index(drop=True)


def _label(column) -> str:
    parts = column if isinstance(column, tuple) else (column,)
    return " ".join(str(part) for part in parts).lower()


def _find_column(columns, required: tuple[str, ...]):
    for column in columns:
        label = _label(column)
        if all(keyword in label for keyword in required):
            return column
    raise ValueError(f"no column matching {required} among {list(columns)}")


def _find_column_any(columns, required_options):
    errors = []
    for required in required_options:
        try:
            return _find_column(columns, required)
        except ValueError as exc:
            errors.append(str(exc))
    raise ValueError(f"no column matched any of {required_options}: {errors}")


def fetch_sp500_from_wikipedia() -> tuple[list[str], pd.DataFrame]:
    """Fetch the S&P 500's current constituents and historical additions/
    removals from Wikipedia's community-maintained "List of S&P 500
    companies" page -- free, no API key, but not an official data feed;
    its table structure has shifted before and could again, which is why
    this looks columns up by keyword rather than hardcoding exact names or
    positions, and raises a clear error (listing the actual columns found)
    instead of silently misparsing if it can't find what it expects. For a
    backtest you actually trust arbitrarily far back in time, a paid
    vendor's official point-in-time feed (Sharadar, FMP's
    historical-sp500-constituent, ...) is more trustworthy than scraping a
    wiki page -- this is the free/quick option, not the most rigorous one.

    Returns `(current_tickers, changes)` -- `changes` has columns `date`,
    `added_ticker`, `removed_ticker` (either may be null per row: some
    entries are a straight swap, some are just an addition or a removal).
    Feed both into `build_membership_events(...)` to get a CsvUniverse-ready
    table, or just use `current_tickers` directly for "what's in the S&P 500
    right now" with no point-in-time history at all.

    Needs network access.
    """
    import requests

    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    # pd.read_html(url) hands the request to urllib with no User-Agent header
    # at all -- Wikipedia's servers 403 that. Fetching it ourselves with a
    # normal-looking User-Agent (via `requests`, already a dependency) and
    # handing read_html the HTML text instead of the URL works around it.
    response = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (compatible; tam-quant)"}, timeout=30)
    response.raise_for_status()
    tables = pd.read_html(response.text)
    current_table, changes_table = tables[0], tables[1]

    ticker_column = _find_column_any(current_table.columns, [("symbol",), ("ticker",)])
    current_tickers = current_table[ticker_column].tolist()

    date_column = _find_column(changes_table.columns, ("date",))
    added_column = _find_column_any(changes_table.columns, [("added", "ticker"), ("added", "symbol")])
    removed_column = _find_column_any(changes_table.columns, [("removed", "ticker"), ("removed", "symbol")])

    changes = pd.DataFrame(
        {
            "date": pd.to_datetime(changes_table[date_column], errors="coerce"),
            "added_ticker": changes_table[added_column],
            "removed_ticker": changes_table[removed_column],
        }
    ).dropna(subset=["date"])
    return current_tickers, changes


def fetch_sp500_membership() -> pd.DataFrame:
    """fetch_sp500_from_wikipedia() + build_membership_events(...) in one
    call -- the {date, ticker, action} table ready to write out and load:

        fetch_sp500_membership().to_csv("sp500_membership.csv", index=False)
        universe = CsvUniverse("sp500_membership.csv")

    Needs network access."""
    current_tickers, changes = fetch_sp500_from_wikipedia()
    return build_membership_events(current_tickers, changes)


@Registry.register(UniverseProvider, "wikipedia")
class WikipediaUniverse(_EventsUniverse):
    """fetch_sp500_membership() wrapped as a UniverseProvider -- fetches once
    at construction (needs network then; constituents(as_of) itself doesn't
    hit the network again). Prefer "pitindex" below unless you specifically
    want Wikipedia's own page: it ships a bundled offline dataset (no
    network needed at all after installing it) and covers sp400/sp600/sp1500
    too, not just the S&P 500."""

    def __init__(self):
        self._events = fetch_sp500_membership().sort_values("date")


@Registry.register(UniverseProvider, "pitindex")
class PitIndexUniverse(UniverseProvider):
    """Point-in-time membership via the `pitindex` package -- a bundled,
    offline dataset (no network at call time at all; `pitindex.update()`
    pulls a fresh weekly build if you have its `[build]` extra). Covers
    "sp500" (default), "sp400", "sp600", or the composite "sp1500". Needs
    the `pitindex` extra (`pip install "tam-quant[pitindex]"` -- Python
    >=3.11 only; see pyproject.toml)."""

    def __init__(self, index: str = "sp500"):
        self._index = index

    def constituents(self, as_of: date) -> list[str]:
        try:
            import pitindex
        except ImportError as exc:
            raise ImportError(
                "PitIndexUniverse needs the `pitindex` extra (Python >=3.11): "
                'run `uv sync --extra pitindex` or `pip install "tam-quant[pitindex]"`.'
            ) from exc
        return sorted(pitindex.get_constituents(as_of, index=self._index)["ticker"].tolist())
