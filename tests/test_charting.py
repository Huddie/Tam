"""tam/charting.py: the generic composable-chart API (Chart/ChartCall/
ChartPipeline, timeseries()) -- deliberately tested here with NO
dependency on tam.backtest's own chart classes, since the whole point of
this module is that it doesn't need one.
"""
from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go
import pytest

from tam.backtest.report import Report
from tam.charting import Chart, ChartCall, ChartPipeline, TimeSeriesChart, timeseries
from tam.registry import Registry


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
    # Validation happens lazily, inside render() (via _to_report()) --
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
    subclass, with no registration and no tam.backtest involvement at
    all, is immediately callable/composable like any built-in."""

    class DoubledChart(Chart):
        title = "Doubled"

        def render(self, report: Report) -> go.Figure:
            fig = go.Figure()
            for portfolio_id in report.portfolio_ids():
                curve = report.equity_curve(portfolio_id) * 2
                fig.add_trace(go.Scatter(x=curve.index, y=curve.values, name=portfolio_id))
            return fig

    series = _series(start_value=10.0)
    fig = DoubledChart()(series).render()
    assert list(fig.data[0].y) == [v * 2 for v in series.values]
