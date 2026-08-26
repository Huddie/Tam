"""Actual-vs-expected trading-day completeness for one symbol-year of daily
EOD bars -- reuses the SAME output shape (CompletenessIndex/MonthCompleteness/
DayCompleteness) as tam.marketdata.completeness's minute-bar version, since
its actual_bars/expected_bars fields were deliberately kept granularity-
generic (not actual_minutes/expected_minutes) for exactly this reuse -- see
that module's own docstring. tam-data-explorer's existing completeness badge/
popover therefore needs NO changes to render this: it already reads whatever
CompletenessIndex JSON sits next to a .parquet file, regardless of whether
that file holds minute or daily bars.

The COMPUTATION differs from the minute-bar version, though: a daily bar has
no session window or extended-hours concept -- "expected" is just "was this
a NYSE trading day", "actual" is just "is there a row for it". Every day's
actual_bars/expected_bars is therefore 0 or 1, and extended_hours_bars is
always 0 (kept in the shape for compatibility, just never populated here).
"""
from __future__ import annotations

from typing import Dict, List, Optional

import pandas as pd

from ..marketdata.completeness import (
    SCHEMA_VERSION,
    CompletenessIndex,
    DayCompleteness,
    MonthCompleteness,
    completeness_sidecar_suffix,
    sidecar_schema_version,
)

__all__ = [
    "SCHEMA_VERSION",
    "completeness_sidecar_suffix",
    "sidecar_schema_version",
    "compute_completeness",
]


def compute_completeness(symbol: str, year: int, df: pd.DataFrame, *, calendar: str = "NYSE") -> Optional[CompletenessIndex]:
    """Builds a CompletenessIndex from `df` -- one symbol-year's already-
    UPSERTed daily bars (naive `date` index), the SAME frame the store's
    _upsert_partition just wrote to disk/R2. Returns None (not an index with
    zeros everywhere) if pandas_market_calendars isn't installed, same
    "skip the sidecar entirely" contract as the minute-bar version."""
    try:
        import pandas_market_calendars as mcal
    except ImportError:
        return None

    schedule = mcal.get_calendar(calendar).schedule(start_date=f"{year}-01-01", end_date=f"{year}-12-31")
    expected_days = {d.date() for d in schedule.index}

    actual_days = set()
    if not df.empty:
        actual_days = {d for d in df.index.date if d.year == year}

    all_days = sorted(expected_days | actual_days)
    by_month: Dict[int, List[DayCompleteness]] = {}
    for day in all_days:
        by_month.setdefault(day.month, []).append(
            DayCompleteness(
                day=day.day,
                actual_bars=1 if day in actual_days else 0,
                expected_bars=1 if day in expected_days else 0,
            )
        )

    months: List[MonthCompleteness] = []
    total_actual = 0
    total_expected = 0
    for month in sorted(by_month):
        days = sorted(by_month[month], key=lambda d: d.day)
        month_actual = sum(d.actual_bars for d in days)
        month_expected = sum(d.expected_bars for d in days)
        total_actual += month_actual
        total_expected += month_expected
        months.append(MonthCompleteness(month=month, actual_bars=month_actual, expected_bars=month_expected, days=days))

    return CompletenessIndex(
        symbol=symbol.upper(),
        year=year,
        calendar=calendar,
        actual_bars=total_actual,
        expected_bars=total_expected,
        months=months,
    )
