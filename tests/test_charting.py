"""tam/charting.py: the generic composable-chart API (Chart/ChartCall/
ChartPipeline, ChartOverlay, timeseries(), rect(), find_divergence()) --
deliberately tested here with NO dependency on tam.backtest's own chart
classes or Report, since the whole point of this module is that it
doesn't need either.
"""
from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go
import pytest

from tam.charting import (
    Chart,
    ChartCall,
    ChartOverlay,
    ChartPipeline,
    DivergenceAlg,
    RectChart,
    TimeSeriesChart,
    ZScoreDivergence,
    find_divergence,
    load_ipython_extension,
    rect,
    set_theme,
    timeseries,
)
import tam.charting as charting
from tam.registry import Registry


@pytest.fixture(autouse=True)
def _reset_theme():
    """set_theme() is deliberately global/process-wide (see its own
    docstring) -- reset it after every test so one test's %darkmode
    doesn't leak into the next test's assumptions about the default."""
    yield
    charting._current_theme = "light"


def _series(name="a", start_value=100.0):
    values = [start_value + i for i in range(20)]
    idx = [date(2024, 1, 1) + timedelta(days=i) for i in range(len(values))]
    return pd.Series(values, index=idx, name=name)


def test_timeseries_returns_a_chart_call():
    result = timeseries(_series())
    assert isinstance(result, ChartCall)


def test_timeseries_plots_raw_values_with_no_normalization():
    """The whole reason timeseries() exists separately from tam.backtest's
    equity-curve charts: values reach the figure completely unchanged --
    no return/drawdown/percent normalization applied."""
    series = _series(start_value=100.0)
    fig = timeseries(series).render()
    assert list(fig.data[0].y) == list(series.values)


def test_timeseries_uses_series_name_as_trace_name():
    fig = timeseries(_series(name="my_strategy")).render()
    assert fig.data[0].name == "my_strategy"


def test_timeseries_title_is_applied():
    fig = timeseries(_series(), title="Custom Title").render()
    assert fig.layout.title.text == "Custom Title"


def test_chart_call_render_returns_plotly_figure():
    call = timeseries(_series())
    fig = call.render()
    assert isinstance(fig, go.Figure)
    assert len(fig.data) > 0


def test_chart_call_accepts_dict_of_series():
    fig = timeseries({"a": _series("a"), "b": _series("b")}).render()
    assert len(fig.data) == 2


def test_chart_call_accepts_dataframe():
    df = pd.DataFrame({"a": _series("a"), "b": _series("b")})
    fig = timeseries(df).render()
    assert len(fig.data) == 2


def test_chart_call_accepts_list_of_series_using_each_ones_own_name():
    fig = timeseries([_series("a"), _series("b")]).render()
    trace_names = {t.name for t in fig.data}
    assert trace_names == {"a", "b"}


def test_chart_call_list_rejects_a_non_series_item():
    # Validation happens lazily, inside render() (via _to_curves()) --
    # timeseries()/ChartCall.__init__ just store the series, matching
    # every other Chart's own "cheap to construct, real work on render"
    # contract.
    with pytest.raises(TypeError):
        timeseries([_series("a"), "not a series"]).render()


def test_chart_call_pipe_returns_chart_pipeline():
    pipeline = timeseries(_series("a")) | timeseries(_series("b"))
    assert isinstance(pipeline, ChartPipeline)


def test_chart_pipeline_render_has_one_subplot_per_chart():
    pipeline = timeseries(_series("a"), title="A") | timeseries(_series("b"), title="B") | timeseries(_series("c"), title="C")
    fig = pipeline.render()
    assert hasattr(fig.layout, "yaxis3")


def test_chart_pipeline_chaining_with_or():
    p = timeseries(_series("a")) | timeseries(_series("b")) | timeseries(_series("c"))
    assert isinstance(p, ChartPipeline)
    assert len(p._calls) == 3


def test_pipeline_or_pipeline():
    p1 = timeseries(_series("a")) | timeseries(_series("b"))
    p2 = timeseries(_series("c")) | timeseries(_series("d"))
    combined = p1 | p2
    assert isinstance(combined, ChartPipeline)
    assert len(combined._calls) == 4


def test_chart_call_or_pipeline():
    single = timeseries(_series("a"))
    pipeline = timeseries(_series("b")) | timeseries(_series("c"))
    result = single | pipeline
    assert isinstance(result, ChartPipeline)
    assert len(result._calls) == 3


def test_single_call_pipeline_returns_direct_figure():
    """A pipeline with one chart should return its own figure unchanged."""
    call = timeseries(_series())
    pipeline = ChartPipeline([call])
    fig = pipeline.render()
    assert len(fig.data) == len(call.render().data)


def test_chart_call_repr_mimebundle_returns_dict():
    # _repr_mimebundle_ must return a dict so Jupyter can pick the right renderer.
    # Outside a real Jupyter kernel plotly returns an empty dict (no renderers
    # registered) -- the protocol itself (returning a dict) is what matters here.
    bundle = timeseries(_series())._repr_mimebundle_()
    assert isinstance(bundle, dict)


def test_chart_pipeline_repr_mimebundle_returns_dict():
    pipeline = timeseries(_series("a")) | timeseries(_series("b"))
    bundle = pipeline._repr_mimebundle_()
    assert isinstance(bundle, dict)


def test_timeseries_registered_under_the_chart_registry():
    """TimeSeriesChart -- the class timeseries() wraps -- is registered
    under the SAME Chart registry tam.backtest.tearsheet's own chart
    classes use, so tam.get(Chart, "timeseries") works the same way
    tam.get(Chart, "cumulative_returns") does."""
    resolved = Registry.get(Chart, "timeseries")
    assert isinstance(resolved, TimeSeriesChart)


def test_custom_chart_subclass_is_directly_usable():
    """The module's own documented extension pattern: a plain Chart
    subclass, with no registration and no tam.backtest/Report involvement
    at all, is immediately callable/composable like any built-in --
    render() takes whatever shape of data THIS chart wants (here, a plain
    pd.Series), not a Report."""

    class DoubledChart(Chart):
        title = "Doubled"

        def render(self, series: pd.Series) -> go.Figure:
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=series.index, y=series.values * 2, name=series.name))
            return fig

    series = _series(start_value=10.0)
    fig = DoubledChart()(series).render()
    assert list(fig.data[0].y) == [v * 2 for v in series.values]


def test_rect_returns_a_chart_call():
    result = rect([(date(2024, 1, 1), date(2024, 1, 5))])
    assert isinstance(result, ChartCall)


def test_rect_renders_no_data_traces_only_shapes():
    """RectChart's whole output lives in layout.shapes (add_vrect), not
    fig.data -- there are no curves to plot, only shaded ranges."""
    fig = rect([(date(2024, 1, 1), date(2024, 1, 5))]).render()
    assert len(fig.data) == 0
    assert len(fig.layout.shapes) == 1


def test_rect_renders_one_shape_per_region():
    regions = [(date(2024, 1, 1), date(2024, 1, 3)), (date(2024, 1, 10), date(2024, 1, 12))]
    fig = rect(regions).render()
    assert len(fig.layout.shapes) == 2


def test_rect_registered_under_the_chart_registry():
    resolved = Registry.get(Chart, "rect")
    assert isinstance(resolved, RectChart)


def test_rect_composes_with_timeseries_and_preserves_shapes_in_composite():
    """timeseries(...) | rect(...) | timeseries(...) -- rect's shapes must
    survive composition (a bug caught live: ChartPipeline.render() only
    copied fig.data traces, silently dropping any chart whose whole
    output lives in fig.layout.shapes instead, which is ALL of RectChart's
    output)."""
    pipeline = timeseries(_series("a")) | rect([(date(2024, 1, 1), date(2024, 1, 5))], title="Divergence") | timeseries(_series("b"))
    fig = pipeline.render()
    assert len(fig.layout.shapes) == 1
    # The middle row's own traces are empty, but the two timeseries rows still have theirs.
    assert len(fig.data) == 2


def test_rect_composed_via_pipe_uses_a_domain_relative_yref_not_a_data_coordinate():
    """Regression: add_shape(row=,col=) resolves yref to a bare data-
    coordinate reference (e.g. "y3"), not add_vrect()'s own default of
    "y3 domain" (0-1 fraction of the row's own height, independent of
    whatever scale that row's real data uses). Confirmed live: for a
    price series with values in the hundreds, a shape left at the bare
    data-coordinate y0=0/y1=1 became a razor-thin sliver near zero instead
    of shading the row's full height, and dragged the axis's own
    autorange around with it."""
    pipeline = timeseries(_series("a", start_value=500.0)) | rect([(date(2024, 1, 1), date(2024, 1, 5))])
    fig = pipeline.render()
    assert fig.layout.shapes[0].yref.endswith(" domain")


def test_and_returns_a_chart_overlay():
    overlay = timeseries(_series("a")) & timeseries(_series("b"))
    assert isinstance(overlay, ChartOverlay)


def test_overlay_renders_all_members_into_one_panel_not_separate_rows():
    overlay = timeseries(_series("a")) & timeseries(_series("b"))
    fig = overlay.render()
    assert len(fig.data) == 2
    # A single shared panel has no yaxis2/yaxis3 subplot-row axes at all -- both
    # traces share plain "yaxis" (or leave it unset, i.e. the primary axis).
    assert getattr(fig.data[0], "yaxis", None) in (None, "y")
    assert getattr(fig.data[1], "yaxis", None) in (None, "y")


def test_overlay_axis_right_puts_a_series_on_the_secondary_axis():
    overlay = timeseries(_series("a", start_value=100.0)) & timeseries(_series("b", start_value=5.0), axis="right")
    fig = overlay.render()
    yaxes = {t.name: t.yaxis for t in fig.data}
    assert yaxes["a"] in (None, "y")
    assert yaxes["b"] == "y2"
    assert fig.layout.yaxis2.overlaying == "y"
    assert fig.layout.yaxis2.side == "right"


def test_timeseries_invert_reverses_its_own_standalone_axis():
    fig = timeseries(_series("a"), invert=True).render()
    assert fig.layout.yaxis.autorange == "reversed"


def test_overlay_invert_flips_the_axis_a_member_actually_ends_up_on():
    """invert=True on the RIGHT-axis member must flip yaxis2, not the
    LEFT axis shared by the other member -- confirmed by asserting the
    left axis stays untouched."""
    overlay = timeseries(_series("a", start_value=100.0)) & timeseries(_series("b", start_value=5.0), axis="right", invert=True)
    fig = overlay.render()
    assert fig.layout.yaxis2.autorange == "reversed"
    assert fig.layout.yaxis.autorange is None


def test_overlay_does_not_let_a_shape_only_members_axis_styling_leak_onto_a_shared_real_axis():
    """Regression: RectChart's own standalone render() hides its axis
    (visible=False, a dummy [0,1] range) since that's meaningless on its
    own thin row -- but when overlaid via `&` with a real chart like
    timeseries() on the SAME axis, that hidden/dummy styling must NOT
    leak onto the shared axis and hide the real data's own axis too."""
    overlay = timeseries(_series("a")) & rect([(date(2024, 1, 1), date(2024, 1, 5))], layer=-1)
    fig = overlay.render()
    assert fig.layout.yaxis.visible is not False
    assert fig.layout.yaxis.range is None


def test_overlay_rect_uses_a_domain_relative_yref_not_a_data_coordinate():
    """Regression: dropping a shape's yref entirely (rather than
    restoring add_vrect()'s own "y domain" default) left it implicitly
    data-coordinate-referenced. For a real price series in the hundreds,
    that turned the shading into a razor-thin sliver near y=0 and pulled
    the shared axis's own autorange along with it, instead of shading the
    full panel height as intended."""
    overlay = timeseries(_series("a", start_value=500.0)) & rect([(date(2024, 1, 1), date(2024, 1, 5))], layer=-1)
    fig = overlay.render()
    assert fig.layout.shapes[0].yref == "y domain"
    assert fig.layout.yaxis.autorange is None
    assert fig.layout.yaxis.range is None


def test_overlay_rect_layer_below_all_traces_renders_as_shape_layer_below():
    overlay = timeseries(_series("a"), layer=0) & rect([(date(2024, 1, 1), date(2024, 1, 5))], layer=-1)
    fig = overlay.render()
    assert fig.layout.shapes[0].layer == "below"


def test_overlay_rect_layer_above_all_traces_renders_as_shape_layer_above():
    overlay = timeseries(_series("a"), layer=0) & rect([(date(2024, 1, 1), date(2024, 1, 5))], layer=1)
    fig = overlay.render()
    assert fig.layout.shapes[0].layer == "above"


def test_overlay_composes_with_pipe_as_one_row_item():
    """(a & b) | c -- the overlay occupies row 1 as ONE unit, c gets row 2."""
    pipeline = (timeseries(_series("a")) & timeseries(_series("b"))) | timeseries(_series("c"))
    assert isinstance(pipeline, ChartPipeline)
    assert len(pipeline._calls) == 2
    fig = pipeline.render()
    assert len(fig.data) == 3


def test_zscore_divergence_default_exists_and_is_the_default():
    assert isinstance(ZScoreDivergence.default, ZScoreDivergence)


def test_divergence_alg_registered_under_the_registry():
    resolved = Registry.get(DivergenceAlg, "zscore")
    assert isinstance(resolved, ZScoreDivergence)


def _opposing_series(n=80, break_at=40, break_len=10):
    """Two series that move opposite each other everywhere EXCEPT a
    deliberate window where they move the SAME direction -- the divergence
    find_divergence() should flag."""
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    a_vals, b_vals = [], []
    for i in range(n):
        in_break = break_at <= i < break_at + break_len
        a_vals.append(100 + i)
        b_vals.append((5 + i * 0.2) if in_break else (5 - i * 0.05))
    return pd.Series(a_vals, index=idx, name="a"), pd.Series(b_vals, index=idx, name="b")


def test_find_divergence_uses_zscore_default_and_flags_the_break_window():
    a, b = _opposing_series()
    regions = find_divergence(a, b, ZScoreDivergence(threshold=1.0, window=10))
    assert len(regions) >= 1
    start, end = regions[0]
    assert pd.Timestamp("2024-01-01") + pd.Timedelta(days=35) <= pd.Timestamp(start)


def test_find_divergence_with_no_alg_uses_the_default_instance():
    a, b = _opposing_series()
    default_result = find_divergence(a, b)
    explicit_result = find_divergence(a, b, ZScoreDivergence.default)
    assert default_result == explicit_result


def test_find_divergence_result_is_directly_usable_by_rect():
    a, b = _opposing_series()
    regions = find_divergence(a, b, ZScoreDivergence(threshold=1.0, window=10))
    fig = rect(regions).render()
    assert len(fig.layout.shapes) == len(regions)


def test_default_theme_is_light():
    fig = timeseries(_series()).render()
    assert fig.layout.template.layout.paper_bgcolor in (None, "white", "#FFFFFF")


def test_set_theme_dark_changes_every_subsequent_chart():
    set_theme("dark")
    fig = timeseries(_series()).render()
    assert fig.layout.paper_bgcolor == "#12121f"
    assert fig.layout.font.color == "white"


def test_set_theme_applies_across_pipeline_and_overlay_too():
    set_theme("dark")
    assert timeseries(_series("a")).render().layout.paper_bgcolor == "#12121f"
    pipeline_fig = (timeseries(_series("a")) | timeseries(_series("b"))).render()
    assert pipeline_fig.layout.paper_bgcolor == "#12121f"
    overlay_fig = (timeseries(_series("a")) & timeseries(_series("b"))).render()
    assert overlay_fig.layout.paper_bgcolor == "#12121f"


def test_set_theme_invalid_name_raises():
    with pytest.raises(ValueError):
        set_theme("neon")


def test_timeseries_color_applies_to_the_line():
    fig = timeseries(_series(), color="white").render()
    assert fig.data[0].line.color == "white"


def test_timeseries_no_color_leaves_plotlys_default_cycling():
    fig = timeseries(_series()).render()
    assert fig.data[0].line.color is None


class _FakeIPython:
    def __init__(self):
        self.registered = []

    def register_magic_function(self, func, magic_kind, magic_name):
        self.registered.append((func, magic_kind, magic_name))


def test_load_ipython_extension_registers_theme_magics_as_line_magics():
    ipython = _FakeIPython()

    load_ipython_extension(ipython)

    names = {name for _, _, name in ipython.registered}
    assert names == {"theme", "darkmode", "lightmode"}
    assert all(kind == "line" for _, kind, _ in ipython.registered)


def test_darkmode_magic_sets_the_dark_theme():
    ipython = _FakeIPython()
    load_ipython_extension(ipython)
    darkmode_magic = next(func for func, _, name in ipython.registered if name == "darkmode")

    darkmode_magic("")

    assert timeseries(_series()).render().layout.paper_bgcolor == "#12121f"


def test_lightmode_magic_resets_to_the_light_theme():
    set_theme("dark")
    ipython = _FakeIPython()
    load_ipython_extension(ipython)
    lightmode_magic = next(func for func, _, name in ipython.registered if name == "lightmode")

    lightmode_magic("")

    assert charting._current_theme == "light"


def test_theme_magic_parses_its_line_argument():
    ipython = _FakeIPython()
    load_ipython_extension(ipython)
    theme_magic = next(func for func, _, name in ipython.registered if name == "theme")

    theme_magic("dark")

    assert charting._current_theme == "dark"
