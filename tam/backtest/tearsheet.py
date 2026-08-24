"""Tearsheet: an extensible, registry-driven multi-chart + metrics-table
report -- the same layout QuantStats' own tearsheet uses, rendered with our
own Plotly figures (see visualization.py) instead of matplotlib/seaborn.

Two Registry(...) interfaces, the same "classic pattern" as Factor/Presenter/
CostModel elsewhere in this codebase:

    @Registry.register(TearsheetChart, "my_chart")
    class MyChart(TearsheetChart):
        title = "My Chart"
        def render(self, report: Report) -> go.Figure: ...

    @Registry.register(TearsheetMetric, "my_metric")
    class MyMetric(TearsheetMetric):
        label, format = "My Metric", "pct"
        def compute(self, report: Report, portfolio_id: str) -> float: ...

Reference either by its registered id in `charts=[...]`/`metrics=[...]`
below, or pass an already-constructed instance directly (e.g. for
non-default params: `RollingSharpeChart(window_days=60)`) -- no change to
this module needed either way.

`SharpeMetric`/`MaxDrawdownMetric` literally reuse tam.basket.factors'
RollingSharpe/MaxDrawdown Factor classes rather than re-deriving the same
formula a third time (Report.summary() has its own small inline version too,
for its own smaller table) -- see each one's own docstring.

Layout: `charts` stacked in a left column, the metrics table in a right
column, when 3 or fewer portfolios are being compared -- more than that and
a fixed-width side table gets cramped with that many per-portfolio columns,
so it switches to charts on top (full width), table below (full width).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import List, Optional, Union

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from ..basket.factors import MaxDrawdown, RollingSharpe
from ..registry import Registry
from .report import Report

_SIDE_BY_SIDE_MAX_PORTFOLIOS = 3


class TearsheetChart(ABC):
    """One chart panel -- a standalone go.Figure built from a Report. Every
    registered chart gets its own figure (not one shared subplot grid), so
    adding/removing charts from a tearsheet never means rewiring row/col
    indices elsewhere."""

    title: str = ""

    @abstractmethod
    def render(self, report: Report) -> go.Figure: ...


class TearsheetMetric(ABC):
    """One row of the tearsheet's metrics table -- one value per portfolio."""

    label: str = ""
    format: str = "ratio"  # "ratio" | "pct" | "currency" | "int"

    @abstractmethod
    def compute(self, report: Report, portfolio_id: str) -> float: ...


def _returns(report: Report, portfolio_id: str) -> pd.Series:
    """One portfolio's own daily returns, with a real DatetimeIndex --
    coerced the same way quantstats_report.returns_for() already does,
    regardless of whether `report` came from a live harness (plain
    datetime.date snapshots) or Report.from_curves() (often already
    Timestamp). Every chart/metric below builds on this so pandas'
    .resample()/.rolling() (which need a real DatetimeIndex) and
    tam.basket.factors' Factor.compute() (which does its own
    `.loc[:pd.Timestamp(as_of)]`) both just work, whichever kind of Report
    was handed in."""
    curve = report.equity_curve(portfolio_id)
    curve = curve.set_axis(pd.to_datetime(curve.index))
    return curve.pct_change().dropna()


def _factor_input(report: Report, portfolio_id: str) -> pd.DataFrame:
    """`_returns()` reshaped as the single-column DataFrame tam.basket.factors'
    Factor.compute(returns, as_of) expects -- see SharpeMetric/
    MaxDrawdownMetric below."""
    return _returns(report, portfolio_id).to_frame(portfolio_id)


# ---- charts -----------------------------------------------------------------


@Registry.register(TearsheetChart, "cumulative_returns")
class CumulativeReturnsChart(TearsheetChart):
    title = "Cumulative Returns vs Benchmark"

    def render(self, report: Report) -> go.Figure:
        fig = go.Figure()
        for portfolio_id in report.portfolio_ids():
            curve = report.equity_curve(portfolio_id)
            normalized = curve / curve.iloc[0] - 1.0
            fig.add_trace(go.Scatter(x=normalized.index, y=normalized.values, mode="lines", name=portfolio_id))
        fig.add_hline(y=0, line_width=1)
        fig.update_layout(title=self.title, yaxis_tickformat=".0%", template="plotly_white")
        return fig


@Registry.register(TearsheetChart, "cumulative_returns_log")
class LogCumulativeReturnsChart(TearsheetChart):
    title = "Cumulative Returns (Log Scaled)"

    def render(self, report: Report) -> go.Figure:
        fig = go.Figure()
        for portfolio_id in report.portfolio_ids():
            curve = report.equity_curve(portfolio_id)
            normalized = curve / curve.iloc[0]
            fig.add_trace(go.Scatter(x=normalized.index, y=normalized.values, mode="lines", name=portfolio_id))
        fig.update_layout(title=self.title, yaxis_type="log", template="plotly_white")
        return fig


@Registry.register(TearsheetChart, "drawdown")
class DrawdownChart(TearsheetChart):
    title = "Underwater Plot"

    def render(self, report: Report) -> go.Figure:
        fig = go.Figure()
        for portfolio_id in report.portfolio_ids():
            drawdown = report.drawdown_curve(portfolio_id)
            fig.add_trace(go.Scatter(x=drawdown.index, y=drawdown.values, mode="lines", name=portfolio_id, fill="tozeroy"))
        fig.update_layout(title=self.title, yaxis_tickformat=".0%", template="plotly_white")
        return fig


@Registry.register(TearsheetChart, "rolling_sharpe")
class RollingSharpeChart(TearsheetChart):
    title = "Rolling Sharpe"

    def __init__(self, window_days: int = 126):
        self._window_days = window_days
        self.title = f"Rolling Sharpe ({window_days}d)"

    def render(self, report: Report) -> go.Figure:
        fig = go.Figure()
        for portfolio_id in report.portfolio_ids():
            returns = _returns(report, portfolio_id)
            rolling = returns.rolling(self._window_days).apply(
                lambda window: window.mean() / window.std() * (252**0.5) if window.std() else 0.0
            )
            fig.add_trace(go.Scatter(x=rolling.index, y=rolling.values, mode="lines", name=portfolio_id))
        fig.add_hline(y=0, line_width=1)
        fig.update_layout(title=self.title, template="plotly_white")
        return fig


@Registry.register(TearsheetChart, "rolling_volatility")
class RollingVolatilityChart(TearsheetChart):
    title = "Rolling Volatility"

    def __init__(self, window_days: int = 126):
        self._window_days = window_days
        self.title = f"Rolling Volatility ({window_days}d, ann.)"

    def render(self, report: Report) -> go.Figure:
        fig = go.Figure()
        for portfolio_id in report.portfolio_ids():
            returns = _returns(report, portfolio_id)
            rolling = returns.rolling(self._window_days).std() * (252**0.5)
            fig.add_trace(go.Scatter(x=rolling.index, y=rolling.values, mode="lines", name=portfolio_id))
        fig.update_layout(title=self.title, yaxis_tickformat=".0%", template="plotly_white")
        return fig


@Registry.register(TearsheetChart, "monthly_returns_distribution")
class MonthlyReturnsDistributionChart(TearsheetChart):
    title = "Distribution of Monthly Returns"

    def render(self, report: Report) -> go.Figure:
        fig = go.Figure()
        for portfolio_id in report.portfolio_ids():
            monthly = _returns(report, portfolio_id).add(1).resample("ME").prod().sub(1)
            fig.add_trace(go.Histogram(x=monthly.values, name=portfolio_id, opacity=0.6))
        fig.update_layout(title=self.title, barmode="overlay", xaxis_tickformat=".0%", template="plotly_white")
        return fig


@Registry.register(TearsheetChart, "eoy_returns")
class EOYReturnsChart(TearsheetChart):
    title = "EOY Returns vs Benchmark"

    def render(self, report: Report) -> go.Figure:
        fig = go.Figure()
        for portfolio_id in report.portfolio_ids():
            yearly = _returns(report, portfolio_id).add(1).resample("YE").prod().sub(1)
            fig.add_trace(go.Bar(x=[d.year for d in yearly.index], y=yearly.values, name=portfolio_id))
        fig.add_hline(y=0, line_width=1)
        fig.update_layout(title=self.title, yaxis_tickformat=".0%", barmode="group", template="plotly_white")
        return fig


DEFAULT_CHARTS = [
    "cumulative_returns",
    "cumulative_returns_log",
    "drawdown",
    "rolling_sharpe",
    "rolling_volatility",
    "monthly_returns_distribution",
    "eoy_returns",
]


def _suffix_max_drawdown(r: np.ndarray) -> np.ndarray:
    """Maximum drawdown reachable starting from every possible index i
    through the end of `r`, in O(n) by walking backward -- the "starting
    wealth of 1.0 is itself a possible peak" case is handled by seeding
    next_min_wealth=1.0 before the loop. Used by WorstDrawdownPathsChart to
    rank candidate start dates by how bad their OWN forward path gets, not
    by the whole-history max drawdown every start date would otherwise
    share."""
    r = np.asarray(r, dtype=float)
    n = len(r)
    max_drawdown = np.empty(n, dtype=float)
    next_min_wealth = 1.0
    next_max_drawdown = 0.0
    for i in range(n - 1, -1, -1):
        gross = 1.0 + r[i]
        current_min_wealth = min(1.0, gross * next_min_wealth)
        drawdown_from_start = current_min_wealth - 1.0
        current_max_drawdown = min(drawdown_from_start, next_max_drawdown)
        max_drawdown[i] = current_max_drawdown
        next_min_wealth = current_min_wealth
        next_max_drawdown = current_max_drawdown
    return max_drawdown


def _return_distribution_by_end_date(r: np.ndarray) -> dict:
    """For every possible END index k, the avg/min/max/std of total return
    across every possible EARLIER start index through k -- "how sensitive is
    the outcome to when you happened to start," not one fixed backtest
    period. O(n) via an expanding mean/min/max/std of 1/entry_wealth rather
    than an O(n^2) double loop over every (start, end) pair."""
    r = np.asarray(r, dtype=float)
    cumulative_growth = np.cumprod(1.0 + r)
    entry_base = np.r_[1.0, cumulative_growth[:-1]]
    inverse_entry_base = pd.Series(1.0 / entry_base)

    expanding_mean = inverse_entry_base.expanding(min_periods=1).mean().to_numpy()
    expanding_min = inverse_entry_base.expanding(min_periods=1).min().to_numpy()
    expanding_max = inverse_entry_base.expanding(min_periods=1).max().to_numpy()
    expanding_std = inverse_entry_base.expanding(min_periods=2).std(ddof=1).to_numpy()

    return {
        "avg": cumulative_growth * expanding_mean - 1.0,
        "min": cumulative_growth * expanding_min - 1.0,
        "max": cumulative_growth * expanding_max - 1.0,
        "std": cumulative_growth * expanding_std,
    }


@Registry.register(TearsheetChart, "return_distribution_by_start_date")
class ReturnDistributionByStartDateChart(TearsheetChart):
    """For every possible END date in a portfolio's own history, the
    avg/min/max/std of total return across every possible EARLIER start date
    through that end -- "how much does the outcome depend on when you
    happened to start," not one fixed backtest period. Not in DEFAULT_CHARTS
    (busy with 4 traces per portfolio) -- opt in via
    charts=DEFAULT_CHARTS + ["return_distribution_by_start_date"]."""

    title = "Return Distribution Across All Start Dates"

    def render(self, report: Report) -> go.Figure:
        fig = go.Figure()
        for portfolio_id in report.portfolio_ids():
            returns = _returns(report, portfolio_id)
            stats = _return_distribution_by_end_date(returns.to_numpy())
            for stat_name in ("avg", "min", "max", "std"):
                fig.add_trace(
                    go.Scatter(x=returns.index, y=stats[stat_name], mode="lines", name=f"{portfolio_id} {stat_name}")
                )
        fig.add_hline(y=0, line_width=1, line_dash="dash")
        fig.update_layout(title=self.title, yaxis_tickformat=".0%", template="plotly_white")
        return fig


@Registry.register(TearsheetChart, "worst_drawdown_paths")
class WorstDrawdownPathsChart(TearsheetChart):
    """The `n_series` start dates whose OWN forward max drawdown (see
    _suffix_max_drawdown) is at or beyond `threshold`, each drawn as its own
    raw daily-return path from that start date onward -- the actual
    day-by-day path a deep drawdown took, not just its single worst-point
    number. `threshold` defaults to -20% (broadly applicable); a single
    volatile ticker's own worst cases might warrant something far more
    extreme (e.g. -90%) -- construct your own
    WorstDrawdownPathsChart(threshold=-0.90) for that instead of the
    registered default. Not in DEFAULT_CHARTS -- opt in via
    charts=DEFAULT_CHARTS + ["worst_drawdown_paths"]."""

    def __init__(self, threshold: float = -0.20, n_series: int = 10):
        self._threshold = threshold
        self._n_series = n_series
        self.title = f"Worst Drawdown Return Paths (MDD ≤ {threshold:.0%})"

    def render(self, report: Report) -> go.Figure:
        fig = go.Figure()
        any_series = False
        for portfolio_id in report.portfolio_ids():
            returns = _returns(report, portfolio_id)
            r = returns.to_numpy()
            dates = returns.index
            suffix_mdd = _suffix_max_drawdown(r)

            deep = np.where(suffix_mdd <= self._threshold)[0]
            if len(deep) == 0:
                continue
            worst = deep[np.argsort(suffix_mdd[deep])[: self._n_series]]
            for i in sorted(worst):
                any_series = True
                fig.add_trace(
                    go.Scatter(
                        x=dates[i:],
                        y=r[i:],
                        mode="lines",
                        opacity=0.7,
                        name=f"{portfolio_id} {dates[i].date()} (MDD {suffix_mdd[i]:.0%})",
                    )
                )

        if not any_series:
            fig.add_annotation(text=f"No start dates with MDD ≤ {self._threshold:.0%}", showarrow=False, xref="paper", yref="paper", x=0.5, y=0.5)
        fig.add_hline(y=0, line_width=1, line_dash="dash")
        fig.update_layout(title=self.title, yaxis_tickformat=".1%", template="plotly_white")
        return fig


# ---- metrics ----------------------------------------------------------------


@Registry.register(TearsheetMetric, "total_return")
class TotalReturnMetric(TearsheetMetric):
    label, format = "Cumulative Return", "pct"

    def compute(self, report: Report, portfolio_id: str) -> float:
        return report.summary(portfolio_id)["total_return"]


@Registry.register(TearsheetMetric, "cagr")
class CagrMetric(TearsheetMetric):
    label, format = "CAGR", "pct"

    def compute(self, report: Report, portfolio_id: str) -> float:
        return report.summary(portfolio_id)["cagr"]


@Registry.register(TearsheetMetric, "sharpe")
class SharpeMetric(TearsheetMetric):
    """Reuses tam.basket.factors.RollingSharpe's own computation (mean/std,
    annualized) over the portfolio's full history, rather than a second
    implementation of the same formula (Report.summary()'s own inline
    version, used by its own smaller table, is the other one)."""

    label, format = "Sharpe", "ratio"

    def compute(self, report: Report, portfolio_id: str) -> float:
        returns = _factor_input(report, portfolio_id)
        as_of = returns.index[-1]
        return float(RollingSharpe(window_days=len(returns)).compute(returns, as_of)[portfolio_id])


@Registry.register(TearsheetMetric, "volatility")
class VolatilityMetric(TearsheetMetric):
    label, format = "Volatility (ann.)", "pct"

    def compute(self, report: Report, portfolio_id: str) -> float:
        return report.summary(portfolio_id)["volatility"]


@Registry.register(TearsheetMetric, "max_drawdown")
class MaxDrawdownMetric(TearsheetMetric):
    """Reuses tam.basket.factors.MaxDrawdown's own computation."""

    label, format = "Max Drawdown", "pct"

    def compute(self, report: Report, portfolio_id: str) -> float:
        returns = _factor_input(report, portfolio_id)
        as_of = returns.index[-1]
        return float(MaxDrawdown(window_days=len(returns)).compute(returns, as_of)[portfolio_id])


@Registry.register(TearsheetMetric, "calmar")
class CalmarMetric(TearsheetMetric):
    label, format = "Calmar", "ratio"

    def compute(self, report: Report, portfolio_id: str) -> float:
        return report.summary(portfolio_id)["calmar"]


@Registry.register(TearsheetMetric, "num_trades")
class NumTradesMetric(TearsheetMetric):
    label, format = "# Trades", "int"

    def compute(self, report: Report, portfolio_id: str) -> float:
        return report.summary(portfolio_id)["num_trades"]


DEFAULT_METRICS = ["total_return", "cagr", "sharpe", "volatility", "max_drawdown", "calmar", "num_trades"]

_FORMATTERS = {
    "pct": lambda v: f"{v:.2%}",
    "ratio": lambda v: f"{v:.2f}",
    "currency": lambda v: f"${v:,.2f}",
    "int": lambda v: f"{v:.0f}",
}


def _resolve(base_type: type, item):
    """`item` is either an already-constructed TearsheetChart/TearsheetMetric
    (used as-is -- the escape hatch for non-default params, e.g.
    RollingSharpeChart(window_days=60)) or a registered id string
    (Registry.get(base_type, item))."""
    return item if isinstance(item, base_type) else Registry.get(base_type, item)


def metrics_table(report: Report, metrics: List[Union[str, TearsheetMetric]] = DEFAULT_METRICS) -> pd.DataFrame:
    """One row per metric, one column per portfolio -- the tearsheet's own
    table shape. Add a row by registering a TearsheetMetric and including its
    id (or an already-constructed instance) in `metrics`."""
    portfolio_ids = report.portfolio_ids()
    rows = {}
    for item in metrics:
        metric = _resolve(TearsheetMetric, item)
        rows[metric.label] = {
            pid: _FORMATTERS[metric.format](metric.compute(report, pid)) for pid in portfolio_ids
        }
    table = pd.DataFrame(rows).T
    return table[portfolio_ids] if not table.empty else table


# ---- assembly ---------------------------------------------------------------

_STYLE = """
  body { font-family: -apple-system, Helvetica, Arial, sans-serif; margin: 24px; color: #222; }
  h1 { margin-bottom: 4px; }
  .subtitle { color: #666; margin-bottom: 24px; }
  .tearsheet-grid { display: grid; gap: 24px; align-items: start; }
  .charts-column { display: flex; flex-direction: column; gap: 12px; }
  .tearsheet-table { border-collapse: collapse; width: 100%; }
  .tearsheet-table th, .tearsheet-table td { padding: 6px 12px; border-bottom: 1px solid #eee; text-align: left; }
  .tearsheet-table th { background: #1f2a44; color: white; }
"""


def build_tearsheet(
    report: Report,
    title: str = "Strategy Tearsheet",
    charts: List[Union[str, TearsheetChart]] = DEFAULT_CHARTS,
    metrics: List[Union[str, TearsheetMetric]] = DEFAULT_METRICS,
) -> str:
    """A self-contained HTML tearsheet: `charts` stacked in a column,
    `metrics_table(report, metrics)` alongside -- side-by-side (charts left,
    table right) for <=3 portfolios being compared, stacked (charts on top,
    table below) for more (see module docstring for why). Each of `charts`/
    `metrics` is either a registered id or an already-constructed instance
    (Registry(TearsheetChart, ...)/Registry(TearsheetMetric, ...))."""
    chart_divs = []
    for i, item in enumerate(charts):
        chart = _resolve(TearsheetChart, item)
        fig = chart.render(report)
        fig_html = fig.to_html(
            full_html=False, include_plotlyjs=("cdn" if i == 0 else False), div_id=f"tam-tearsheet-chart-{i}"
        )
        chart_divs.append(f"<h3>{chart.title}</h3>{fig_html}" if chart.title else fig_html)

    table_html = metrics_table(report, metrics).to_html(classes="tearsheet-table", border=0)
    side_by_side = len(report.portfolio_ids()) <= _SIDE_BY_SIDE_MAX_PORTFOLIOS
    grid_columns = "2fr 1fr" if side_by_side else "1fr"

    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{title}</title>
<style>{_STYLE}
  .tearsheet-grid {{ grid-template-columns: {grid_columns}; }}
</style></head>
<body>
  <h1>{title}</h1>
  <div class="subtitle">Portfolios: {", ".join(report.portfolio_ids())}</div>
  <div class="tearsheet-grid">
    <div class="charts-column">{"".join(chart_divs)}</div>
    <div>{table_html}</div>
  </div>
</body></html>"""


def write_html(report: Report, path: str, **kwargs) -> Path:
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(build_tearsheet(report, **kwargs))
    return out_path


def show(report: Report, **kwargs):
    """Inline display in a notebook, e.g. `tearsheet.show(report)` as a cell's
    last expression -- IPython.display.HTML(...) of the same build_tearsheet()
    output write_html() saves to a file."""
    from IPython.display import HTML

    return HTML(build_tearsheet(report, **kwargs))


class Tearsheet:
    """Stateful, composable tearsheet builder -- configure `charts`/`metrics`/
    `title` once (via the constructor or fluent add_chart()/add_metric()
    calls), then call .show(report)/.write(report, path) as many times as
    you like without repeating charts=/metrics= on every call. The
    module-level build_tearsheet()/write_html()/show() functions are the
    stateless, one-shot equivalent -- both styles share the same
    implementation (this class is a thin wrapper), pick whichever fits.

        ts = Tearsheet().add_chart("return_distribution_by_start_date").add_chart(WorstDrawdownPathsChart(threshold=-0.9))
        ts.show(report)
        ts.write(report, "tearsheet.html")

        ts = Tearsheet(charts=[...], metrics=[...], title="...")
    """

    def __init__(
        self,
        charts: Optional[List[Union[str, TearsheetChart]]] = None,
        metrics: Optional[List[Union[str, TearsheetMetric]]] = None,
        title: str = "Strategy Tearsheet",
    ):
        self.title = title
        self.charts: List[Union[str, TearsheetChart]] = list(charts) if charts is not None else list(DEFAULT_CHARTS)
        self.metrics: List[Union[str, TearsheetMetric]] = list(metrics) if metrics is not None else list(DEFAULT_METRICS)

    def add_chart(self, chart: Union[str, TearsheetChart]) -> "Tearsheet":
        self.charts.append(chart)
        return self

    def add_metric(self, metric: Union[str, TearsheetMetric]) -> "Tearsheet":
        self.metrics.append(metric)
        return self

    def build(self, report: Report) -> str:
        return build_tearsheet(report, title=self.title, charts=self.charts, metrics=self.metrics)

    def show(self, report: Report):
        from IPython.display import HTML

        return HTML(self.build(report))

    def write(self, report: Report, path: str) -> Path:
        out_path = Path(path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(self.build(report))
        return out_path
