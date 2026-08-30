"""Validation for a single day's raw minute bars, run BEFORE anything is
written to a MinuteBarStore -- catches OHLC integrity problems and
missing/short trading sessions early, at the source, rather than letting bad
data land in R2 and surface as a confusing NaN/lookahead bug three layers
away in a backtest.

Session-coverage checks use pandas_market_calendars (an optional dependency,
gated behind the `marketdata` extra like every other specialty dependency in
this project -- see pyproject.toml) for the NYSE trading calendar: which
days should have data at all, and how long a regular vs. half-day session
runs. Without it installed, those specific checks are skipped with a warning
rather than failing outright; the OHLC-integrity checks (which need no
calendar) always run.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from .schema import CLOSE, HIGH, LOW, OPEN, SYMBOL, VOLUME


@dataclass
class ValidationReport:
    day: date
    errors: list[str] = field(default_factory=list)
    warning_messages: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def raise_if_invalid(self) -> None:
        if self.errors:
            raise ValueError(
                f"minute-bar validation failed for {self.day}: {len(self.errors)} error(s):\n"
                + "\n".join(f"  - {message}" for message in self.errors)
            )


def validate_day(df: pd.DataFrame, day: date, *, calendar: str = "NYSE") -> ValidationReport:
    """Runs OHLC-integrity checks (always) plus calendar-aware session-
    coverage checks (only if pandas_market_calendars is installed) against
    `df` -- one day's already-filtered, already-normalized bars for every
    symbol in that day's universe (tam.marketdata.schema.MINUTE_BAR_COLUMNS,
    indexed by tz-aware UTC `ts`). Returns a ValidationReport instead of
    raising directly, so a caller can decide whether to raise_if_invalid()
    immediately (tam.marketdata.ingest's default) or collect several days'
    reports first. Session-coverage findings are warnings, not errors --
    thin/illiquid names legitimately have gaps in a normal session (no trade
    in a given minute means no row), so a short session alone isn't proof of
    a real problem, just worth a human's attention."""
    report = ValidationReport(day=day)
    if df.empty:
        return report

    _check_ohlc_integrity(df, report)
    _check_session_coverage(df, day, calendar, report)
    return report


def _check_ohlc_integrity(df: pd.DataFrame, report: ValidationReport) -> None:
    bad_high = df[HIGH] < df[[OPEN, CLOSE, LOW]].max(axis=1)
    if bad_high.any():
        report.errors.append(f"{int(bad_high.sum())} row(s) have high < max(open, close, low)")

    bad_low = df[LOW] > df[[OPEN, CLOSE, HIGH]].min(axis=1)
    if bad_low.any():
        report.errors.append(f"{int(bad_low.sum())} row(s) have low > min(open, close, high)")

    bad_volume = df[VOLUME] < 0
    if bad_volume.any():
        report.errors.append(f"{int(bad_volume.sum())} row(s) have negative volume")

    for column in (OPEN, HIGH, LOW, CLOSE):
        non_positive = df[column] <= 0
        if non_positive.any():
            report.errors.append(f"{int(non_positive.sum())} row(s) have non-positive {column}")

    for symbol, group in df.groupby(SYMBOL):
        if group.index.has_duplicates:
            report.errors.append(f"{symbol}: duplicate timestamp(s) within this day")
        if not group.index.is_monotonic_increasing:
            report.errors.append(f"{symbol}: timestamps not sorted ascending")


def _check_session_coverage(df: pd.DataFrame, day: date, calendar: str, report: ValidationReport) -> None:
    try:
        import pandas_market_calendars as mcal
    except ImportError:
        message = (
            "pandas_market_calendars not installed -- skipping session-coverage checks "
            "(install the `marketdata` extra to enable them)"
        )
        report.warning_messages.append(message)
        warnings.warn(message, stacklevel=2)
        return

    schedule = mcal.get_calendar(calendar).schedule(start_date=day, end_date=day)
    if schedule.empty:
        report.errors.append(f"{day} is not a {calendar} trading day but has data -- check the date/calendar")
        return

    market_open, market_close = schedule.iloc[0]["market_open"], schedule.iloc[0]["market_close"]
    expected_minutes = int((market_close - market_open).total_seconds() // 60)

    for symbol, group in df.groupby(SYMBOL):
        actual_minutes = len(group)
        if actual_minutes < expected_minutes * 0.5:
            message = f"{symbol} on {day}: only {actual_minutes}/{expected_minutes} expected minutes present"
            report.warning_messages.append(message)
            warnings.warn(message, stacklevel=2)
