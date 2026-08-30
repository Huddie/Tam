"""QuantStats adapter: the two functions that bridge a `Report` (price-level
equity curves, possibly a plain `datetime.date` index if it came from a
harness) to what every quantstats.{stats,plots,reports} function actually
expects (period returns, a real `DatetimeIndex`). Nothing else -- call
quantstats' own functions directly with this module's output rather than
going through a per-function wrapper here: quantstats.plots' own functions
don't all share one signature (e.g. "snapshot"/"drawdown" don't take a
`benchmark` kwarg at all, unlike "returns"/"rolling_sharpe"), so a wrapper
that tries to forward one blindly either breaks or silently does nothing
depending which quantstats function you named -- letting you call the real
function yourself means you're reading quantstats' own docs/signature for
whichever one you pick, not this module's guess at a common shape.

Needs the `quantstats` extra (`pip install "tam-quant[quantstats]"`) for
anything beyond importing this module, which always works.

    from tam.backtest.quantstats_report import returns_for, resolve_benchmark
    import quantstats as qs

    returns = returns_for(report, "main")
    benchmark = resolve_benchmark(report, "alt")   # or "SPY", or an already-built Series, or None

    qs.reports.metrics(returns, benchmark=benchmark, mode="full")
    qs.plots.snapshot(returns)
    qs.reports.html(returns, benchmark=benchmark, output="tearsheet.html")

Kept out of report.py/visualization.py so those stay free of this dependency
-- the same separation report.py's own docstring already describes for
plotly ("Kept free of any plotting dependency; see visualization.py").
"""

from __future__ import annotations

import pandas as pd

from .report import Report


def returns_for(report: Report, portfolio_id: str) -> pd.Series:
    """`report.equity_curve(portfolio_id)` as period returns, DatetimeIndex --
    what every quantstats.stats/.plots/.reports function expects (price
    levels don't work). Coerces the index via pd.to_datetime() regardless of
    whether the source Report came from a harness (plain datetime.date) or
    Report.from_curves (often already Timestamp) -- quantstats' own
    resampling (monthly heatmaps, calendar-year CAGR) needs a real
    DatetimeIndex, not date objects."""
    curve = report.equity_curve(portfolio_id)
    curve = curve.set_axis(pd.to_datetime(curve.index))
    return curve.pct_change().dropna()


def resolve_benchmark(report: Report, benchmark: str | pd.Series | None):
    """A benchmark that names another portfolio already in THIS Report
    resolves to that portfolio's own returns (comparing two strategies from
    the same backtest run, no network involved) -- anything else (a raw
    ticker string quantstats fetches itself via yfinance, an already-built
    pd.Series, or None) passes through unchanged, ready to hand straight to
    any quantstats function's own `benchmark=` argument."""
    if isinstance(benchmark, str) and benchmark in report.portfolio_ids():
        return returns_for(report, benchmark)
    return benchmark
