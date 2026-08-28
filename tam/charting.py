"""General-purpose composable charting: any Chart subclass is callable
with data and returns a ChartCall that renders inline in a Jupyter cell
(last-expression rich display) or via .show(); chain multiple with | to
produce a single composite figure.

    class MyChart(Chart):
        title = "My Chart"
        def render(self, report: Report) -> go.Figure: ...

    MyChart()(my_series)             # a ChartCall -- auto-displays in Jupyter
    MyChart()(my_series).show()      # explicit .show()
    c1(series) | c2(series)          # a ChartPipeline -- one composite figure

`series` accepted by a Chart's __call__ may be any of:
    - pd.Series             (one named curve; .name used as its key)
    - Dict[str, pd.Series]  (explicit {name: curve} mapping)
    - pd.DataFrame          (one named curve per column)
    - List[pd.Series]       (each item's own .name used as its key)

All four are wrapped into a tam.backtest.report.Report via
Report.from_curves() -- Report's own "named curves + derived analytics"
shape turns out to be exactly what any Chart.render(report) needs,
independent of whether those curves came from a real backtest.

timeseries() is the plot-anything entry point built on this: raw series
with NO return/drawdown normalization applied (unlike
tam.backtest.tearsheet's own equity-curve-semantic charts, which live in
that module because they're genuinely backtest-specific -- this module
holds only the generic composition/display machinery and the one generic
chart, so a raw price series, an indicator overlay, or a FRED series can
be plotted without importing anything backtest-related):

    from tam.charting import timeseries
    timeseries([close, sma(close, 20), sma(close, 50)])
    timeseries(price_series, title="Price") | timeseries(rsi_series, title="RSI")
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, List, Union

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from .backtest.report import Report
from .registry import Registry

_SeriesInput = Union[pd.Series, Dict[str, pd.Series], pd.DataFrame, List[pd.Series]]


def _to_report(series: _SeriesInput) -> Report:
    """Convert any accepted series shape into a Report for chart.render().
    Accepts a single (optionally named) pd.Series, a {name: series} dict, a
    wide DataFrame (one column per name), or a plain list of pd.Series --
    each one's own .name becomes its key (e.g. tam.strategy.indicators.sma()
    already comes back named "sma_20", so `timeseries([close, sma(close,
    20)])` just works with no manual renaming); an unnamed one in a list
    falls back to "series_N", same leniency as a single bare Series
    falling back to "portfolio"."""
    if isinstance(series, pd.Series):
        name = series.name or "portfolio"
        return Report.from_curves({str(name): series})
    if isinstance(series, (list, tuple)):
        curves: Dict[str, pd.Series] = {}
        for item in series:
            if not isinstance(item, pd.Series):
                raise TypeError(f"each item in a list passed to a chart must be a pd.Series, got {type(item).__name__}")
            curves[str(item.name) if item.name is not None else f"series_{len(curves)}"] = item
        return Report.from_curves(curves)
    return Report.from_curves(series)


class Chart(ABC):
    """One chart panel -- a standalone go.Figure built from a Report. Every
    chart is directly callable: pass a series/curves and get back a
    ChartCall that renders inline in Jupyter or via .show(). Chain multiple
    calls with | to produce a single composite figure:

        DrawdownChart()(my_series) | RollingSharpeChart()(my_series)
    """

    title: str = ""

    @abstractmethod
    def render(self, report: Report) -> go.Figure: ...

    def __call__(
        self,
        series: Union[pd.Series, Dict[str, pd.Series], pd.DataFrame],
    ) -> ChartCall:
        """Wrap `series` + this chart into a ChartCall. `series` may be a
        pd.Series (one portfolio, name preserved as portfolio id), a
        {name: series} dict, or a DataFrame (one column per portfolio)."""
        return ChartCall(self, series)


class ChartCall:
    """A (chart, series) pair -- the result of calling a Chart with data.
    Renders as a standalone Plotly figure via .show() or as a Jupyter rich
    display (last-expression in a cell). Chain with | to produce a
    ChartPipeline that renders all charts as one composite figure."""

    def __init__(self, chart: Chart, series: _SeriesInput) -> None:
        self._chart = chart
        self._series = series

    def render(self) -> go.Figure:
        """Build and return the Plotly figure for this chart."""
        return self._chart.render(_to_report(self._series))

    def show(self) -> None:
        """Display the figure (calls fig.show(), works in notebooks + scripts)."""
        self.render().show()

    def to_html(self, *args, **kwargs) -> str:
        """Passes through to the rendered go.Figure's own to_html() -- this
        is what makes `tam.discovery.upload(timeseries(...), ...)` work
        without an explicit `.render()` first: upload() duck-types any
        object with a to_html() method as a Figure (hasattr(x, "to_html")),
        and without this, a bare ChartCall/ChartPipeline would instead be
        (wrongly) treated as a file path."""
        return self.render().to_html(*args, **kwargs)

    def __or__(self, other: Union[ChartCall, ChartPipeline]) -> ChartPipeline:
        if isinstance(other, ChartPipeline):
            return ChartPipeline([self] + other._calls)
        return ChartPipeline([self, other])

    # Jupyter rich display protocol ----------------------------------------

    def _build_mimebundle(self, **kwargs):
        return self.render()._repr_mimebundle_(**kwargs)  # type: ignore[attr-defined]

    def _repr_mimebundle_(self, **kwargs):
        return self._build_mimebundle(**kwargs)


class ChartPipeline:
    """An ordered sequence of ChartCalls rendered as one composite Plotly
    figure (one subplot row per chart). Created by chaining ChartCalls with |:

        c1(series) | c2(series) | c3(series)

    Each chart's own traces are copied into the composite figure at its own
    row, so every layout knob (yaxis title, yaxis type, etc.) is preserved
    per-panel. Charts that already use Heatmap/Bar/Table traces copy faithfully
    -- only axis *domain* keys in the per-trace layout are rewritten; the
    traces themselves are untouched."""

    def __init__(self, calls: List[ChartCall]) -> None:
        self._calls = list(calls)

    def __or__(self, other: Union[ChartCall, ChartPipeline]) -> ChartPipeline:
        if isinstance(other, ChartPipeline):
            return ChartPipeline(self._calls + other._calls)
        return ChartPipeline(self._calls + [other])

    def render(self) -> go.Figure:
        """Build a single composite figure with one subplot row per chart."""
        n = len(self._calls)
        if n == 0:
            return go.Figure()
        if n == 1:
            return self._calls[0].render()

        titles = [c._chart.title or "" for c in self._calls]
        # Determine specs: use "table" type for any chart whose sub-figure
        # contains a go.Table trace, otherwise "xy". We render each sub-figure
        # first to inspect its trace types.
        sub_figs = [call.render() for call in self._calls]
        specs = []
        for sub in sub_figs:
            has_table = any(isinstance(t, go.Table) for t in sub.data)
            specs.append([{"type": "table" if has_table else "xy"}])

        composite = make_subplots(
            rows=n,
            cols=1,
            subplot_titles=titles,
            specs=specs,
            vertical_spacing=0.06,
        )

        for row_idx, sub in enumerate(sub_figs, start=1):
            for trace in sub.data:
                composite.add_trace(trace, row=row_idx, col=1)

            # Copy per-subplot layout props (yaxis titles, types, tick formats)
            # from the source figure's single-panel layout into the right slot
            # of the composite.  The source figure always has xaxis/yaxis (the
            # first pair); composite rows use xaxis{i}/yaxis{i}.
            src_layout = sub.layout.to_plotly_json()
            suffix = "" if row_idx == 1 else str(row_idx)
            for axis_base in ("xaxis", "yaxis"):
                src_axis = src_layout.get(axis_base, {})
                if src_axis:
                    # Drop domain/anchor keys -- make_subplots owns those.
                    src_axis = {k: v for k, v in src_axis.items() if k not in ("domain", "anchor")}
                    getattr(composite.layout, f"{axis_base}{suffix}").update(src_axis)

        composite.update_layout(
            template="plotly_white",
            height=max(350 * n, 600),
            showlegend=True,
        )
        return composite

    def show(self) -> None:
        """Display the composite figure."""
        self.render().show()

    def to_html(self, *args, **kwargs) -> str:
        """Same reasoning as ChartCall.to_html() -- lets a composed
        pipeline (c1(series) | c2(series)) go straight into
        tam.discovery.upload() too, not just a single ChartCall."""
        return self.render().to_html(*args, **kwargs)

    def _repr_mimebundle_(self, **kwargs):
        return self.render()._repr_mimebundle_(**kwargs)  # type: ignore[attr-defined]


@Registry.register(Chart, "timeseries")
class TimeSeriesChart(Chart):
    """Plots each named series RAW -- no return/drawdown normalization.
    `timeseries()` below is the ergonomic standalone entry point most
    callers should use instead of constructing this directly."""

    def __init__(self, title: str = "Time Series"):
        self.title = title

    def render(self, report: Report) -> go.Figure:
        fig = go.Figure()
        for portfolio_id in report.portfolio_ids():
            curve = report.equity_curve(portfolio_id)
            fig.add_trace(go.Scatter(x=curve.index, y=curve.values, mode="lines", name=portfolio_id))
        fig.update_layout(title=self.title, template="plotly_white")
        return fig


def timeseries(series: _SeriesInput, title: str = "Time Series") -> ChartCall:
    """The standalone/composable entry point for plotting raw series
    together -- same call/compose contract as every Chart here (this
    module's own docstring above covers the general pattern):

        timeseries(close)                                     # one line, uses close.name
        timeseries([close, sma(close, 20), sma(close, 50)])    # several, each using its own .name
        timeseries({"SPY": close, "SMA 20": sma_20})           # explicit names
        timeseries(price_series, title="Price") | timeseries(rsi_series, title="RSI")  # two rows, one figure

    Different scales (e.g. price vs. a 0-100 RSI) belong on SEPARATE calls
    chained with `|`, not lumped into one timeseries(...) call -- each
    ChartCall gets its own y-axis when composed into a ChartPipeline (see
    ChartPipeline.render()'s per-subplot axis handling), a plain overlay
    within one timeseries(...) call shares a single axis."""
    return TimeSeriesChart(title=title)(series)
