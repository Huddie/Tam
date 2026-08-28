"""General-purpose composable charting: any Chart subclass is callable
with data and returns a ChartCall that renders inline in a Jupyter cell
(last-expression rich display) or via .show(); chain multiple with | to
produce a single composite figure.

    class MyChart(Chart):
        title = "My Chart"
        def render(self, data) -> go.Figure: ...   # `data` is whatever YOUR chart expects

    MyChart()(my_data)             # a ChartCall -- auto-displays in Jupyter
    MyChart()(my_data).show()      # explicit .show()
    c1(data) | c2(data)            # a ChartPipeline -- one composite figure

Chart is deliberately NOT tied to tam.backtest.report.Report (or anything
else) -- `render(self, data)` takes whatever shape of `data` that
PARTICULAR chart needs, decided entirely by the subclass. Report happens
to be the right shape for tam.backtest.tearsheet's own equity-curve
charts (that module's own concrete Chart subclasses take a Report and are
driven directly by build_tearsheet(), never through __call__/ChartCall at
all) -- but a generic chart like TimeSeriesChart below has no reason to
require one, and RectChart below has no curves at all (just date ranges),
which wouldn't fit into a Report's "named curves" shape in the first
place. ChartCall.render() passes `data` straight through to
Chart.render(data), no conversion.

`_SeriesInput` (accepted by TimeSeriesChart, and any chart author who
wants the same convenience) may be any of:
    - pd.Series             (one named curve; .name used as its key)
    - Dict[str, pd.Series]  (explicit {name: curve} mapping)
    - pd.DataFrame          (one named curve per column)
    - List[pd.Series]       (each item's own .name used as its key)

_to_curves() normalizes any of these into a plain {name: pd.Series} dict
-- the same shapes tam.backtest.report.Report.from_curves() itself
accepts, but returned as a plain dict since nothing here needs a Report's
other machinery (drawdown_curve(), summary(), trade markers, ...).

timeseries() is the plot-anything entry point built on _to_curves(): raw
series with NO return/drawdown normalization applied (unlike
tam.backtest.tearsheet's own equity-curve-semantic charts, which live in
that module because they're genuinely backtest-specific -- this module
holds only the generic composition/display machinery and a couple of
generic charts, so a raw price series, an indicator overlay, or a FRED
series can be plotted without importing anything backtest-related):

    from tam.charting import timeseries, rect
    timeseries([close, sma(close, 20), sma(close, 50)])
    timeseries(price_series, title="Price") | timeseries(rsi_series, title="RSI")
    timeseries(spy) | rect(divergence_blocks, title="Divergence") | timeseries(yield_)
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple, Union

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from .registry import Registry

_SeriesInput = Union[pd.Series, Dict[str, pd.Series], pd.DataFrame, List[pd.Series]]


def _to_curves(series: _SeriesInput) -> Dict[str, pd.Series]:
    """Normalize any accepted series shape into a plain {name: pd.Series}
    dict -- the same shapes tam.backtest.report.Report.from_curves()
    itself accepts (a single Series, a {name: series} dict, a wide
    DataFrame, or a list of Series), but without needing a Report at all.
    Accepts a single (optionally named) pd.Series, a {name: series} dict, a
    wide DataFrame (one column per name), or a plain list of pd.Series --
    each one's own .name becomes its key (e.g. tam.strategy.indicators.sma()
    already comes back named "sma_20", so `timeseries([close, sma(close,
    20)])` just works with no manual renaming); an unnamed one in a list
    falls back to "series_N", same leniency as a single bare Series
    falling back to "portfolio"."""
    if isinstance(series, pd.Series):
        name = series.name or "portfolio"
        return {str(name): series}
    if isinstance(series, (list, tuple)):
        curves: Dict[str, pd.Series] = {}
        for item in series:
            if not isinstance(item, pd.Series):
                raise TypeError(f"each item in a list passed to a chart must be a pd.Series, got {type(item).__name__}")
            curves[str(item.name) if item.name is not None else f"series_{len(curves)}"] = item
        return curves
    if isinstance(series, pd.DataFrame):
        return {str(name): series[name] for name in series.columns}
    return dict(series)


class Chart(ABC):
    """One chart panel -- a standalone go.Figure built from whatever `data`
    shape THIS chart needs (see module docstring -- deliberately not tied
    to Report or any other single shape). Every chart is directly
    callable: pass data and get back a ChartCall that renders inline in
    Jupyter or via .show(). Chain multiple calls with | to produce a
    single composite figure:

        timeseries(my_series) | rect(my_regions)
    """

    title: str = ""

    @abstractmethod
    def render(self, data: Any) -> go.Figure: ...

    def __call__(self, data: Any, *, axis: str = "left", layer: Optional[int] = None) -> ChartCall:
        """Wrap `data` + this chart into a ChartCall. `data`'s shape is
        whatever this Chart's own render() expects -- see that method's
        docstring/type hint. `axis`/`layer` only matter when this call is
        combined with others via `&` (same-panel overlay) -- see
        ChartOverlay; piping with `|` (separate rows) ignores both. Omit
        `layer` (the common case) to have it auto-assigned by `&`'s own
        chain order instead of picking a number yourself."""
        return ChartCall(self, data, axis=axis, layer=layer)


class ChartCall:
    """A (chart, data) pair -- the result of calling a Chart with data.
    Renders as a standalone Plotly figure via .show() or as a Jupyter rich
    display (last-expression in a cell). Chain with `|` to produce a
    ChartPipeline that renders all charts as one composite figure with one
    row each; chain with `&` to produce a ChartOverlay that renders all
    charts into the SAME panel instead (see ChartOverlay)."""

    def __init__(self, chart: Chart, data: Any, *, axis: str = "left", layer: Optional[int] = None) -> None:
        self._chart = chart
        self._data = data
        self.axis = axis
        self.layer = 0 if layer is None else layer
        self._layer_explicit = layer is not None

    def _with_layer(self, layer: int) -> ChartCall:
        """A copy with `layer` overridden -- `_layer_explicit` is left as
        it was, so an auto-assigned layer stays open to being recomputed
        by a LATER `&` (e.g. combining two already-resolved overlays), while
        a layer the caller actually typed stays a fixed anchor forever."""
        copy = ChartCall(self._chart, self._data, axis=self.axis, layer=layer)
        copy._layer_explicit = self._layer_explicit
        return copy

    @property
    def title(self) -> str:
        return self._chart.title

    def render(self) -> go.Figure:
        """Build and return the Plotly figure for this chart."""
        return self._chart.render(self._data)

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

    def __or__(self, other: Union[ChartCall, ChartOverlay, ChartPipeline]) -> ChartPipeline:
        if isinstance(other, ChartPipeline):
            return ChartPipeline([self] + other._calls)
        return ChartPipeline([self, other])

    def __and__(self, other: Union[ChartCall, ChartOverlay]) -> ChartOverlay:
        if isinstance(other, ChartOverlay):
            return ChartOverlay(_resolve_layers([self] + other._calls))
        return ChartOverlay(_resolve_layers([self, other]))

    # Jupyter rich display protocol ----------------------------------------

    def _build_mimebundle(self, **kwargs):
        return self.render()._repr_mimebundle_(**kwargs)  # type: ignore[attr-defined]

    def _repr_mimebundle_(self, **kwargs):
        return self._build_mimebundle(**kwargs)


def _resolve_layers(calls: List[ChartCall]) -> List[ChartCall]:
    """Assigns a concrete `layer` to every call in order: one the caller
    explicitly passed (`layer=...` at construction) is a fixed anchor;
    one left unset becomes the PREVIOUS call's own resolved layer + 1 --
    "RHS is LHS.layer + 1" for a plain `a & b`, and each further `& c`
    keeps counting up from there. Explicit values are never touched.
    Re-run on every `&` (even one combining two already-resolved
    ChartOverlays) so unset layers always reflect their CURRENT position,
    not whatever position they happened to resolve to earlier."""
    resolved: List[ChartCall] = []
    previous = -1
    for call in calls:
        layer = call.layer if call._layer_explicit else previous + 1
        resolved.append(call if layer == call.layer else call._with_layer(layer))
        previous = layer
    return resolved


class ChartOverlay:
    """Multiple ChartCalls sharing ONE panel instead of one row each --
    created by chaining with `&` (as `|` creates a ChartPipeline):

        timeseries(spy) & rect(divergence_blocks, axis="left", layer=-1)
        timeseries(spy) & timeseries(yield_, axis="right")

    `axis` picks which y-axis a member's traces attach to ("left"/"right"
    -- "right" pulls in a secondary axis overlaying the left one, same
    convention as a hand-built dual-axis go.Figure). `layer` is a sort
    key controlling draw order among members SHARING an axis side (lower
    = added first = drawn further back) -- for a trace-bearing chart
    (e.g. timeseries) this gives true, arbitrary-depth ordering; for a
    shape-only chart (e.g. rect, which has no traces at all) Plotly can
    only express "below ALL traces" or "above ALL traces", not a precise
    position among them -- so a rect with `layer` below every trace-
    bearing member in the group renders as `layer="below"`, otherwise
    `layer="above"`. Leave `layer` unset (the default on timeseries()/
    rect()/any Chart.__call__) to have it auto-assigned by `&`'s own
    left-to-right order instead -- `a & b & c` puts them at layers 0, 1, 2
    with no manual numbering; pass `layer=` explicitly only to override
    that for one member (e.g. rect's own `layer=-1` to force it behind
    everything regardless of where it sits in the `&` chain). Composes
    further with `|`/`&` exactly like ChartCall (an overlay is one
    row-item, just like a single chart is)."""

    def __init__(self, calls: List[ChartCall]) -> None:
        self._calls = list(calls)

    @property
    def title(self) -> str:
        return next((c.title for c in self._calls if c.title), "")

    def __or__(self, other: Union[ChartCall, ChartOverlay, ChartPipeline]) -> ChartPipeline:
        if isinstance(other, ChartPipeline):
            return ChartPipeline([self] + other._calls)
        return ChartPipeline([self, other])

    def __and__(self, other: Union[ChartCall, ChartOverlay]) -> ChartOverlay:
        if isinstance(other, ChartOverlay):
            return ChartOverlay(_resolve_layers(self._calls + other._calls))
        return ChartOverlay(_resolve_layers(self._calls + [other]))

    def render(self) -> go.Figure:
        """Merge every member's own rendered figure into ONE panel,
        ordered by `layer` (ties broken by original order), each attached
        to its own `axis` side."""
        ordered = sorted(self._calls, key=lambda c: c.layer)
        subs = [(call, call.render()) for call in ordered]
        needs_secondary = any(c.axis == "right" for c, _ in subs)
        trace_layers = [c.layer for c, sub in subs if len(sub.data) > 0]

        fig = go.Figure()
        if needs_secondary:
            fig.update_layout(yaxis2=dict(overlaying="y", side="right"))

        for call, sub in subs:
            is_secondary = call.axis == "right"
            has_traces = len(sub.data) > 0

            for trace in sub.data:
                if is_secondary:
                    trace.update(yaxis="y2")
                fig.add_trace(trace)

            for shape in sub.layout.shapes:
                shape_dict = shape.to_plotly_json()
                shape_dict.pop("xref", None)
                shape_dict.pop("yref", None)
                if trace_layers:
                    shape_dict["layer"] = "below" if call.layer <= min(trace_layers) else "above"
                fig.add_shape(shape_dict)

            if not has_traces:
                # A shape-only member (e.g. rect) has no real data on this
                # axis -- its own axis styling (visible=False, a dummy 0-1
                # range, ...) is only meaningful for ITS OWN standalone
                # panel; copying it here would overwrite a REAL axis shared
                # with data-bearing members like timeseries(). Confirmed
                # live: without this guard, rect's hidden/dummy axis
                # silently hid the SPY axis it was meant to shade behind.
                continue

            src_layout = sub.layout.to_plotly_json()
            src_xaxis = src_layout.get("xaxis", {})
            if src_xaxis:
                fig.layout.xaxis.update({k: v for k, v in src_xaxis.items() if k not in ("domain", "anchor")})
            target_y_key = "yaxis2" if is_secondary else "yaxis"
            src_yaxis = src_layout.get("yaxis", {})
            if src_yaxis:
                excluded = ("domain", "anchor", "overlaying", "side")
                getattr(fig.layout, target_y_key).update({k: v for k, v in src_yaxis.items() if k not in excluded})

        fig.update_layout(title=self.title, template="plotly_white", showlegend=True)
        return fig

    def show(self) -> None:
        self.render().show()

    def to_html(self, *args, **kwargs) -> str:
        return self.render().to_html(*args, **kwargs)

    def _repr_mimebundle_(self, **kwargs):
        return self.render()._repr_mimebundle_(**kwargs)  # type: ignore[attr-defined]


class ChartPipeline:
    """An ordered sequence of ChartCalls rendered as one composite Plotly
    figure (one subplot row per chart). Created by chaining ChartCalls with |:

        c1(series) | c2(series) | c3(series)

    Each chart's own traces AND shapes (add_vrect()/add_hrect() bands --
    RectChart has no traces at all, only shapes) are copied into the
    composite figure at its own row, so every layout knob (yaxis title,
    yaxis type, etc.) is preserved per-panel. Charts that already use
    Heatmap/Bar/Table traces copy faithfully -- only axis *domain* keys in
    the per-trace layout are rewritten; the traces themselves are
    untouched. A chart with its OWN secondary y-axis (a dual-axis overlay
    built with `yaxis2` on its traces, e.g. price vs. an inverted rate)
    keeps that secondary axis in its own row too -- detected per-row from
    whether that row's source figure declares a `yaxis2` at all. Each
    row-item may be a plain ChartCall (one chart, one row) or a
    ChartOverlay (several charts sharing that one row, see ChartOverlay)
    -- both expose the same `.render()`/`.title` contract, so this class
    doesn't need to know or care which it's holding."""

    def __init__(self, calls: List[Union[ChartCall, ChartOverlay]]) -> None:
        self._calls = list(calls)

    def __or__(self, other: Union[ChartCall, ChartOverlay, ChartPipeline]) -> ChartPipeline:
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

        titles = [c.title or "" for c in self._calls]
        # Determine specs: use "table" type for any chart whose sub-figure
        # contains a go.Table trace, "secondary_y" for any chart with its
        # own right-hand axis (e.g. a dual-axis overlay chart), else plain
        # "xy". We render each sub-figure first to inspect its shape.
        sub_figs = [call.render() for call in self._calls]
        specs = []
        for sub in sub_figs:
            has_table = any(isinstance(t, go.Table) for t in sub.data)
            spec = {"type": "table" if has_table else "xy"}
            if not has_table and "yaxis2" in sub.layout.to_plotly_json():
                spec["secondary_y"] = True
            specs.append([spec])

        composite = make_subplots(
            rows=n,
            cols=1,
            subplot_titles=titles,
            specs=specs,
            vertical_spacing=0.06,
        )

        for row_idx, sub in enumerate(sub_figs, start=1):
            for trace in sub.data:
                # A trace on the source figure's OWN secondary axis (yaxis="y2")
                # needs secondary_y=True here too, or it lands on this row's
                # PRIMARY axis instead -- add_trace()'s row/col resolution
                # doesn't know to look at the trace's own yaxis attribute.
                is_secondary = getattr(trace, "yaxis", None) == "y2"
                composite.add_trace(trace, row=row_idx, col=1, secondary_y=is_secondary)

            # add_vrect()/add_hrect() live in layout.shapes, not fig.data --
            # RectChart's whole output is exactly this (no traces at all).
            # Re-adding via add_shape(..., row=, col=) lets Plotly recompute
            # the right xref/yref for THIS row itself; passing through the
            # original xref/yref instead would anchor every shape to row 1's
            # axes regardless of which row it actually came from.
            for shape in sub.layout.shapes:
                shape_dict = shape.to_plotly_json()
                shape_dict.pop("xref", None)
                shape_dict.pop("yref", None)
                composite.add_shape(shape_dict, row=row_idx, col=1)

            # Copy per-subplot layout props (yaxis titles, types, tick formats)
            # from the source figure's single-panel layout into the right slot
            # of the composite. get_subplot() (not a hand-computed "yaxis{i}"
            # suffix) is what correctly finds that slot regardless of row --
            # confirmed live: a secondary_y row shifts EVERY later row's axis
            # numbering (row 2 becomes yaxis3, not yaxis2, once row 1 claims
            # yaxis2 for its own secondary axis), so a fixed suffix scheme
            # breaks the moment any earlier row has a secondary axis at all.
            src_layout = sub.layout.to_plotly_json()
            subplot = composite.get_subplot(row=row_idx, col=1)
            if hasattr(subplot, "xaxis"):  # SubplotXY -- not a table cell (SubplotDomain has no axes)
                for axis_obj, src_key in ((subplot.xaxis, "xaxis"), (subplot.yaxis, "yaxis")):
                    src_axis = src_layout.get(src_key, {})
                    if src_axis:
                        axis_obj.update({k: v for k, v in src_axis.items() if k not in ("domain", "anchor")})

            secondary_subplot = composite.get_subplot(row=row_idx, col=1, secondary_y=True)
            if secondary_subplot is not None:
                src_axis = src_layout.get("yaxis2", {})
                if src_axis:
                    # "overlaying" is position-dependent (which primary axis
                    # THIS row's secondary axis sits on top of) -- make_subplots
                    # already got that right when secondary_y=True was set in
                    # specs; copying the source figure's own "yaxis2" would
                    # overwrite it with a value meaningful only in a single,
                    # standalone (non-composite) figure.
                    excluded = ("domain", "anchor", "overlaying")
                    secondary_subplot.yaxis.update({k: v for k, v in src_axis.items() if k not in excluded})

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

    def render(self, series: _SeriesInput) -> go.Figure:
        fig = go.Figure()
        for name, curve in _to_curves(series).items():
            fig.add_trace(go.Scatter(x=curve.index, y=curve.values, mode="lines", name=name))
        fig.update_layout(title=self.title, template="plotly_white")
        return fig


def timeseries(series: _SeriesInput, title: str = "Time Series", *, axis: str = "left", layer: Optional[int] = None) -> ChartCall:
    """The standalone/composable entry point for plotting raw series
    together -- same call/compose contract as every Chart here (this
    module's own docstring above covers the general pattern):

        timeseries(close)                                     # one line, uses close.name
        timeseries([close, sma(close, 20), sma(close, 50)])    # several, each using its own .name
        timeseries({"SPY": close, "SMA 20": sma_20})           # explicit names
        timeseries(price_series, title="Price") | timeseries(rsi_series, title="RSI")  # two rows, one figure
        timeseries(spy) & timeseries(yield_, axis="right")     # ONE row, dual y-axis overlay (see ChartOverlay)

    Different scales (e.g. price vs. a 0-100 RSI) can go on SEPARATE rows
    chained with `|`, or on the SAME row via `&` with `axis="right"` for
    one of them (a dual-axis overlay) -- `axis`/`layer` only matter for
    the latter; see ChartOverlay for what they control."""
    return TimeSeriesChart(title=title)(series, axis=axis, layer=layer)


_Region = Tuple[Any, Any]


@Registry.register(Chart, "rect")
class RectChart(Chart):
    """A thin panel of shaded vertical bands ONLY, no curves -- for
    composing alongside timeseries() panels via `|` (its own row) or `&`
    (shaded directly behind/in-front-of another chart in the SAME row,
    see ChartOverlay). `rect()` below is the ergonomic standalone entry
    point most callers should use instead of constructing this directly."""

    def __init__(self, title: str = "", color: str = "red", opacity: float = 0.2):
        self.title = title
        self._color = color
        self._opacity = opacity

    def render(self, regions: List[_Region]) -> go.Figure:
        fig = go.Figure()
        for start, end in regions:
            fig.add_vrect(x0=start, x1=end, fillcolor=self._color, opacity=self._opacity, line_width=0)
        fig.update_layout(
            title=self.title,
            template="plotly_white",
            yaxis=dict(visible=False, showticklabels=False, range=[0, 1]),
        )
        return fig


def rect(
    regions: List[_Region], title: str = "", color: str = "red", opacity: float = 0.2, *, axis: str = "left", layer: Optional[int] = None
) -> ChartCall:
    """A composable shaded-region panel -- date ranges (divergence
    episodes, regimes, recessions, ...) to mark on a chart:

        timeseries(spy) | rect(divergence_blocks, title="Divergence") | timeseries(yield_)   # own row
        timeseries(spy) & rect(divergence_blocks, layer=-1)                                  # shaded BEHIND spy's own lines

    `regions` is a list of (start, end) tuples -- anything Plotly accepts
    as an x-axis value (dates, timestamps, numbers). Piped with `|` this
    gets its OWN row; combined with `&` instead, it shares a row with
    whatever else is in that ChartOverlay (typically what you want for
    "shade behind these specific lines" rather than a separate strip)."""
    return RectChart(title=title, color=color, opacity=opacity)(regions, axis=axis, layer=layer)


def _contiguous_ranges(flags: pd.Series) -> List[_Region]:
    """[(start, end), ...] for each maximal run of consecutive True values
    in `flags` (a boolean Series indexed by date/time) -- e.g. the ranges
    where two series have "diverged," ready to feed straight into
    rect()."""
    ranges: List[_Region] = []
    start = None
    prev_index = None
    for index, flag in flags.items():
        if flag and start is None:
            start = index
        elif not flag and start is not None:
            ranges.append((start, prev_index))
            start = None
        prev_index = index
    if start is not None:
        ranges.append((start, prev_index))
    return ranges


class DivergenceAlg(ABC):
    """Interface: given two aligned series, find the date ranges where
    they've diverged "heavily" from each other -- see find_divergence(),
    the standalone entry point most callers should use instead of calling
    a DivergenceAlg directly."""

    @abstractmethod
    def find(self, a: pd.Series, b: pd.Series) -> List[_Region]: ...


@Registry.register(DivergenceAlg, "zscore")
class ZScoreDivergence(DivergenceAlg):
    """`a`/`b` are assumed to normally move OPPOSITE each other -- the same
    assumption baked into displaying one of them on an inverted secondary
    axis (e.g. SPY vs. bond yields: higher yields usually coincide with
    lower valuations). Flags ranges where their rolling z-scores instead
    move the SAME direction by more than `threshold` standard deviations,
    over a `window`-day rolling lookback. Raise `threshold` for fewer/
    more-extreme-only ranges, lower it for more/smaller ones; `window`
    controls how local the normalization is (shorter = more sensitive to
    recent moves)."""

    def __init__(self, threshold: float = 2.5, window: int = 252):
        self.threshold = threshold
        self.window = window

    def find(self, a: pd.Series, b: pd.Series) -> List[_Region]:
        aligned = pd.concat({"a": a, "b": b}, axis=1).ffill().dropna()
        z_a = self._rolling_z(aligned["a"])
        z_b = self._rolling_z(aligned["b"])
        diverging = (z_a + z_b).abs() > self.threshold
        return _contiguous_ranges(diverging)

    def _rolling_z(self, series: pd.Series) -> pd.Series:
        return (series - series.rolling(self.window).mean()) / series.rolling(self.window).std()


ZScoreDivergence.default = ZScoreDivergence()


def find_divergence(series_a: pd.Series, series_b: pd.Series, alg: Optional[DivergenceAlg] = None) -> List[_Region]:
    """Date ranges where `series_a`/`series_b` diverge "heavily" from each
    other, per `alg` (default: ZScoreDivergence.default) -- ready to feed
    straight into rect():

        rect(find_divergence(spy, yield_))
        rect(find_divergence(spy, yield_, ZScoreDivergence(threshold=1.5)))

    `alg` is any DivergenceAlg -- swap in a different registered
    implementation (`Registry.get(DivergenceAlg, "...")`) or your own
    subclass; "zscore" (ZScoreDivergence) is the only one built in."""
    alg = alg or ZScoreDivergence.default
    return alg.find(series_a, series_b)
