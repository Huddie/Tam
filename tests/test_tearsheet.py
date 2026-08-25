"""tam/backtest/tearsheet.py: the registry-driven chart/metric interfaces, and
build_tearsheet()'s layout switch. Small hand-built curves throughout, same
style as tests/test_report.py/test_visualization.py -- no network, no real
backtest involved.
"""
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from tam.backtest.report import Report
from tam.backtest.tearsheet import (
    ALL_CHARTS,
    ALL_METRICS,
    DEFAULT_CHARTS,
    DEFAULT_METRICS,
    CumulativeReturnsChart,
    DrawdownChart,
    MaxDrawdownByStartDateChart,
    MaxDrawdownMetric,
    MonthlyReturnsHeatmapChart,
    RollingSharpeChart,
    SharpeDifferenceByStartDateChart,
    SharpeMetric,
    Tearsheet,
    TearsheetChart,
    TearsheetMetric,
    WorstDrawdownPeriodsChart,
    build_tearsheet,
    metrics_table,
)
from tam.registry import Registry


def _series(values, start=date(2022, 1, 1)):
    idx = [start + timedelta(days=i) for i in range(len(values))]
    return pd.Series(values, index=idx)


def _report(n_portfolios=2):
    values = [100.0, 101.0, 99.0, 103.0, 105.0, 104.0, 108.0, 110.0, 107.0, 112.0] * 30  # long enough for rolling windows
    curves = {}
    for i in range(n_portfolios):
        drift = 1.0 + i * 0.002
        curves[f"portfolio_{i}"] = _series([v * (drift**j) for j, v in enumerate(values)])
    return Report.from_curves(curves)


def test_builtin_charts_and_metrics_are_registered():
    assert set(ALL_CHARTS) <= set(Registry.names(TearsheetChart))
    assert set(ALL_METRICS) <= set(Registry.names(TearsheetMetric))


@pytest.mark.parametrize("chart_id", DEFAULT_CHARTS)
def test_every_default_chart_renders_without_error(chart_id):
    report = _report()
    chart = Registry.get(TearsheetChart, chart_id)

    fig = chart.render(report)

    assert fig.data  # at least one trace


@pytest.mark.parametrize("chart_id", [c for c in ALL_CHARTS if c not in DEFAULT_CHARTS])
def test_every_opt_in_chart_renders_without_error(chart_id):
    report = _report(n_portfolios=2)
    chart = Registry.get(TearsheetChart, chart_id)

    fig = chart.render(report)

    assert fig.data or fig.layout.annotations  # a real trace, or a graceful "nothing to show" annotation


@pytest.mark.parametrize("metric_id", ALL_METRICS)
def test_every_metric_computes_a_finite_number(metric_id):
    report = _report(n_portfolios=1)
    portfolio_id = report.portfolio_ids()[0]
    metric = Registry.get(TearsheetMetric, metric_id)

    value = metric.compute(report, portfolio_id)

    assert np.isfinite(value)


def test_cumulative_returns_chart_has_one_trace_per_portfolio():
    report = _report(n_portfolios=3)

    fig = CumulativeReturnsChart().render(report)

    assert len(fig.data) == 3
    assert {trace.name for trace in fig.data} == set(report.portfolio_ids())


def test_rolling_sharpe_chart_accepts_a_custom_window_and_labels_its_own_title():
    report = _report()

    chart = RollingSharpeChart(window_days=30)

    assert "30" in chart.title
    fig = chart.render(report)
    assert fig.data


def test_return_distribution_by_start_date_chart_has_four_traces_per_portfolio():
    from tam.backtest.tearsheet import ReturnDistributionByStartDateChart

    report = _report(n_portfolios=2)

    fig = ReturnDistributionByStartDateChart().render(report)

    assert len(fig.data) == 8  # 4 stats (avg/min/max/std) x 2 portfolios


def test_return_distribution_matches_a_direct_brute_force_computation():
    from tam.backtest.tearsheet import _return_distribution_by_end_date

    r = np.array([0.01, -0.02, 0.03, 0.015, -0.01, 0.02])
    stats = _return_distribution_by_end_date(r)

    # brute force: for the LAST end date, the return from every possible
    # earlier start index through the end.
    cumulative = np.cumprod(1 + r)
    brute_returns = [cumulative[-1] / cumulative[i - 1] - 1 if i > 0 else cumulative[-1] - 1 for i in range(len(r))]

    assert stats["avg"][-1] == pytest.approx(np.mean(brute_returns))
    assert stats["min"][-1] == pytest.approx(np.min(brute_returns))
    assert stats["max"][-1] == pytest.approx(np.max(brute_returns))


def test_suffix_max_drawdown_matches_a_brute_force_computation():
    from tam.backtest.tearsheet import _suffix_max_drawdown

    rng = np.random.default_rng(12345)
    r = np.maximum(rng.normal(loc=0.0005, scale=0.03, size=200), -0.99)

    optimized = _suffix_max_drawdown(r)

    def brute_force(returns):
        wealth = np.r_[1.0, np.cumprod(1.0 + returns)]
        running_peak = np.maximum.accumulate(wealth)
        return (wealth / running_peak - 1.0).min()

    brute = np.array([brute_force(r[i:]) for i in range(len(r))])
    assert np.allclose(optimized, brute, rtol=1e-12, atol=1e-12)


def test_worst_drawdown_paths_chart_finds_a_synthetic_crash():
    from tam.backtest.tearsheet import WorstDrawdownPathsChart

    # a flat series with one sharp -30% crash in the middle
    values = [100.0] * 20 + [70.0] * 20
    report = Report.from_curves({"crashy": _series(values)})

    fig = WorstDrawdownPathsChart(threshold=-0.20).render(report)

    assert fig.data
    assert "No start dates" not in (fig.layout.annotations[0].text if fig.layout.annotations else "")


def test_worst_drawdown_paths_chart_annotates_when_nothing_crosses_the_threshold():
    from tam.backtest.tearsheet import WorstDrawdownPathsChart

    report = _report(n_portfolios=1)  # mild synthetic wobble, never near -90%

    fig = WorstDrawdownPathsChart(threshold=-0.90).render(report)

    assert fig.layout.annotations
    assert "No start dates" in fig.layout.annotations[0].text


def test_sharpe_metric_matches_report_summary_reasonably_closely():
    # SharpeMetric reuses tam.basket.factors.RollingSharpe's own math over the
    # full history; Report.summary()'s inline Sharpe is a second, independent
    # implementation of the same formula -- they should agree (both are
    # annualized mean/std over the same returns), not just both "look like a
    # number."
    report = _report(n_portfolios=1)
    portfolio_id = report.portfolio_ids()[0]

    from_metric = SharpeMetric().compute(report, portfolio_id)
    from_summary = report.summary(portfolio_id)["sharpe"]

    assert from_metric == pytest.approx(from_summary, rel=1e-9)


def test_max_drawdown_metric_matches_report_summary_reasonably_closely():
    report = _report(n_portfolios=1)
    portfolio_id = report.portfolio_ids()[0]

    from_metric = MaxDrawdownMetric().compute(report, portfolio_id)
    from_summary = report.summary(portfolio_id)["max_drawdown"]

    assert from_metric == pytest.approx(from_summary, rel=1e-9)


def test_metrics_table_has_one_row_per_metric_and_one_column_per_portfolio():
    report = _report(n_portfolios=2)

    table = metrics_table(report, metrics=["total_return", "sharpe", "max_drawdown"])

    assert list(table.columns) == report.portfolio_ids()
    assert list(table.index) == ["Cumulative Return", "Sharpe", "Max Drawdown"]


def test_metrics_table_accepts_an_already_constructed_metric_instance():
    report = _report(n_portfolios=1)

    class DoubleSharpe(TearsheetMetric):
        label, format = "2x Sharpe", "ratio"

        def compute(self, report, portfolio_id):
            return SharpeMetric().compute(report, portfolio_id) * 2

    table = metrics_table(report, metrics=["sharpe", DoubleSharpe()])

    portfolio_id = report.portfolio_ids()[0]
    sharpe = SharpeMetric().compute(report, portfolio_id)
    doubled = float(table.loc["2x Sharpe", portfolio_id])

    # table values are formatted strings (f"{v:.2f}" for a "ratio" metric) --
    # compare at that same 2-decimal precision, not exact float equality.
    assert doubled == pytest.approx(sharpe * 2, abs=0.005)


def test_build_tearsheet_uses_side_by_side_layout_for_three_or_fewer_portfolios():
    report = _report(n_portfolios=3)

    html = build_tearsheet(report, charts=["cumulative_returns"], metrics=["sharpe"])

    assert "2fr 1fr" in html


def test_build_tearsheet_uses_stacked_layout_for_more_than_three_portfolios():
    report = _report(n_portfolios=4)

    html = build_tearsheet(report, charts=["cumulative_returns"], metrics=["sharpe"])

    assert "2fr 1fr" not in html
    assert "grid-template-columns: 1fr;" in html


def test_build_tearsheet_accepts_an_already_constructed_chart_instance():
    report = _report(n_portfolios=1)

    html = build_tearsheet(report, charts=[RollingSharpeChart(window_days=20)], metrics=["sharpe"])

    assert "Rolling Sharpe (20d)" in html


def test_registering_a_custom_chart_makes_it_usable_by_id():
    import plotly.graph_objects as go

    @Registry.register(TearsheetChart, "test_only_flat_line")
    class _FlatLineChart(TearsheetChart):
        title = "Flat Line"

        def render(self, report):
            return go.Figure(go.Scatter(x=[0, 1], y=[1, 1]))

    report = _report(n_portfolios=1)
    html = build_tearsheet(report, charts=["test_only_flat_line"], metrics=["sharpe"])

    assert "Flat Line" in html


def _decoded_iframe_html(iframe) -> str:
    """show()/Tearsheet.show() return an IFrame wrapping a data: URI (see
    _display()'s own docstring for why, not a plain IPython.display.HTML) --
    decode it back to the actual tearsheet HTML for comparison in tests."""
    import base64

    prefix = "data:text/html;base64,"
    assert iframe.src.startswith(prefix)
    return base64.b64decode(iframe.src[len(prefix) :]).decode("utf-8")


def test_write_html_and_show_produce_the_same_content(tmp_path):
    from tam.backtest.tearsheet import show, write_html

    report = _report(n_portfolios=1)
    path = write_html(report, str(tmp_path / "tearsheet.html"), charts=["cumulative_returns"], metrics=["sharpe"])

    assert path.exists() and path.stat().st_size > 0
    iframe = show(report, charts=["cumulative_returns"], metrics=["sharpe"])
    assert _decoded_iframe_html(iframe) == path.read_text()


def test_show_returns_an_iframe_not_a_plain_html_object():
    # Regression test: multiple Plotly figures concatenated into one HTML
    # blob and shown via IPython.display.HTML(...) directly used to render
    # only the first chart -- browsers don't reliably execute <script> tags
    # inserted into a notebook cell's output beyond the first one. An
    # iframe's own document always executes every chart's own script.
    from IPython.display import IFrame

    from tam.backtest.tearsheet import show

    report = _report(n_portfolios=1)

    result = show(report, charts=["cumulative_returns", "drawdown"], metrics=["sharpe"])

    assert isinstance(result, IFrame)
    html = _decoded_iframe_html(result)
    assert "Cumulative Returns vs Benchmark" in html
    assert "Underwater Plot" in html


def test_tearsheet_class_defaults_to_default_charts_and_metrics():
    ts = Tearsheet()

    assert ts.charts == DEFAULT_CHARTS
    assert ts.metrics == DEFAULT_METRICS


def test_tearsheet_class_accepts_explicit_charts_and_metrics_in_the_constructor():
    ts = Tearsheet(charts=["cumulative_returns"], metrics=["sharpe"], title="Custom")

    assert ts.charts == ["cumulative_returns"]
    assert ts.metrics == ["sharpe"]
    assert ts.title == "Custom"


def test_tearsheet_add_chart_and_add_metric_are_fluent_and_mutate_in_place():
    ts = Tearsheet(charts=[], metrics=[])

    result = ts.add_chart("cumulative_returns").add_metric("sharpe")

    assert result is ts  # fluent -- returns self, not a copy
    assert ts.charts == ["cumulative_returns"]
    assert ts.metrics == ["sharpe"]


def test_tearsheet_build_matches_build_tearsheet_with_the_same_args():
    report = _report(n_portfolios=1)
    ts = Tearsheet(charts=["cumulative_returns"], metrics=["sharpe"], title="Custom")

    assert ts.build(report) == build_tearsheet(report, title="Custom", charts=["cumulative_returns"], metrics=["sharpe"])


def test_tearsheet_show_and_write_reuse_the_same_configured_charts_and_metrics(tmp_path):
    report = _report(n_portfolios=1)
    ts = Tearsheet().add_chart("drawdown").add_metric("max_drawdown")

    html_from_show = _decoded_iframe_html(ts.show(report))
    path = ts.write(report, str(tmp_path / "tearsheet.html"))

    assert html_from_show == path.read_text()
    assert "Underwater Plot" in html_from_show  # drawdown chart's own title
    assert "Max Drawdown" in html_from_show  # metric row label


def test_tearsheet_constructor_does_not_share_mutable_state_between_instances():
    # DEFAULT_CHARTS is a module-level list -- Tearsheet() must copy it, not
    # alias, or add_chart() on one instance would leak into every other
    # Tearsheet() (and into DEFAULT_CHARTS itself).
    original_length = len(DEFAULT_CHARTS)
    ts1 = Tearsheet()
    ts1.add_chart("some_marker_chart_id_not_a_real_chart")

    ts2 = Tearsheet()

    assert "some_marker_chart_id_not_a_real_chart" not in ts2.charts
    assert "some_marker_chart_id_not_a_real_chart" not in DEFAULT_CHARTS
    assert len(DEFAULT_CHARTS) == original_length


def test_max_drawdown_by_start_date_differs_from_the_underwater_plot():
    # DrawdownChart (Underwater Plot) fixes ONE start (the data's own first
    # date) and shows drawdown evolving over calendar time from there.
    # MaxDrawdownByStartDateChart instead fixes the END and asks "what if I'd
    # started on date X" for every possible X -- genuinely different series,
    # not just a renamed duplicate.
    report = _report(n_portfolios=1)
    portfolio_id = report.portfolio_ids()[0]

    underwater = DrawdownChart().render(report).data[0]
    by_start_date = MaxDrawdownByStartDateChart().render(report).data[0]

    assert list(underwater.y) != list(by_start_date.y)
    # the LAST point of "by start date" (start = the very last observation)
    # is the trivial 1-day drawdown -- 0, since a single point can't dip
    # below its own starting wealth.
    assert by_start_date.y[-1] == pytest.approx(0.0)


def test_suffix_stats_final_value_matches_a_manual_calculation():
    from tam.backtest.tearsheet import _suffix_stats

    r = np.array([0.10, -0.05, 0.02])
    dates = pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"]).to_numpy(dtype="datetime64[ns]")

    stats = _suffix_stats(r, dates, dates[-1])

    # starting from index 0: 100_000 * 1.10 * 0.95 * 1.02
    assert stats["final_value"][0] == pytest.approx(100_000 * 1.10 * 0.95 * 1.02)
    # starting from the last index: just that one day's return
    assert stats["final_value"][-1] == pytest.approx(100_000 * 1.02)
    assert stats["trades"][0] == 3
    assert stats["trades"][-1] == 1


def test_sharpe_difference_by_start_date_defaults_to_the_last_portfolio_as_benchmark():
    report = _report(n_portfolios=2)

    fig = SharpeDifferenceByStartDateChart(min_trades=0).render(report)

    assert len(fig.data) == 1  # 2 portfolios -> 1 non-benchmark series
    assert report.portfolio_ids()[-1] in fig.data[0].name


def test_sharpe_difference_by_start_date_accepts_an_explicit_benchmark():
    report = _report(n_portfolios=2)
    portfolio_ids = report.portfolio_ids()

    fig = SharpeDifferenceByStartDateChart(benchmark_id=portfolio_ids[0], min_trades=0).render(report)

    assert portfolio_ids[0] in fig.data[0].name
    assert portfolio_ids[1] in fig.data[0].name


def test_sharpe_difference_by_start_date_annotates_with_a_single_portfolio():
    report = _report(n_portfolios=1)

    fig = SharpeDifferenceByStartDateChart().render(report)

    assert not fig.data
    assert "at least 2 portfolios" in fig.layout.annotations[0].text


def test_monthly_returns_heatmap_defaults_to_the_first_portfolio():
    report = _report(n_portfolios=2)

    fig = MonthlyReturnsHeatmapChart().render(report)

    assert report.portfolio_ids()[0] in fig.layout.title.text


def test_monthly_returns_heatmap_accepts_an_explicit_portfolio_id():
    report = _report(n_portfolios=2)
    portfolio_id = report.portfolio_ids()[1]

    fig = MonthlyReturnsHeatmapChart(portfolio_id=portfolio_id).render(report)

    assert portfolio_id in fig.layout.title.text


def test_worst_drawdown_periods_chart_shades_the_deepest_synthetic_crash():
    values = [100.0] * 20 + [70.0] * 20 + [100.0] * 20
    report = Report.from_curves({"crashy": _series(values)})

    fig = WorstDrawdownPeriodsChart(n_periods=1).render(report)

    assert len(fig.layout.shapes) == 1  # one shaded vrect for the one crash

