"""Optional QuantStats integration: feeds a Report's own equity curves into
quantstats.{stats,plots,reports} for its much larger metrics/plot/tearsheet
library, ALONGSIDE (not instead of) Report.summary()/visualization.render().

Needs the `quantstats` extra (`pip install "tam-quant[quantstats]"`) -- it
pulls in matplotlib/seaborn/tabulate, real weight nothing else here needs, so
`import quantstats` happens lazily inside each function below, not at this
module's top level. Plain `import tam.backtest.quantstats_report` always
works; calling one of its functions without the extra installed raises a
clear ImportError instead of failing at import time for everyone.

    from tam.backtest.visualization import write_html
    from tam.backtest import quantstats_report

    write_html(report, "dashboard.html")                            # ours
    quantstats_report.write_html(report, "main", "tearsheet.html")  # quantstats

Kept out of report.py/visualization.py so those stay free of this dependency
-- the same separation report.py's own docstring already describes for
plotly ("Kept free of any plotting dependency; see visualization.py").
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional, Union

import pandas as pd

from .report import Report

_IMPORT_ERROR = (
    "quantstats_report needs the `quantstats` extra: run `uv sync --extra quantstats` "
    'and retry, or in a notebook `!pip install -q "tam-quant[quantstats]"`.'
)


def _import_quantstats():
    try:
        import quantstats as qs
    except ImportError as exc:
        raise ImportError(_IMPORT_ERROR) from exc
    return qs


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


def _resolve_benchmark(report: Report, benchmark: Optional[Union[str, pd.Series]]):
    """A benchmark that names another portfolio already in THIS Report
    resolves to that portfolio's own returns (comparing two strategies from
    the same backtest run, no network involved) -- anything else (a raw
    ticker string quantstats fetches itself via yfinance, an already-built
    pd.Series, or None) passes through unchanged."""
    if isinstance(benchmark, str) and benchmark in report.portfolio_ids():
        return returns_for(report, benchmark)
    return benchmark


def metrics(
    report: Report,
    portfolio_id: str,
    benchmark: Optional[Union[str, pd.Series]] = None,
    mode: str = "basic",
    **kwargs,
) -> pd.DataFrame:
    """quantstats.reports.metrics(...) -- the ~60-metric table (mode="full"
    for the complete set), ALONGSIDE (not instead of) the smaller
    report.summary(portfolio_id)/report.summary_all(). `benchmark` -- see
    _resolve_benchmark(). `**kwargs` passes straight through to
    quantstats.reports.metrics (rf, periods_per_year, ...)."""
    qs = _import_quantstats()
    return qs.reports.metrics(
        returns_for(report, portfolio_id),
        benchmark=_resolve_benchmark(report, benchmark),
        mode=mode,
        display=False,
        **kwargs,
    )


def plot(report: Report, portfolio_id: str, kind: str = "snapshot", benchmark: Optional[Union[str, pd.Series]] = None, **kwargs):
    """getattr(quantstats.plots, kind)(...) -- e.g. kind="snapshot" (default),
    "drawdown", "monthly_heatmap", or any other function name in
    quantstats.plots. `**kwargs` passes straight through (title, savefig, ...)."""
    qs = _import_quantstats()
    try:
        plot_fn = getattr(qs.plots, kind)
    except AttributeError:
        available = [name for name in dir(qs.plots) if not name.startswith("_")]
        raise ValueError(f"kind must be one of quantstats.plots' functions {available}, got {kind!r}") from None
    return plot_fn(returns_for(report, portfolio_id), benchmark=_resolve_benchmark(report, benchmark), **kwargs)


def write_html(
    report: Report,
    portfolio_id: str,
    path: str,
    benchmark: Optional[Union[str, pd.Series]] = None,
    **kwargs,
) -> Path:
    """quantstats.reports.html(...) -- a full QuantStats tearsheet written to
    `path`, the quantstats counterpart to visualization.write_html()'s
    plotly dashboard. `**kwargs` passes straight through (title,
    periods_per_year, ...)."""
    qs = _import_quantstats()
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    qs.reports.html(
        returns_for(report, portfolio_id),
        benchmark=_resolve_benchmark(report, benchmark),
        output=str(out_path),
        **kwargs,
    )
    return out_path
