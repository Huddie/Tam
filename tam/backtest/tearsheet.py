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
from typing import List, Optional, Tuple, Union

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from ..basket.factors import ExpectedShortfall, MaxDrawdown, RollingSharpe
from ..registry import Registry
from .report import Report

_SIDE_BY_SIDE_MAX_PORTFOLIOS = 3

# Shared diverging colorscale for every return-valued heatmap in this module
# -- red for negative, green for positive, shading from dark (further from
# zero) to pale (near zero), meeting at a light neutral midpoint. Deliberately
# NOT Plotly's built-in "RdYlGn": its middle color is yellow, which reads as
# neither clearly positive nor clearly negative -- shades of a single hue per
# sign are easier to reason about at a glance.
RETURN_COLORSCALE = [
    [0.0, "rgb(120,0,0)"],
    [0.25, "rgb(200,60,60)"],
    [0.5, "rgb(245,245,245)"],
    [0.75, "rgb(60,140,60)"],
    [1.0, "rgb(0,90,0)"],
]


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


@Registry.register(TearsheetChart, "rolling_return")
class RollingReturnChart(TearsheetChart):
    """Trailing compounded return over a configurable rolling window,
    recomputed at every date -- "if I'd held for the last N ending here,
    what would my return have been," for every possible end date at once
    (equivalently: for every possible START date, the return through
    exactly N later). Configure the window in whichever unit is natural:
    exactly one of `years`/`months`/`days` (raw trading days) -- `days`
    wins if more than one is given. Defaults to a 1-year window."""

    _TRADING_DAYS_PER_YEAR = 252
    _TRADING_DAYS_PER_MONTH = 21

    def __init__(
        self,
        years: Optional[float] = None,
        months: Optional[float] = None,
        days: Optional[int] = None,
    ):
        if days is not None:
            self._window_days = days
            label = f"{days}-Day"
        elif months is not None:
            self._window_days = round(months * self._TRADING_DAYS_PER_MONTH)
            label = f"{months:g}-Month"
        elif years is not None:
            self._window_days = round(years * self._TRADING_DAYS_PER_YEAR)
            label = f"{years:g}-Year"
        else:
            self._window_days = self._TRADING_DAYS_PER_YEAR
            label = "1-Year"
        self.title = f"Rolling {label} Return"

    def render(self, report: Report) -> go.Figure:
        fig = go.Figure()
        for portfolio_id in report.portfolio_ids():
            returns = _returns(report, portfolio_id)
            # sum of log(1+r) over the window == log of the product of
            # (1+r) over the window -- exp of that back out is the exact
            # compounded return, without a slower per-window .apply(prod).
            log_growth = np.log1p(returns)
            rolling_log_sum = log_growth.rolling(self._window_days).sum()
            rolling = np.expm1(rolling_log_sum)
            fig.add_trace(go.Scatter(x=rolling.index, y=rolling.values, mode="lines", name=portfolio_id))
        fig.add_hline(y=0, line_width=1)
        fig.update_layout(title=self.title, yaxis_tickformat=".0%", template="plotly_white")
        return fig


def _rolling_return_matrix(returns: pd.Series, windows: list, start_freq: str) -> pd.DataFrame:
    """index=start date (one per `start_freq` period, e.g. month-start),
    columns=window label, values=compounded return of holding from that
    start for that window's length -- NaN wherever the window runs past the
    end of `returns`. `windows` is a list of (label, window_days) pairs.

    Computed via a cumulative sum of log(1+r) rather than an O(n * windows)
    double loop of per-window products: return from position i through
    position j-1 (a `window_days`-long holding period starting at i) is
    exp(cumulative[j] - cumulative[i]) - 1, where cumulative[k] is the sum
    of log(1+r) over returns[0:k] (cumulative[0] = 0)."""
    n = len(returns)
    log_growth = np.log1p(returns.to_numpy())
    cumulative = np.concatenate([[0.0], np.cumsum(log_growth)])

    # the integer POSITION (not date) of the first trading day in each
    # start_freq period -- lets a window "starting" on a non-trading day
    # (e.g. the 1st of a month that's a weekend) resolve to the next real
    # trading day, same as any other calendar-vs-trading-day mismatch here.
    positions = pd.Series(range(n), index=returns.index)
    period_starts = positions.resample(start_freq).first().dropna().astype(int)

    rows = {}
    for start_date, i in period_starts.items():
        row = {}
        for label, window_days in windows:
            j = i + window_days
            row[label] = float(np.exp(cumulative[j] - cumulative[i]) - 1.0) if j <= n else float("nan")
        rows[start_date] = row
    return pd.DataFrame(rows).T


@Registry.register(TearsheetChart, "rolling_return_heatmap")
class RollingReturnHeatmapChart(TearsheetChart):
    """A grid, same shape as MonthlyReturnsHeatmapChart's year x month grid,
    but for start date x window length instead: for a configurable SET of
    window lengths (default 1/2/5/10 years) crossed with every possible
    start date (bucketed to `start_freq`, default monthly -- "MS" -- so the
    grid stays readable instead of one column per single day), the
    compounded return of holding from that start for that window's length.
    Read off any (start, window) cell directly -- equivalently, any
    (start, start+window) = (start, end) pair.

    Give exactly one of `years`/`months`/`days` as a LIST of window sizes
    (e.g. years=[1, 2, 5, 10]); `days` wins if more than one is given.
    Single-portfolio (a grid per portfolio would need its own subplot) --
    pick which with `portfolio_id` (defaults to the Report's first)."""

    _TRADING_DAYS_PER_YEAR = 252
    _TRADING_DAYS_PER_MONTH = 21

    def __init__(
        self,
        years: Optional[List[float]] = None,
        months: Optional[List[float]] = None,
        days: Optional[List[int]] = None,
        start_freq: str = "MS",
        portfolio_id: Optional[str] = None,
        colorscale=RETURN_COLORSCALE,
    ):
        if days is not None:
            self._windows = [(f"{d}D", d) for d in days]
        elif months is not None:
            self._windows = [(f"{m:g}M", round(m * self._TRADING_DAYS_PER_MONTH)) for m in months]
        elif years is not None:
            self._windows = [(f"{y:g}Y", round(y * self._TRADING_DAYS_PER_YEAR)) for y in years]
        else:
            self._windows = [(f"{y}Y", y * self._TRADING_DAYS_PER_YEAR) for y in (1, 2, 5, 10)]
        self._start_freq = start_freq
        self._portfolio_id = portfolio_id
        self._colorscale = colorscale
        self.title = "Rolling Return Heatmap"

    def render(self, report: Report) -> go.Figure:
        portfolio_id = self._portfolio_id or report.portfolio_ids()[0]
        returns = _returns(report, portfolio_id)
        grid = _rolling_return_matrix(returns, self._windows, self._start_freq)
        labels = [label for label, _ in self._windows]
        grid = grid[labels]

        values = grid.T.to_numpy(dtype=float) * 100  # shape (n_windows, n_starts) -- matches y=labels, x=starts
        text = [[f"{v:.1f}" if pd.notna(v) else "" for v in row] for row in values]

        fig = go.Figure(
            go.Heatmap(
                z=values,
                x=[d.strftime("%Y-%m") for d in grid.index],
                y=labels,
                colorscale=self._colorscale,
                zmid=0,
                text=text,
                texttemplate="%{text}",
                hovertemplate="Start %{x}, window %{y}: %{z:.2f}%<extra></extra>",
            )
        )
        fig.update_layout(
            title=f"{self.title} — {portfolio_id}", xaxis_title="Start Date", yaxis_title="Window", template="plotly_white"
        )
        return fig


def _period_boundaries(index: pd.DatetimeIndex, freq: str) -> Tuple[list, list]:
    """(start_dates, end_dates) -- one pair per `freq` period `index`
    actually covers (e.g. freq="YE" -> each calendar year's first and last
    date present in `index`), for ReturnMatrixChart's default axes when the
    caller doesn't pass explicit start_dates/end_dates."""
    positions = pd.Series(range(len(index)), index=index)
    starts = positions.resample(freq).first().dropna()
    ends = positions.resample(freq).last().dropna()
    return [index[int(p)] for p in starts], [index[int(p)] for p in ends]


def _return_matrix(returns: pd.Series, start_dates: list, end_dates: list) -> pd.DataFrame:
    """index=start_dates, columns=end_dates, values=compounded return
    holding from the first available trading day ON OR AFTER each start
    date through the last available trading day ON OR BEFORE each end date
    -- NaN wherever that end resolves to before that start (or either date
    falls entirely outside `returns`' own history). Same cumulative-log-sum
    trick as _rolling_return_matrix, just indexed by explicit (start, end)
    date pairs instead of (start, window length)."""
    n = len(returns)
    log_growth = np.log1p(returns.to_numpy())
    cumulative = np.concatenate([[0.0], np.cumsum(log_growth)])
    dates = returns.index

    def _start_pos(d):
        pos = dates.searchsorted(pd.Timestamp(d), side="left")
        return int(pos) if pos < n else None

    def _end_pos(d):
        pos = dates.searchsorted(pd.Timestamp(d), side="right") - 1
        return int(pos) if pos >= 0 else None

    rows = {}
    for start_date in start_dates:
        s = _start_pos(start_date)
        row = {}
        for end_date in end_dates:
            e = _end_pos(end_date)
            row[end_date] = float(np.exp(cumulative[e + 1] - cumulative[s]) - 1.0) if (s is not None and e is not None and e >= s) else float("nan")
        rows[start_date] = row
    return pd.DataFrame(rows).T


@Registry.register(TearsheetChart, "return_matrix")
class ReturnMatrixChart(TearsheetChart):
    """X axis = start date, Y axis = end date, cell = compounded return
    holding from that start through that end -- only defined where end >=
    start (a classic "returns triangle"). Pass explicit `start_dates`/
    `end_dates` lists for exactly the dates you want on each axis; either
    (or both) omitted falls back to `freq`-derived period boundaries from
    the portfolio's own history (default "YE" -- calendar year; "QE"/"ME"/
    "W" for quarterly/monthly/weekly). Single-portfolio -- pick which with
    `portfolio_id` (defaults to the Report's first)."""

    def __init__(
        self,
        start_dates: Optional[list] = None,
        end_dates: Optional[list] = None,
        freq: str = "YE",
        portfolio_id: Optional[str] = None,
        colorscale=RETURN_COLORSCALE,
    ):
        self._start_dates = start_dates
        self._end_dates = end_dates
        self._freq = freq
        self._portfolio_id = portfolio_id
        self._colorscale = colorscale
        self.title = "Return Matrix"

    def render(self, report: Report) -> go.Figure:
        portfolio_id = self._portfolio_id or report.portfolio_ids()[0]
        returns = _returns(report, portfolio_id)

        if self._start_dates is None or self._end_dates is None:
            freq_starts, freq_ends = _period_boundaries(returns.index, self._freq)
        start_dates = self._start_dates if self._start_dates is not None else freq_starts
        end_dates = self._end_dates if self._end_dates is not None else freq_ends

        matrix = _return_matrix(returns, start_dates, end_dates)
        start_labels = [pd.Timestamp(d).strftime("%Y-%m-%d") for d in start_dates]
        end_labels = [pd.Timestamp(d).strftime("%Y-%m-%d") for d in end_dates]

        values = matrix.to_numpy(dtype=float).T * 100  # shape (n_end, n_start) -- matches y=end, x=start
        text = [[f"{v:.1f}" if pd.notna(v) else "" for v in row] for row in values]

        fig = go.Figure(
            go.Heatmap(
                z=values,
                x=start_labels,
                y=end_labels,
                colorscale=self._colorscale,
                zmid=0,
                text=text,
                texttemplate="%{text}",
                hovertemplate="Start %{x}, End %{y}: %{z:.2f}%<extra></extra>",
            )
        )
        fig.update_layout(
            title=f"{self.title} — {portfolio_id}", xaxis_title="Start", yaxis_title="End", template="plotly_white"
        )
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


@Registry.register(TearsheetChart, "monthly_returns")
class MonthlyReturnsChart(TearsheetChart):
    """What each portfolio actually made in EACH calendar month, in
    chronological order -- one bar per month per portfolio. Different from
    both monthly_returns_heatmap (same numbers, arranged as a year x month
    grid instead of a timeline) and monthly_returns_distribution (a
    histogram of the same numbers, with the calendar order thrown away)."""

    title = "Monthly Returns"

    def render(self, report: Report) -> go.Figure:
        fig = go.Figure()
        for portfolio_id in report.portfolio_ids():
            monthly = _returns(report, portfolio_id).add(1).resample("ME").prod().sub(1)
            fig.add_trace(
                go.Bar(x=[d.strftime("%Y-%m") for d in monthly.index], y=monthly.values, name=portfolio_id)
            )
        fig.add_hline(y=0, line_width=1)
        fig.update_layout(title=self.title, yaxis_tickformat=".1%", barmode="group", template="plotly_white")
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


def _suffix_stats(r: np.ndarray, dates: np.ndarray, final_exit_date) -> dict:
    """Ported directly from the user's own validated suffix-stats algorithm:
    for every possible start index i, the growth/final value/CAGR/monthly
    return/Sharpe/max drawdown/win rate of holding from i through the end of
    the series -- "what would the outcome be if I'd started here." O(n) via
    cumulative sums/products walked from the end, not an O(n^2) double loop
    over every (start, end) pair."""
    r = np.asarray(r, dtype=float)
    dates = np.asarray(dates, dtype="datetime64[ns]")
    n_obs = len(r)
    trades = np.arange(n_obs, 0, -1, dtype=int)

    growth = np.cumprod((1.0 + r)[::-1])[::-1]
    final_value = 100_000.0 * growth
    total_return = growth - 1.0

    elapsed_days = (np.datetime64(final_exit_date, "ns") - dates).astype("timedelta64[D]").astype(float)
    years = elapsed_days / 365.25
    months = years * 12.0

    cagr = np.full(n_obs, np.nan)
    valid = (years > 0) & (growth > 0)
    cagr[valid] = growth[valid] ** (1.0 / years[valid]) - 1.0

    monthly_return = np.full(n_obs, np.nan)
    valid = (months > 0) & (growth > 0)
    monthly_return[valid] = growth[valid] ** (1.0 / months[valid]) - 1.0

    sum_r = np.cumsum(r[::-1])[::-1]
    sum_r2 = np.cumsum((r**2)[::-1])[::-1]
    mean_return = sum_r / trades
    variance = np.full(n_obs, np.nan)
    valid = trades > 1
    variance[valid] = (sum_r2[valid] - (sum_r[valid] ** 2 / trades[valid])) / (trades[valid] - 1)
    variance = np.where(np.isnan(variance), np.nan, np.maximum(variance, 0.0))
    volatility = np.sqrt(variance)
    sharpe = np.full(n_obs, np.nan)
    valid = np.isfinite(volatility) & (volatility > 0)
    sharpe[valid] = mean_return[valid] / volatility[valid] * np.sqrt(252)

    wins = np.cumsum((r > 0)[::-1])[::-1]
    win_rate = wins / trades

    return {
        "growth": growth,
        "final_value": final_value,
        "total_return": total_return,
        "cagr": cagr,
        "monthly_return": monthly_return,
        "sharpe": sharpe,
        "win_rate": win_rate,
        "max_drawdown": _suffix_max_drawdown(r),
        "trades": trades,
        "years": years,
    }


def _by_start_date_series(report: Report, portfolio_id: str, field: str, min_trades: Optional[int] = None) -> pd.Series:
    """One field of _suffix_stats(...), as a Series indexed by start date --
    the shared plumbing behind every *ByStartDateChart below. `min_trades`
    (the user's own MIN_TRADES_FOR_RATIO_CHARTS) drops start dates too close
    to the end of history to produce a trustworthy annualized ratio (CAGR/
    Sharpe/monthly return) -- irrelevant for max_drawdown/final_value, which
    are meaningful even for a short remaining window."""
    returns = _returns(report, portfolio_id)
    dates = returns.index.to_numpy(dtype="datetime64[ns]")
    stats = _suffix_stats(returns.to_numpy(), dates, dates[-1])
    series = pd.Series(stats[field], index=returns.index)
    if min_trades:
        series = series[stats["trades"] >= min_trades]
    return series


@Registry.register(TearsheetChart, "max_drawdown_by_start_date")
class MaxDrawdownByStartDateChart(TearsheetChart):
    """"If I'd started on date X and held through the end of history, how
    bad would my worst drawdown have been" -- one point per possible start
    date, NOT the same thing as the Underwater Plot (which fixes ONE start
    -- the actual first date in the data -- and shows drawdown evolving over
    calendar time from there)."""

    title = "Maximum Drawdown by Start Date"

    def render(self, report: Report) -> go.Figure:
        fig = go.Figure()
        for portfolio_id in report.portfolio_ids():
            series = _by_start_date_series(report, portfolio_id, "max_drawdown")
            fig.add_trace(go.Scatter(x=series.index, y=series.values, mode="lines", name=portfolio_id))
        fig.add_hline(y=0, line_width=1)
        fig.update_layout(title=self.title, xaxis_title="Start Date", yaxis_tickformat=".0%", template="plotly_white")
        return fig


@Registry.register(TearsheetChart, "cagr_by_start_date")
class CagrByStartDateChart(TearsheetChart):
    def __init__(self, min_trades: int = 252):
        self._min_trades = min_trades
        self.title = "CAGR by Start Date"

    def render(self, report: Report) -> go.Figure:
        fig = go.Figure()
        for portfolio_id in report.portfolio_ids():
            series = _by_start_date_series(report, portfolio_id, "cagr", self._min_trades)
            fig.add_trace(go.Scatter(x=series.index, y=series.values, mode="lines", name=portfolio_id))
        fig.add_hline(y=0, line_width=1)
        fig.update_layout(title=self.title, xaxis_title="Start Date", yaxis_tickformat=".0%", template="plotly_white")
        return fig


@Registry.register(TearsheetChart, "sharpe_by_start_date")
class SharpeByStartDateChart(TearsheetChart):
    def __init__(self, min_trades: int = 252):
        self._min_trades = min_trades
        self.title = "Sharpe by Start Date"

    def render(self, report: Report) -> go.Figure:
        fig = go.Figure()
        for portfolio_id in report.portfolio_ids():
            series = _by_start_date_series(report, portfolio_id, "sharpe", self._min_trades)
            fig.add_trace(go.Scatter(x=series.index, y=series.values, mode="lines", name=portfolio_id))
        fig.add_hline(y=0, line_width=1)
        fig.update_layout(title=self.title, xaxis_title="Start Date", template="plotly_white")
        return fig


@Registry.register(TearsheetChart, "monthly_return_by_start_date")
class MonthlyReturnByStartDateChart(TearsheetChart):
    def __init__(self, min_trades: int = 252):
        self._min_trades = min_trades
        self.title = "Geometric Monthly Return by Start Date"

    def render(self, report: Report) -> go.Figure:
        fig = go.Figure()
        for portfolio_id in report.portfolio_ids():
            series = _by_start_date_series(report, portfolio_id, "monthly_return", self._min_trades)
            fig.add_trace(go.Scatter(x=series.index, y=series.values, mode="lines", name=portfolio_id))
        fig.add_hline(y=0, line_width=1)
        fig.update_layout(title=self.title, xaxis_title="Start Date", yaxis_tickformat=".1%", template="plotly_white")
        return fig


@Registry.register(TearsheetChart, "final_value_by_start_date")
class FinalValueByStartDateChart(TearsheetChart):
    title = "Final Portfolio Value by Start Date"

    def render(self, report: Report) -> go.Figure:
        fig = go.Figure()
        for portfolio_id in report.portfolio_ids():
            series = _by_start_date_series(report, portfolio_id, "final_value")
            fig.add_trace(go.Scatter(x=series.index, y=series.values, mode="lines", name=portfolio_id))
        fig.update_layout(title=self.title, xaxis_title="Start Date", yaxis_tickprefix="$", yaxis_tickformat=",", template="plotly_white")
        return fig


@Registry.register(TearsheetChart, "sharpe_difference_by_start_date")
class SharpeDifferenceByStartDateChart(TearsheetChart):
    """Every non-benchmark portfolio's Sharpe-by-start-date MINUS the
    benchmark's own -- "is the strategy's edge over the benchmark stable
    across start dates, or does it depend on when you happened to start."
    `benchmark_id` defaults to the LAST portfolio id (alphabetically) when
    omitted, which only makes sense for a simple 2-portfolio comparison --
    pass it explicitly (SharpeDifferenceByStartDateChart(benchmark_id=...))
    for anything else."""

    def __init__(self, benchmark_id: Optional[str] = None, min_trades: int = 252):
        self._benchmark_id = benchmark_id
        self._min_trades = min_trades
        self.title = "Sharpe Difference by Start Date"

    def render(self, report: Report) -> go.Figure:
        portfolio_ids = report.portfolio_ids()
        benchmark_id = self._benchmark_id or (portfolio_ids[-1] if len(portfolio_ids) > 1 else None)

        fig = go.Figure()
        if benchmark_id is None:
            fig.add_annotation(
                text="Needs at least 2 portfolios (or an explicit benchmark_id)",
                showarrow=False, xref="paper", yref="paper", x=0.5, y=0.5,
            )
            fig.update_layout(title=self.title, template="plotly_white")
            return fig

        benchmark_sharpe = _by_start_date_series(report, benchmark_id, "sharpe", self._min_trades)
        for portfolio_id in portfolio_ids:
            if portfolio_id == benchmark_id:
                continue
            strategy_sharpe = _by_start_date_series(report, portfolio_id, "sharpe", self._min_trades)
            diff = (strategy_sharpe - benchmark_sharpe).dropna()
            fig.add_trace(go.Scatter(x=diff.index, y=diff.values, mode="lines", name=f"{portfolio_id} − {benchmark_id}"))
        fig.add_hline(y=0, line_width=1)
        fig.update_layout(title=self.title, xaxis_title="Start Date", template="plotly_white")
        return fig


@Registry.register(TearsheetChart, "rolling_sortino")
class RollingSortinoChart(TearsheetChart):
    """Same shape as RollingSharpeChart, but the denominator is downside
    deviation (std of negative returns only in the window) instead of full
    std -- doesn't penalize upside volatility the way Sharpe does."""

    def __init__(self, window_days: int = 126):
        self._window_days = window_days
        self.title = f"Rolling Sortino ({window_days}d)"

    def render(self, report: Report) -> go.Figure:
        def sortino(window: pd.Series) -> float:
            downside = window[window < 0]
            downside_std = downside.std()
            return window.mean() / downside_std * (252**0.5) if downside_std else 0.0

        fig = go.Figure()
        for portfolio_id in report.portfolio_ids():
            returns = _returns(report, portfolio_id)
            rolling = returns.rolling(self._window_days).apply(sortino)
            fig.add_trace(go.Scatter(x=rolling.index, y=rolling.values, mode="lines", name=portfolio_id))
        fig.add_hline(y=0, line_width=1)
        fig.update_layout(title=self.title, template="plotly_white")
        return fig


@Registry.register(TearsheetChart, "monthly_returns_heatmap")
class MonthlyReturnsHeatmapChart(TearsheetChart):
    """Year x month grid of one portfolio's own monthly returns -- inherently
    single-portfolio (a grid per portfolio would need its own subplot, not
    just another trace on the same axes), so pick which one with
    `portfolio_id` (defaults to the Report's first)."""

    def __init__(self, portfolio_id: Optional[str] = None, colorscale=RETURN_COLORSCALE):
        self._portfolio_id = portfolio_id
        self._colorscale = colorscale
        self.title = "Monthly Returns (%)"

    def render(self, report: Report) -> go.Figure:
        portfolio_id = self._portfolio_id or report.portfolio_ids()[0]
        monthly = _returns(report, portfolio_id).add(1).resample("ME").prod().sub(1)

        table = monthly.to_frame("return")
        table["year"] = table.index.year
        table["month"] = table.index.month
        pivot = table.pivot(index="year", columns="month", values="return").reindex(columns=range(1, 13))
        month_labels = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        values = pivot.to_numpy() * 100
        text = [[f"{v:.1f}" if pd.notna(v) else "" for v in row] for row in values]

        fig = go.Figure(
            go.Heatmap(
                z=values, x=month_labels, y=[str(y) for y in pivot.index],
                colorscale=self._colorscale, zmid=0, text=text, texttemplate="%{text}",
                hovertemplate="%{y} %{x}: %{z:.2f}%<extra></extra>",
            )
        )
        fig.update_layout(title=f"{self.title} — {portfolio_id}", template="plotly_white")
        return fig


@Registry.register(TearsheetChart, "return_quantiles")
class ReturnQuantilesChart(TearsheetChart):
    """Box plot of each portfolio's own return distribution at 5 different
    compounding frequencies (daily/weekly/monthly/quarterly/yearly) side by
    side -- how much a strategy's apparent variability shrinks (or doesn't)
    as you zoom out from daily to yearly returns."""

    title = "Return Quantiles"
    _FREQUENCIES = [("D", "Daily"), ("W", "Weekly"), ("ME", "Monthly"), ("QE", "Quarterly"), ("YE", "Yearly")]

    def render(self, report: Report) -> go.Figure:
        fig = go.Figure()
        for portfolio_id in report.portfolio_ids():
            returns = _returns(report, portfolio_id)
            for freq, label in self._FREQUENCIES:
                resampled = returns if freq == "D" else returns.add(1).resample(freq).prod().sub(1)
                fig.add_trace(
                    go.Box(
                        y=resampled.values * 100, name=label,
                        legendgroup=portfolio_id, offsetgroup=portfolio_id,
                        marker_color=None, hovertext=portfolio_id,
                    )
                )
        fig.update_layout(title=self.title, yaxis_title="Return (%)", boxmode="group", template="plotly_white")
        return fig


@Registry.register(TearsheetChart, "worst_drawdown_periods")
class WorstDrawdownPeriodsChart(TearsheetChart):
    """One portfolio's own cumulative-return curve, with its `n_periods`
    deepest contiguous underwater periods shaded -- where the Underwater
    Plot shows drawdown magnitude over time, this shows those same worst
    stretches directly against the equity curve they came from. Inherently
    single-portfolio (shading is meaningless without a single curve to
    shade against) -- pick which with `portfolio_id` (defaults to the
    Report's first)."""

    def __init__(self, portfolio_id: Optional[str] = None, n_periods: int = 5):
        self._portfolio_id = portfolio_id
        self._n_periods = n_periods
        self.title = f"Worst {n_periods} Drawdown Periods"

    def render(self, report: Report) -> go.Figure:
        portfolio_id = self._portfolio_id or report.portfolio_ids()[0]
        curve = report.equity_curve(portfolio_id)
        curve = curve.set_axis(pd.to_datetime(curve.index))
        drawdown = curve / curve.cummax() - 1.0

        periods, start = [], None
        for current_date, value in drawdown.items():
            underwater = value < 0
            if underwater and start is None:
                start = current_date
            elif not underwater and start is not None:
                periods.append((start, current_date))
                start = None
        if start is not None:
            periods.append((start, drawdown.index[-1]))

        periods_with_depth = [(s, e, drawdown.loc[s:e].min()) for s, e in periods]
        worst = sorted(periods_with_depth, key=lambda period: period[2])[: self._n_periods]

        normalized = curve / curve.iloc[0] - 1.0
        fig = go.Figure(go.Scatter(x=normalized.index, y=normalized.values, mode="lines", name=portfolio_id))
        for s, e, _depth in worst:
            fig.add_vrect(x0=s, x1=e, fillcolor="red", opacity=0.15, line_width=0)
        fig.update_layout(title=f"{self.title} — {portfolio_id}", yaxis_tickformat=".0%", template="plotly_white")
        return fig


ALL_CHARTS = DEFAULT_CHARTS + [
    "return_distribution_by_start_date",
    "worst_drawdown_paths",
    "max_drawdown_by_start_date",
    "cagr_by_start_date",
    "sharpe_by_start_date",
    "monthly_return_by_start_date",
    "final_value_by_start_date",
    "sharpe_difference_by_start_date",
    "rolling_sortino",
    "rolling_return",
    "rolling_return_heatmap",
    "return_matrix",
    "monthly_returns",
    "monthly_returns_heatmap",
    "return_quantiles",
    "worst_drawdown_periods",
]
"""Every registered built-in chart, DEFAULT_CHARTS plus everything opt-in --
convenience for `Tearsheet(charts=ALL_CHARTS)`/`charts=ALL_CHARTS + [...]`
when you want the full built-in set rather than picking individually. A few
of these (sharpe_difference_by_start_date, monthly_returns_heatmap,
worst_drawdown_periods) pick a default portfolio/benchmark when there's more
than 2 -- construct your own instance with an explicit portfolio_id/
benchmark_id instead of the registered id if that default is wrong for your
report."""


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


@Registry.register(TearsheetMetric, "sortino")
class SortinoMetric(TearsheetMetric):
    """Same shape as SharpeMetric, but the denominator is downside deviation
    (std of negative returns only) instead of full std -- doesn't penalize
    upside volatility."""

    label, format = "Sortino", "ratio"

    def compute(self, report: Report, portfolio_id: str) -> float:
        returns = _returns(report, portfolio_id)
        downside_std = returns[returns < 0].std()
        if not downside_std:
            return 0.0
        return float(returns.mean() / downside_std * (252**0.5))


@Registry.register(TearsheetMetric, "skew")
class SkewMetric(TearsheetMetric):
    label, format = "Skew", "ratio"

    def compute(self, report: Report, portfolio_id: str) -> float:
        return float(_returns(report, portfolio_id).skew())


@Registry.register(TearsheetMetric, "kurtosis")
class KurtosisMetric(TearsheetMetric):
    label, format = "Kurtosis", "ratio"

    def compute(self, report: Report, portfolio_id: str) -> float:
        return float(_returns(report, portfolio_id).kurt())


@Registry.register(TearsheetMetric, "value_at_risk")
class ValueAtRiskMetric(TearsheetMetric):
    label, format = "Daily VaR (95%)", "pct"

    def compute(self, report: Report, portfolio_id: str) -> float:
        return float(_returns(report, portfolio_id).quantile(0.05))


@Registry.register(TearsheetMetric, "expected_shortfall")
class ExpectedShortfallMetric(TearsheetMetric):
    """Reuses tam.basket.factors.ExpectedShortfall's own computation."""

    label, format = "Expected Shortfall (95%)", "pct"

    def compute(self, report: Report, portfolio_id: str) -> float:
        returns = _factor_input(report, portfolio_id)
        as_of = returns.index[-1]
        return float(ExpectedShortfall(window_days=len(returns), confidence=0.95).compute(returns, as_of)[portfolio_id])


DEFAULT_METRICS = ["total_return", "cagr", "sharpe", "volatility", "max_drawdown", "calmar", "num_trades"]

ALL_METRICS = DEFAULT_METRICS + ["sortino", "skew", "kurtosis", "value_at_risk", "expected_shortfall"]
"""Every registered built-in metric -- see ALL_CHARTS's own docstring."""

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


def _display(html: str, height: int):
    """An <iframe>, not IPython.display.HTML(html) directly -- a tearsheet
    embeds MULTIPLE Plotly figures, each its own <script>Plotly.newPlot(...)
    </script> call; browsers generally don't execute <script> tags inserted
    into a notebook cell's output via raw HTML display beyond (unreliably)
    the first one, so most charts after the first would silently never
    render. An iframe's own document has its own independent script-
    execution context, so every chart renders correctly regardless of the
    outer notebook frontend's HTML-injection quirks."""
    import base64

    from IPython.display import IFrame

    encoded = base64.b64encode(html.encode("utf-8")).decode("ascii")
    return IFrame(src=f"data:text/html;base64,{encoded}", width="100%", height=height)


def show(report: Report, height: int = 1200, **kwargs):
    """Inline display in a notebook, e.g. `tearsheet.show(report)` as a cell's
    last expression -- an <iframe> (see _display()'s docstring for why, not a
    plain IPython.display.HTML) of the same build_tearsheet() output
    write_html() saves to a file. `height` sets the iframe's own height in
    pixels (it scrolls internally, same as any other iframe) -- a tall
    charts-stacked-in-a-column layout usually wants more than the default
    1200 if you've added several extra charts."""
    return _display(build_tearsheet(report, **kwargs), height)


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

    def show(self, report: Report, height: int = 1200):
        return _display(self.build(report), height)

    def write(self, report: Report, path: str) -> Path:
        out_path = Path(path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(self.build(report))
        return out_path
