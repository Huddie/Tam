from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go

from tam.backtest.report import Report
from tam.backtest.visualization import RenderOptions, render, render_curves, write_html


def _snap(d, portfolio, value, cash=0.0):
    return {"date": d, "portfolio": portfolio, "cash": cash, "value": value}


def _series(portfolio, values, start=date(2024, 1, 1)):
    return [_snap(start + timedelta(days=i), portfolio, v) for i, v in enumerate(values)]


def _two_portfolio_report():
    snapshots = _series("main", [100.0, 120.0, 90.0, 110.0, 150.0]) + _series("alt", [200.0, 190.0, 210.0, 220.0])
    return Report(snapshots)


def test_render_returns_figure():
    report = _two_portfolio_report()

    fig = render(report)

    assert isinstance(fig, go.Figure)


def test_render_trace_counts_and_types():
    report = _two_portfolio_report()

    fig = render(report)

    scatter_traces = [t for t in fig.data if isinstance(t, go.Scatter)]
    table_traces = [t for t in fig.data if isinstance(t, go.Table)]

    # 2 portfolios -> 1 equity trace + 1 drawdown trace each = 4 scatter traces.
    assert len(scatter_traces) == 4
    # Exactly one summary table trace.
    assert len(table_traces) == 1
    assert len(fig.data) == 5

    # Equity traces should be legend-visible, drawdown traces hidden from legend.
    equity_traces = [t for t in scatter_traces if t.name in ("main", "alt")]
    drawdown_traces = [t for t in scatter_traces if t.name in ("main drawdown", "alt drawdown")]
    assert len(equity_traces) == 2
    assert len(drawdown_traces) == 2
    assert all(t.showlegend is not False for t in equity_traces)
    assert all(t.showlegend is False for t in drawdown_traces)


def test_render_uses_given_title():
    report = _two_portfolio_report()

    fig = render(report, title="My Custom Title")

    assert fig.layout.title.text == "My Custom Title"


def test_write_html_creates_nonempty_file(tmp_path):
    report = _two_portfolio_report()
    out_path = tmp_path / "report.html"

    write_html(report, str(out_path))

    assert out_path.exists()
    assert out_path.stat().st_size > 0


def test_render_table_shows_actual_dollar_values_not_just_the_indexed_chart():
    report = _two_portfolio_report()

    fig = render(report)

    table = next(t for t in fig.data if isinstance(t, go.Table))
    header = list(table.header.values)
    assert "Start Value" in header
    assert "End Value" in header

    start_col = list(table.cells.values[header.index("Start Value")])
    end_col = list(table.cells.values[header.index("End Value")])
    # portfolio_ids() sorts alphabetically -> "alt" before "main".
    assert start_col == ["$200.00", "$100.00"]
    assert end_col == ["$220.00", "$150.00"]


def _report_with_trades():
    snapshots = _series("main", [100.0, 120.0, 90.0, 110.0, 150.0])
    trades = [
        {"date": date(2024, 1, 2), "portfolio": "main", "ticker": "AAPL", "side": "BUY", "qty": 10, "price": 100.0},
        {"date": date(2024, 1, 4), "portfolio": "main", "ticker": "AAPL", "side": "SELL", "qty": 4, "price": 90.0},
        {"date": date(2024, 1, 4), "portfolio": "main", "ticker": "MSFT", "side": "BUY", "qty": 2, "price": 300.0},
    ]
    return Report(snapshots, trades)


def test_render_adds_visible_by_default_trade_marker_trace_when_trades_exist():
    report = _report_with_trades()

    fig = render(report)

    marker_traces = [t for t in fig.data if isinstance(t, go.Scatter) and t.mode == "markers"]
    assert len(marker_traces) == 1
    trace = marker_traces[0]
    assert trace.visible is True
    assert trace.name == "main trades"
    assert len(trace.x) == 2  # two distinct trade dates: Jan 2 and Jan 4


def test_render_options_show_trades_default_false_hides_marker_trace_initially():
    report = _report_with_trades()

    fig = render(report, options=RenderOptions(show_trades_default=False))

    marker_traces = [t for t in fig.data if isinstance(t, go.Scatter) and t.mode == "markers"]
    assert len(marker_traces) == 1
    assert marker_traces[0].visible is False


def test_render_options_template_and_height_are_applied():
    report = _two_portfolio_report()

    fig = render(report, options=RenderOptions(template="plotly_dark", height=700))

    assert fig.layout.height == 700


def test_render_groups_same_day_trades_into_one_marker_with_combined_hover_text():
    report = _report_with_trades()

    fig = render(report)

    trace = next(t for t in fig.data if isinstance(t, go.Scatter) and t.mode == "markers")
    hover_by_date = {pd.Timestamp(x).date(): text for x, text in zip(trace.x, trace.hovertext)}

    hover = hover_by_date[date(2024, 1, 4)]
    assert "2 trades" in hover
    assert "SELL 4 AAPL" in hover
    assert "BUY 2 MSFT" in hover

    single = hover_by_date[date(2024, 1, 2)]
    assert "1 trade" in single
    assert "1 trades" not in single
    assert "BUY 10 AAPL" in single


def test_render_adds_show_hide_toggle_buttons_when_trades_exist():
    report = _report_with_trades()

    fig = render(report)

    assert len(fig.layout.updatemenus) == 1
    buttons = fig.layout.updatemenus[0].buttons
    assert [b.label for b in buttons] == ["Show Trades", "Hide Trades"]


def test_render_omits_toggle_buttons_when_no_trades():
    report = _two_portfolio_report()

    fig = render(report)

    assert len(fig.layout.updatemenus) == 0


def test_render_table_includes_a_trade_count_column():
    report = _report_with_trades()

    fig = render(report)

    table = next(t for t in fig.data if isinstance(t, go.Table))
    header = list(table.header.values)
    assert "# Trades" in header
    assert list(table.cells.values[header.index("# Trades")]) == ["3"]


def test_drawdown_traces_are_filled_to_zero_with_a_transparent_version_of_their_own_color():
    report = _two_portfolio_report()

    fig = render(report)

    drawdown_traces = [t for t in fig.data if isinstance(t, go.Scatter) and t.name.endswith(" drawdown")]
    assert drawdown_traces
    for trace in drawdown_traces:
        assert trace.fill == "tozeroy"
        assert trace.fillcolor.startswith("rgba(")
        assert trace.fillcolor.endswith(",0.2)")
        # Fill color's RGB channels match the line's own color, just with alpha added.
        line_rgb = tuple(int(trace.line.color.lstrip("#")[i : i + 2], 16) for i in (0, 2, 4))
        fill_rgb = tuple(int(c) for c in trace.fillcolor[len("rgba(") : -len(",0.2)")].split(","))
        assert line_rgb == fill_rgb


def test_render_uses_the_same_line_color_for_a_portfolio_s_equity_and_drawdown():
    report = _two_portfolio_report()

    fig = render(report)

    equity_color = {
        t.name: t.line.color
        for t in fig.data
        if isinstance(t, go.Scatter) and t.mode == "lines" and t.name in ("main", "alt")
    }
    drawdown_color = {
        t.name.removesuffix(" drawdown"): t.line.color
        for t in fig.data
        if isinstance(t, go.Scatter) and t.mode == "lines" and t.name.endswith(" drawdown")
    }

    assert equity_color["main"] == drawdown_color["main"]
    assert equity_color["alt"] == drawdown_color["alt"]
    assert equity_color["main"] != equity_color["alt"]  # still distinct between portfolios


def _price_series(values, start=date(2024, 1, 1)):
    index = pd.to_datetime([start + timedelta(days=i) for i in range(len(values))])
    return pd.Series(values, index=index)


def test_render_omits_price_panel_when_not_given():
    report = _two_portfolio_report()

    fig = render(report)

    assert "Ticker Prices" not in [a.text for a in fig.layout.annotations]
    assert len(fig.layout.annotations) == 3  # equity, drawdown, table titles only


def test_render_adds_price_panel_above_the_equity_chart_when_given():
    report = _two_portfolio_report()
    prices = {
        "AAPL": _price_series([190.0, 195.0, 193.0, 200.0]),
        "MSFT": _price_series([300.0, 305.0, 298.0, 310.0]),
    }

    fig = render(report, prices=prices)

    titles = [a.text for a in fig.layout.annotations]
    assert titles[0] == "Ticker Prices"
    assert titles == ["Ticker Prices", "Relative Performance (Indexed to 100)", "Drawdown", "Summary Metrics"]

    price_traces = {t.name: t for t in fig.data if isinstance(t, go.Scatter) and t.name in prices}
    assert set(price_traces) == {"AAPL", "MSFT"}
    assert list(price_traces["AAPL"].y) == [190.0, 195.0, 193.0, 200.0]

    # The price panel is row 1 -- its y-axis is the figure's first ("yaxis"), log scale.
    assert fig.layout.yaxis.type == "log"
    assert fig.layout.yaxis.title.text == "Price ($, log scale)"


def test_render_price_panel_coexists_with_trade_markers_and_toggle():
    report = _report_with_trades()
    prices = {"AAPL": _price_series([100.0, 102.0, 98.0, 105.0, 110.0])}

    fig = render(report, prices=prices)

    assert any(t.name == "AAPL" for t in fig.data if isinstance(t, go.Scatter))
    marker_traces = [t for t in fig.data if isinstance(t, go.Scatter) and t.mode == "markers"]
    assert len(marker_traces) == 1
    assert len(fig.layout.updatemenus) == 1


def test_write_html_with_prices_creates_nonempty_file(tmp_path):
    report = _two_portfolio_report()
    prices = {"AAPL": _price_series([190.0, 195.0, 193.0, 200.0])}
    out_path = tmp_path / "report.html"

    write_html(report, str(out_path), prices=prices)

    assert out_path.exists()
    assert out_path.stat().st_size > 0


def test_render_curves_matches_render_of_the_equivalent_report():
    values = [100.0, 120.0, 90.0, 110.0, 150.0]
    idx = pd.to_datetime([date(2024, 1, 1) + timedelta(days=i) for i in range(len(values))])
    curves = {"main": pd.Series(values, index=idx)}

    from_curves_fig = render_curves(curves, title="My Custom Title")
    equivalent_fig = render(Report.from_curves(curves), title="My Custom Title")

    assert from_curves_fig.layout.title.text == equivalent_fig.layout.title.text == "My Custom Title"
    from_curves_names = sorted(t.name for t in from_curves_fig.data if isinstance(t, go.Scatter))
    equivalent_names = sorted(t.name for t in equivalent_fig.data if isinstance(t, go.Scatter))
    assert from_curves_names == equivalent_names == ["main", "main drawdown"]


def test_render_curves_accepts_a_wide_dataframe_and_render_options():
    idx = pd.to_datetime([date(2024, 1, 1) + timedelta(days=i) for i in range(4)])
    df = pd.DataFrame({"a": [100.0, 105.0, 102.0, 110.0], "b": [50.0, 49.0, 51.0, 53.0]}, index=idx)

    fig = render_curves(df, options=RenderOptions(height=700))

    assert fig.layout.height == 700
    equity_names = sorted(
        t.name for t in fig.data if isinstance(t, go.Scatter) and t.mode == "lines" and t.name in ("a", "b")
    )
    assert equity_names == ["a", "b"]
