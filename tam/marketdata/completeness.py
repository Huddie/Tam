"""Per-day/month/year completeness index for one symbol-year of minute
bars -- actual row counts vs. expected trading-session minutes (NYSE
calendar, via pandas_market_calendars -- the same optional `marketdata`
extra dependency tam.marketdata.validate's own session-coverage check
already uses, and the same "skip with None, don't fail outright" behavior
when it isn't installed).

Computed once per write (see MinuteBarStore._upsert_partition), not per
read/query -- cheap (one groupby over an already-in-memory year's `ts`
column), but no reason to redo it on every request either. Persisted as a
small JSON sidecar next to each year's parquet file
(<root>/<SYMBOL>/<year>.completeness.json), which tam-data-explorer's
Worker just reads back verbatim -- it never recomputes this itself
(porting a full NYSE trading calendar into a Cloudflare Worker isn't worth
it for something already computed correctly at ingest time in Python).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pandas as pd


@dataclass(frozen=True)
class DayCompleteness:
    day: int
    actual_minutes: int
    expected_minutes: int


@dataclass(frozen=True)
class MonthCompleteness:
    month: int
    actual_minutes: int
    expected_minutes: int
    days: List[DayCompleteness] = field(default_factory=list)


@dataclass(frozen=True)
class CompletenessIndex:
    symbol: str
    year: int
    calendar: str
    actual_minutes: int
    expected_minutes: int
    months: List[MonthCompleteness] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(
            {
                "symbol": self.symbol,
                "year": self.year,
                "calendar": self.calendar,
                "actual_minutes": self.actual_minutes,
                "expected_minutes": self.expected_minutes,
                "months": [
                    {
                        "month": m.month,
                        "actual_minutes": m.actual_minutes,
                        "expected_minutes": m.expected_minutes,
                        "days": [
                            {"day": d.day, "actual_minutes": d.actual_minutes, "expected_minutes": d.expected_minutes}
                            for d in m.days
                        ],
                    }
                    for m in self.months
                ],
            }
        )


def completeness_sidecar_suffix() -> str:
    """The filename suffix a year's completeness sidecar uses, next to that
    same year's own `.parquet` file -- e.g. `2024.parquet` ->
    `2024.completeness.json`. A shared helper so store.py (writer) and
    anything else that needs to derive one path from the other (e.g.
    tam-data-explorer's Worker, and scripts/backfill_completeness.py) never
    duplicate this string transform."""
    return ".completeness.json"


def compute_completeness(symbol: str, year: int, df: pd.DataFrame, *, calendar: str = "NYSE") -> Optional[CompletenessIndex]:
    """Builds a CompletenessIndex from `df` -- one symbol-year's already-
    UPSERTed minute bars (tz-aware UTC `ts` index), the SAME frame
    MinuteBarStore._upsert_partition just wrote to disk/R2, not re-read
    from anywhere. Returns None (not an index with zeros everywhere) if
    pandas_market_calendars isn't installed, so a caller can just skip
    writing the sidecar entirely in that case rather than persisting a
    misleadingly-empty one."""
    try:
        import pandas_market_calendars as mcal
    except ImportError:
        return None

    schedule = mcal.get_calendar(calendar).schedule(start_date=f"{year}-01-01", end_date=f"{year}-12-31")

    expected_by_day: Dict = {}
    for date, row in schedule.iterrows():
        expected_by_day[date.date()] = int((row["market_close"] - row["market_open"]).total_seconds() // 60)

    actual_by_day: Dict = df.groupby(df.index.date).size().to_dict() if not df.empty else {}

    all_days = sorted(set(expected_by_day) | set(actual_by_day))
    by_month: Dict[int, List[DayCompleteness]] = {}
    for day in all_days:
        by_month.setdefault(day.month, []).append(
            DayCompleteness(
                day=day.day,
                actual_minutes=int(actual_by_day.get(day, 0)),
                expected_minutes=int(expected_by_day.get(day, 0)),
            )
        )

    months: List[MonthCompleteness] = []
    total_actual = 0
    total_expected = 0
    for month in sorted(by_month):
        days = sorted(by_month[month], key=lambda d: d.day)
        month_actual = sum(d.actual_minutes for d in days)
        month_expected = sum(d.expected_minutes for d in days)
        total_actual += month_actual
        total_expected += month_expected
        months.append(MonthCompleteness(month=month, actual_minutes=month_actual, expected_minutes=month_expected, days=days))

    return CompletenessIndex(
        symbol=symbol.upper(),
        year=year,
        calendar=calendar,
        actual_minutes=total_actual,
        expected_minutes=total_expected,
        months=months,
    )
