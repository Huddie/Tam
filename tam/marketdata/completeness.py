"""Per-day/month/year completeness index for one symbol-year of bars --
actual bar counts vs. expected trading-session bar counts (NYSE calendar,
via pandas_market_calendars -- the same optional `marketdata` extra
dependency tam.marketdata.validate's own session-coverage check already
uses, and the same "skip with None, don't fail outright" behavior when it
isn't installed).

Field names are deliberately bar-count-generic (actual_bars/expected_bars,
not actual_minutes/expected_minutes) even though the only caller today is
tam.marketdata's own 1-minute bar store, where one bar happens to equal one
minute. Naming it "minutes" would bake in an assumption that stops being
true the moment anything ingests a different bar size (5-minute bars,
daily bars, ...) -- this schema/module shouldn't need a breaking rename
just because a second bar granularity shows up later.

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

import pandas as pd


@dataclass(frozen=True)
class DayCompleteness:
    day: int
    actual_bars: int
    expected_bars: int
    extended_hours_bars: int = 0


@dataclass(frozen=True)
class MonthCompleteness:
    month: int
    actual_bars: int
    expected_bars: int
    extended_hours_bars: int = 0
    days: list[DayCompleteness] = field(default_factory=list)


@dataclass(frozen=True)
class CompletenessIndex:
    symbol: str
    year: int
    calendar: str
    actual_bars: int
    expected_bars: int
    extended_hours_bars: int = 0
    months: list[MonthCompleteness] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "symbol": self.symbol,
                "year": self.year,
                "calendar": self.calendar,
                "actual_bars": self.actual_bars,
                "expected_bars": self.expected_bars,
                "extended_hours_bars": self.extended_hours_bars,
                "months": [
                    {
                        "month": m.month,
                        "actual_bars": m.actual_bars,
                        "expected_bars": m.expected_bars,
                        "extended_hours_bars": m.extended_hours_bars,
                        "days": [
                            {
                                "day": d.day,
                                "actual_bars": d.actual_bars,
                                "expected_bars": d.expected_bars,
                                "extended_hours_bars": d.extended_hours_bars,
                            }
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


# Bumped whenever the JSON shape changes in a way an old sidecar can't be
# told apart from a current one just by "does this key exist" (e.g. the
# actual_minutes/expected_minutes -> actual_bars/expected_bars rename --
# an old sidecar simply lacks the NEW keys, so code reading it would see
# zeros everywhere instead of an error). scripts/backfill_completeness.py
# checks this to tell "already backfilled, current schema" apart from
# "exists, but from a previous schema" -- only the latter needs a
# mandatory rewrite even without --force.
SCHEMA_VERSION = 2


def sidecar_schema_version(sidecar_bytes: bytes) -> int:
    """The schema_version an already-written sidecar's JSON claims -- 1
    (not 0) for anything written before this field existed at all, since
    that's the schema those bytes actually are (the very first one,
    actual_minutes/expected_minutes), not "unversioned"."""
    try:
        payload = json.loads(sidecar_bytes)
    except (ValueError, TypeError):
        return 1
    return int(payload.get("schema_version") or 1)


def compute_completeness(
    symbol: str, year: int, df: pd.DataFrame, *, calendar: str = "NYSE"
) -> CompletenessIndex | None:
    """Builds a CompletenessIndex from `df` -- one symbol-year's already-
    UPSERTed 1-minute bars (tz-aware UTC `ts` index), the SAME frame
    MinuteBarStore._upsert_partition just wrote to disk/R2, not re-read
    from anywhere. expected_bars is the regular NYSE session's length in
    minutes, since that's this caller's own bar size -- a future caller
    with a different bar size would need its own expected-count formula,
    but the CompletenessIndex/DayCompleteness shape itself (actual_bars vs
    expected_bars) doesn't assume minutes anywhere. Returns None (not an
    index with zeros everywhere) if pandas_market_calendars isn't
    installed, so a caller can just skip writing the sidecar entirely in
    that case rather than persisting a misleadingly-empty one."""
    try:
        import pandas_market_calendars as mcal
    except ImportError:
        return None

    schedule = mcal.get_calendar(calendar).schedule(start_date=f"{year}-01-01", end_date=f"{year}-12-31")

    expected_by_day: dict = {}
    session_bounds: dict = {}
    for date, row in schedule.iterrows():
        day = date.date()
        expected_by_day[day] = int((row["market_close"] - row["market_open"]).total_seconds() // 60)
        session_bounds[day] = (row["market_open"], row["market_close"])

    # Counted PER regular session window, not "every row on this calendar
    # date" -- the stored bars can include extended-hours trades (pre-
    # market/after-hours), and expected_bars above is the REGULAR session
    # length only. Counting every row for the date against that narrower
    # expected total let a handful of extended-hours bars push actual_bars
    # past 100% of expected for a real, fully-covered session -- comparing
    # like against like here is what keeps that from happening.
    # Extended-hours bars aren't discarded, just counted separately
    # (extended_hours_bars) -- informational, not part of the completeness
    # ratio, since there's no "expected" extended-hours count to compare
    # against. A day with rows that ISN'T a scheduled trading day at all
    # (a data anomaly, not extended hours) has no session window to scope
    # to, so it keeps its raw, unscoped count as actual_bars instead --
    # still worth surfacing (via expected_bars=0) rather than silently
    # dropped.
    actual_by_day: dict = {}
    extended_by_day: dict = {}
    if not df.empty:
        for day, group in df.groupby(df.index.date):
            bounds = session_bounds.get(day)
            if bounds is None:
                actual_by_day[day] = len(group)
            else:
                market_open, market_close = bounds
                in_session = (group.index >= market_open) & (group.index < market_close)
                actual_by_day[day] = int(in_session.sum())
                extended_by_day[day] = int((~in_session).sum())

    all_days = sorted(set(expected_by_day) | set(actual_by_day))
    by_month: dict[int, list[DayCompleteness]] = {}
    for day in all_days:
        by_month.setdefault(day.month, []).append(
            DayCompleteness(
                day=day.day,
                actual_bars=int(actual_by_day.get(day, 0)),
                expected_bars=int(expected_by_day.get(day, 0)),
                extended_hours_bars=int(extended_by_day.get(day, 0)),
            )
        )

    months: list[MonthCompleteness] = []
    total_actual = 0
    total_expected = 0
    total_extended = 0
    for month in sorted(by_month):
        days = sorted(by_month[month], key=lambda d: d.day)
        month_actual = sum(d.actual_bars for d in days)
        month_expected = sum(d.expected_bars for d in days)
        month_extended = sum(d.extended_hours_bars for d in days)
        total_actual += month_actual
        total_expected += month_expected
        total_extended += month_extended
        months.append(
            MonthCompleteness(
                month=month,
                actual_bars=month_actual,
                expected_bars=month_expected,
                extended_hours_bars=month_extended,
                days=days,
            )
        )

    return CompletenessIndex(
        symbol=symbol.upper(),
        year=year,
        calendar=calendar,
        actual_bars=total_actual,
        expected_bars=total_expected,
        extended_hours_bars=total_extended,
        months=months,
    )
