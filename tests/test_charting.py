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

from tam import charting
from tam.charting import (
    CandlestickChart,
    Chart,
    ChartCall,
    ChartOverlay,
    ChartPipeline,
    DistributionChart,
    DivergenceAlg,
    HeatmapChart,
    RectChart,
    TableChart,
    TimeSeriesChart,
    ZScoreDivergence,
    candles,
    distribution,
    find_divergence,
    heatmap,
    load_ipython_extension,
    rect,
    set_theme,
    table,
    timeseries,
)
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
    pipeline = (
        timeseries(_series("a"), title="A") | timeseries(_series("b"), title="B") | timeseries(_series("c"), title="C")
    )
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


def test_distribution_returns_a_chart_call():
    result = distribution(_series())
    assert isinstance(result, ChartCall)


def test_distribution_renders_a_single_histogram_trace():
    fig = distribution(_series()).render()
    assert len(fig.data) == 1
    assert fig.data[0].type == "histogram"


def test_distribution_uses_series_name_as_trace_name():
    fig = distribution(_series(name="my_scores")).render()
    assert fig.data[0].name == "my_scores"


def test_distribution_accepts_a_dict_of_series_and_overlays_them():
    fig = distribution(
        {"model": _series(name="model"), "baseline": _series(name="baseline", start_value=50.0)}
    ).render()
    assert len(fig.data) == 2
    assert {trace.name for trace in fig.data} == {"model", "baseline"}
    assert fig.layout.barmode == "overlay"


def test_distribution_single_series_has_no_overlay_barmode():
    fig = distribution(_series()).render()
    assert fig.layout.barmode is None


def test_distribution_bins_param_sets_nbinsx():
    fig = distribution(_series(), bins=42).render()
    assert fig.data[0].nbinsx == 42


def test_distribution_histnorm_passes_through_to_plotly():
    fig = distribution(_series(), histnorm="probability").render()
    assert fig.data[0].histnorm == "probability"


def test_distribution_title_is_applied():
    fig = distribution(_series(), title="Custom Title").render()
    assert fig.layout.title.text == "Custom Title"


def test_distribution_drops_nan_values():
    series = _series()
    series.iloc[3] = float("nan")
    fig = distribution(series).render()
    assert len(fig.data[0].x) == len(series) - 1


def test_distribution_composes_with_timeseries_via_pipe():
    pipeline = distribution(_series()) | timeseries(_series())
    fig = pipeline.render()
    assert len(fig.data) == 2


def test_distribution_registered_under_the_chart_registry():
    resolved = Registry.get(Chart, "distribution")
    assert isinstance(resolved, DistributionChart)


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
    pipeline = (
        timeseries(_series("a"))
        | rect([(date(2024, 1, 1), date(2024, 1, 5))], title="Divergence")
        | timeseries(_series("b"))
    )
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


def _leaderboard_df() -> pd.DataFrame:
    return pd.DataFrame({"feature": ["rsi_14", "macd"], "mean_ic": [0.0521, -0.0113], "hit_rate": [0.55, 0.48]})


def test_table_returns_a_chart_call():
    result = table(_leaderboard_df())
    assert isinstance(result, ChartCall)


def test_table_renders_a_go_table_trace_with_a_column_per_header():
    fig = table(_leaderboard_df()).render()
    assert len(fig.data) == 1
    assert isinstance(fig.data[0], go.Table)
    assert list(fig.data[0].header.values) == ["feature", "mean_ic", "hit_rate"]


def test_table_formats_float_columns_but_leaves_other_dtypes_as_plain_str():
    fig = table(_leaderboard_df()).render()
    mean_ic_cells, feature_cells = fig.data[0].cells.values[1], fig.data[0].cells.values[0]
    assert mean_ic_cells[0] == "0.0521"  # float64 -> float_format
    assert feature_cells[0] == "rsi_14"  # object dtype -> plain str()


def test_table_registered_under_the_chart_registry():
    resolved = Registry.get(Chart, "table")
    assert isinstance(resolved, TableChart)


def test_table_composes_with_timeseries_via_pipe():
    pipeline = timeseries(_series("a")) | table(_leaderboard_df())
    fig = pipeline.render()
    assert len(fig.data) == 2
    assert isinstance(fig.data[-1], go.Table)


def _corr_df() -> pd.DataFrame:
    return pd.DataFrame([[1.0, 0.3], [0.3, 1.0]], index=["ret_5d", "rsi_14"], columns=["ret_5d", "rsi_14"])


def test_heatmap_returns_a_chart_call():
    result = heatmap(_corr_df())
    assert isinstance(result, ChartCall)


def test_heatmap_renders_a_go_heatmap_trace_with_matrix_values():
    fig = heatmap(_corr_df()).render()
    assert len(fig.data) == 1
    assert isinstance(fig.data[0], go.Heatmap)
    assert list(fig.data[0].x) == ["ret_5d", "rsi_14"]
    assert list(fig.data[0].y) == ["ret_5d", "rsi_14"]
    assert fig.data[0].z[0][1] == pytest.approx(0.3)


def test_heatmap_defaults_to_a_zero_centered_color_scale():
    fig = heatmap(_corr_df()).render()
    assert fig.data[0].zmid == 0.0


def test_heatmap_zmid_can_be_disabled():
    fig = heatmap(_corr_df(), zmid=None).render()
    assert fig.data[0].zmid is None


def test_heatmap_registered_under_the_chart_registry():
    resolved = Registry.get(Chart, "heatmap")
    assert isinstance(resolved, HeatmapChart)


def test_heatmap_composes_with_timeseries_via_pipe():
    pipeline = timeseries(_series("a")) | heatmap(_corr_df())
    fig = pipeline.render()
    assert len(fig.data) == 2
    assert isinstance(fig.data[-1], go.Heatmap)


def test_heatmap_colorbar_is_off_by_default():
    """Regression: go.Heatmap's default colorbar sits at the figure's right
    edge -- the exact same place ChartPipeline's shared legend defaults to.
    Composed via | (e.g. ExperimentResult.report()), an on-by-default
    colorbar visually collided with the legend text, both unreadable.
    Each cell already annotates its own value directly, so the colorbar is
    redundant here anyway."""
    fig = heatmap(_corr_df()).render()
    assert fig.data[0].showscale is False


def test_heatmap_colorbar_can_be_enabled():
    fig = heatmap(_corr_df(), show_colorbar=True).render()
    assert fig.data[0].showscale is True


def test_pipeline_gives_table_rows_a_smaller_row_heights_share_than_chart_rows():
    """Regression: go.Table's header/cell heights are fixed pixel values,
    not stretched to fill an arbitrary subplot domain -- giving a table the
    SAME row share as an xy/heatmap row left it top-anchored with a large
    blank gap below it (confirmed live in ExperimentResult.report()'s
    composite, two ~100px tables each stranded inside a ~350px-tall row)."""
    pipeline = table(_leaderboard_df()) | timeseries(_series("a"))
    fig = pipeline.render()

    table_trace = next(t for t in fig.data if isinstance(t, go.Table))
    table_span = table_trace.domain.y[1] - table_trace.domain.y[0]
    chart_span = fig.layout.yaxis.domain[1] - fig.layout.yaxis.domain[0]
    assert table_span < chart_span


def _rendered_row_height_px(
    fig: go.Figure, row_domain_y: tuple[float, float], n: int, vertical_spacing: float
) -> float:
    """The ACTUAL on-screen pixel height of a row given its own normalized
    `row_heights` fraction -- mirrors make_subplots()'s own documented
    behavior (confirmed directly via full_figure_for_development() while
    fixing this: row_heights are scaled by (1 - vertical_spacing*(n-1))
    before mapping onto the plot area, and the plot area itself is
    `fig.layout.height` minus Plotly's fixed default top+bottom margin,
    100 + 80 = 180px)."""
    normalized_span = row_domain_y[1] - row_domain_y[0]
    row_heights_fraction = normalized_span / (1 - vertical_spacing * (n - 1))
    return row_heights_fraction * (fig.layout.height - 180)


def test_pipeline_gives_a_short_table_only_the_space_it_needs_not_a_full_chart_row():
    """Regression: the first fix here gave every table a flat fraction of a
    350px chart row regardless of its real row count -- too much for a
    short table (confirmed live: a big blank gap below a 2-row table)."""
    pipeline = table(_leaderboard_df()) | timeseries(_series("a"))  # _leaderboard_df() has 2 data rows
    fig = pipeline.render()
    vertical_spacing = min(0.12, 0.9 / 1)

    table_trace = next(t for t in fig.data if isinstance(t, go.Table))
    px = _rendered_row_height_px(fig, table_trace.domain.y, n=2, vertical_spacing=vertical_spacing)
    content_px = 28 + 2 * 20 + 30  # header + 2 rows + padding, the same formula render() itself uses
    assert px < 250  # nowhere near the old flat 350px-per-row share
    assert px >= content_px - 1  # but still enough to render every row, not clipped


def test_pipeline_does_not_clip_a_longer_tables_rows():
    """Regression: the same flat-fraction fix that fixed short tables
    UNDER-sized a longer one -- confirmed live, a 4-row leaderboard's last
    row got pushed outside its allotted domain slice entirely (clipped, not
    just cramped)."""
    long_df = pd.concat([_leaderboard_df()] * 4, ignore_index=True)  # 8 data rows
    pipeline = table(long_df) | timeseries(_series("a"))
    fig = pipeline.render()
    vertical_spacing = min(0.12, 0.9 / 1)

    table_trace = next(t for t in fig.data if isinstance(t, go.Table))
    px = _rendered_row_height_px(fig, table_trace.domain.y, n=2, vertical_spacing=vertical_spacing)
    content_px = 28 + 8 * 20 + 30
    assert px >= content_px - 1  # every one of the 8 rows fits, none pushed outside the domain


def _ohlc_df(date_column: str | None = "date", periods: int = 5) -> pd.DataFrame:
    """A tiny OHLC frame -- `date_column=None` puts the dates on the index
    instead (the shape `Symbol(...).eod_bars()` isn't in, but a plain
    DataFrame().set_index("date") would be), exercising candles()'s other
    auto-detection branch."""
    idx = [date(2024, 1, 1) + timedelta(days=i) for i in range(periods)]
    df = pd.DataFrame(
        {
            "open": [100.0 + i for i in range(periods)],
            "high": [101.0 + i for i in range(periods)],
            "low": [99.0 + i for i in range(periods)],
            "close": [100.5 + i for i in range(periods)],
        }
    )
    if date_column is None:
        df.index = idx
    else:
        df[date_column] = idx
    return df


def test_candles_returns_a_chart_call():
    result = candles(_ohlc_df())
    assert isinstance(result, ChartCall)


def test_candles_renders_a_native_candlestick_trace_with_the_right_values():
    fig = candles(_ohlc_df()).render()
    assert len(fig.data) == 1
    trace = fig.data[0]
    assert isinstance(trace, go.Candlestick)
    assert list(trace.open) == [100.0, 101.0, 102.0, 103.0, 104.0]
    assert list(trace.close) == [100.5, 101.5, 102.5, 103.5, 104.5]


def test_candles_auto_detects_a_date_column():
    df = _ohlc_df(date_column="date")
    fig = candles(df).render()
    assert list(fig.data[0].x) == list(df["date"])


def test_candles_auto_detects_a_ts_column():
    df = _ohlc_df(date_column="ts")
    fig = candles(df).render()
    assert list(fig.data[0].x) == list(df["ts"])


def test_candles_auto_detects_a_datetime_index_when_no_date_or_ts_column_exists():
    df = _ohlc_df(date_column=None)
    fig = candles(df).render()
    assert list(fig.data[0].x) == list(df.index)


def test_candles_accepts_an_explicit_x_column_overriding_the_guess():
    df = _ohlc_df(date_column="date").rename(columns={"date": "my_date"})
    fig = candles(df, x="my_date").render()
    assert list(fig.data[0].x) == list(df["my_date"])


def test_candles_raises_a_clear_error_when_no_x_can_be_resolved():
    df = _ohlc_df(date_column=None)
    df = df.reset_index(drop=True)  # back to a plain RangeIndex, no date/ts column either
    with pytest.raises(ValueError, match="couldn't find an x-axis column"):
        candles(df).render()


def test_candles_accepts_custom_column_names():
    df = _ohlc_df().rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close"})
    fig = candles(df, open="Open", high="High", low="Low", close="Close").render()
    assert list(fig.data[0].open) == list(df["Open"])


def test_candles_rangeslider_is_off_by_default():
    fig = candles(_ohlc_df()).render()
    assert fig.layout.xaxis.rangeslider.visible is False


def test_candles_rangeslider_can_be_enabled():
    fig = candles(_ohlc_df(), rangeslider=True).render()
    assert fig.layout.xaxis.rangeslider.visible is True


def test_candles_registered_under_the_chart_registry():
    resolved = Registry.get(Chart, "candles")
    assert isinstance(resolved, CandlestickChart)


def test_candles_composes_with_timeseries_via_pipe():
    pipeline = candles(_ohlc_df()) | timeseries(_series("volume"))
    fig = pipeline.render()
    assert len(fig.data) == 2


def test_candles_composes_via_overlay():
    overlay = candles(_ohlc_df()) & rect([(date(2024, 1, 1), date(2024, 1, 2))], layer=-1)
    fig = overlay.render()
    assert len(fig.data) == 1
    assert len(fig.layout.shapes) == 1


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


def test_invert_reverses_its_own_standalone_axis():
    fig = timeseries(_series("a")).invert().render()
    assert fig.layout.yaxis.autorange == "reversed"


def test_invert_twice_cancels_back_to_normal():
    fig = timeseries(_series("a")).invert().invert().render()
    assert fig.layout.yaxis.autorange is None


def test_invert_works_on_any_chart_not_just_timeseries():
    fig = rect([(date(2024, 1, 1), date(2024, 1, 5))]).invert().render()
    assert fig.layout.yaxis.autorange == "reversed"


def test_overlay_invert_flips_the_axis_a_member_actually_ends_up_on():
    """.invert() on the RIGHT-axis member must flip yaxis2, not the LEFT
    axis shared by the other member -- confirmed by asserting the left
    axis stays untouched."""
    overlay = (
        timeseries(_series("a", start_value=100.0)) & timeseries(_series("b", start_value=5.0), axis="right").invert()
    )
    fig = overlay.render()
    assert fig.layout.yaxis2.autorange == "reversed"
    assert fig.layout.yaxis.autorange is None


def test_invert_returns_a_new_call_leaving_the_original_untouched():
    original = timeseries(_series("a"))
    inverted = original.invert()
    assert original.render().layout.yaxis.autorange is None
    assert inverted.render().layout.yaxis.autorange == "reversed"


def test_invert_composes_with_pipe_after_being_called():
    pipeline = timeseries(_series("a")).invert() | timeseries(_series("b"))
    fig = pipeline.render()
    assert fig.layout.yaxis.autorange == "reversed"
    assert fig.layout.yaxis2.autorange is None


def test_axis_title_labels_the_axis_this_call_ends_up_on():
    overlay = timeseries(_series("a"), axis_title="Points") & timeseries(_series("b"), axis="right", axis_title="%")
    fig = overlay.render()
    assert fig.layout.yaxis.title.text == "Points"
    assert fig.layout.yaxis2.title.text == "%"


def test_overlay_with_secondary_axis_moves_legend_inside_to_avoid_clipping():
    """Regression: Plotly's default legend floats to the right of the
    plot, which is exactly where a secondary axis's own title/ticks live
    too -- confirmed live, they collide and the legend gets clipped off
    the right edge of the figure. A dual-axis overlay should default to a
    horizontal legend inside the top of the plot instead, with no action
    needed from the caller."""
    overlay = timeseries(_series("a")) & timeseries(_series("b"), axis="right")
    fig = overlay.render()
    assert fig.layout.legend.orientation == "h"
    assert fig.layout.margin.r == 60


def test_overlay_without_secondary_axis_leaves_the_default_legend_alone():
    overlay = timeseries(_series("a")) & timeseries(_series("b"))
    fig = overlay.render()
    assert fig.layout.legend.orientation is None


def test_pipeline_with_a_secondary_axis_row_moves_legend_inside_to_avoid_clipping():
    pipeline = (timeseries(_series("a")) & timeseries(_series("b"), axis="right")) | timeseries(_series("c"))
    fig = pipeline.render()
    assert fig.layout.legend.orientation == "h"
    assert fig.layout.margin.r == 60


def test_pipeline_always_uses_a_horizontal_legend_regardless_of_secondary_axis():
    """Regression: a plain vertical legend defaults to a fixed top-right
    corner of the WHOLE figure, sized to fit only its own content -- for a
    tall multi-row ChartPipeline composite (confirmed live on a 5-row,
    ~2600px ExperimentResult.report()) that strands it in a tiny box next
    to the first row while every row below has empty margin on the right
    with no legend at all. Unlike ChartOverlay (a single panel, where the
    plain default legend is genuinely fine with no secondary axis),
    ChartPipeline always has multiple rows once it renders at all, so the
    horizontal top legend is unconditional here, not just for the
    secondary-axis case."""
    pipeline = timeseries(_series("a")) | timeseries(_series("b"))
    fig = pipeline.render()
    assert fig.layout.legend.orientation == "h"
    assert fig.layout.legend.y == pytest.approx(1.02)


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
