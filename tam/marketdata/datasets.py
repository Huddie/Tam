"""Declarative registry of every "one ticker-scoped DuckDB macro, filtered
by an optional date range" dataset -- the common shape roughly a dozen of
tam.Symbol's methods share. Adding a new dataset that fits this shape
(the macro takes just a ticker, optionally filtered/ordered by one date
column) means adding ONE entry here plus one short method on Symbol, not
rewriting Symbol's own query-building logic. Datasets that DON'T fit this
shape (rollup_bars/rolling_volatility take an extra int argument; SEC
financials/filings need CIK resolution and their own dedupe logic) get
their own hand-written methods on Symbol instead -- forcing every dataset
through one identical shape would cost more clarity than the uniformity
would buy back.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from . import reference_schema as ref_schema
from . import schema as bar_schema
from ..data.schema import DATE, OHLCV_COLUMNS


@dataclass(frozen=True)
class DatasetSpec:
    macro: str  # the DuckDB macro name (almost always == the Symbol method name)
    date_column: Optional[
        str
    ]  # filtered by start=/end=, and the default ORDER BY -- None if this dataset has no meaningful range filter
    columns: List[str]  # for the graceful-empty-frame fallback when the underlying glob matches nothing yet
    supports_scan_all: bool = (
        True  # can this macro be called with NO ticker (sym := NULL) to scan every ticker at once?
    )
    ticker_column: str = (
        "ticker"  # only relevant when supports_scan_all -- the column a multi-ticker WHERE ... IN (...) filters on
    )


DATASETS = {
    # minute/eod/rollup macros REQUIRE a real ticker (sym has no default,
    # since each is a per-symbol file glob -- there's no "every symbol at
    # once" view to scan) -- supports_scan_all=False means Symbol falls
    # back to one query per ticker + a concat, never a single scan-all +
    # filter (that query would simply fail: `minute_bars(NULL)` errors,
    # it doesn't mean "everything").
    "minute_bars": DatasetSpec(
        "minute_bars", "ts", [bar_schema.TS, *bar_schema.MINUTE_BAR_COLUMNS], supports_scan_all=False
    ),
    "daily_bars": DatasetSpec(
        "daily_bars", "day", ["symbol", "day", "open", "high", "low", "close", "volume"], supports_scan_all=False
    ),
    "weekly_bars": DatasetSpec(
        "weekly_bars", "week", ["symbol", "week", "open", "high", "low", "close", "volume"], supports_scan_all=False
    ),
    "monthly_bars": DatasetSpec(
        "monthly_bars", "month", ["symbol", "month", "open", "high", "low", "close", "volume"], supports_scan_all=False
    ),
    "eod_bars": DatasetSpec("eod_bars", "date", [DATE, *OHLCV_COLUMNS], supports_scan_all=False),
    # The six reference-data macros all accept `sym := NULL` (scan every
    # ticker) -- a multi-ticker Symbol can do ONE query (scan-all +
    # `WHERE ticker IN (...)`) instead of one call per ticker.
    "splits": DatasetSpec("splits", ref_schema.SPLIT_EXECUTION_DATE, ref_schema.SPLIT_COLUMNS),
    "dividends": DatasetSpec("dividends", ref_schema.DIVIDEND_EX_DIVIDEND_DATE, ref_schema.DIVIDEND_COLUMNS),
    "ipo": DatasetSpec(
        "ipos", None, ref_schema.IPO_COLUMNS
    ),  # method name singular (usually 0-1 rows); macro name is plural
    "short_volume": DatasetSpec("short_volume", ref_schema.SHORT_VOLUME_DATE, ref_schema.SHORT_VOLUME_COLUMNS),
    "short_interest": DatasetSpec(
        "short_interest", ref_schema.SHORT_INTEREST_SETTLEMENT_DATE, ref_schema.SHORT_INTEREST_COLUMNS
    ),
    "float_data": DatasetSpec(
        "float_data", None, ref_schema.FLOAT_COLUMNS
    ),  # a snapshot -- overwritten wholesale each ingest run, nothing to range-filter
}
