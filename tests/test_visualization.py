from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go

from tam.backtest.report import Report
from tam.backtest.visualization import render, write_html


def _snap(d, portfolio, value, cash=0.0):
    return {"date": d, "portfolio": portfolio, "cash": cash, "value": value}


def _series(portfolio, values, start=date(2024, 1, 1)):
    return [
        _snap(start + timedelta(days=i), portfolio, v)
        for i, v in enumerate(values)
    ]


def _two_portfolio_report():
    snapshots = (
        _series("main", [100.0, 120.0, 90.0, 110.0, 150.0])
        + _series("alt", [200.0, 190.0, 210.0, 220.0])
    )
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


def test_render_uses_the_same_line_color_for_a_portfolio_s_equity_and_drawdown():
    report = _two_portfolio_report()

    fig = render(report)

    equity_color = {t.name: t.line.color for t in fig.data if isinstance(t, go.Scatter) and t.mode == "lines"
                     and t.name in ("main", "alt")}
    drawdown_color = {t.name.removesuffix(" drawdown"): t.line.color for t in fig.data
                       if isinstance(t, go.Scatter) and t.mode == "lines" and t.name.endswith(" drawdown")}

    assert equity_color["main"] == drawdown_color["main"]
    assert equity_color["alt"] == drawdown_color["alt"]
    assert equity_color["main"] != equity_color["alt"]  # still distinct between portfolios
