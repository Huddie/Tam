"""Renders a Report into a clean, comparison-ready Plotly dashboard.

Kept separate from report.py so computing metrics never requires plotly installed;
only call into this module when you actually want a chart.
"""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from .report import Report

_PERCENT_METRICS = {"total_return", "cagr", "volatility", "max_drawdown"}
_CURRENCY_METRICS = {"start_value", "end_value"}
_METRIC_LABELS = {
    "start_value": "Start Value",
    "end_value": "End Value",
    "total_return": "Total Return",
    "cagr": "CAGR",
    "volatility": "Volatility",
    "sharpe": "Sharpe",
    "max_drawdown": "Max Drawdown",
    "calmar": "Calmar",
}

_BUY_COLOR = "#2ca02c"
_SELL_COLOR = "#d62728"
_NEUTRAL_COLOR = "#7f7f7f"


def _side_label(side) -> str:
    return side.value if hasattr(side, "value") else str(side)


def _trade_marker_trace(report: Report, portfolio_id: str, normalized_curve: pd.Series):
    """One marker per trading day this portfolio traded, positioned on its own
    (indexed) equity line. Multiple trades on the same day are grouped into a
    single marker whose hover text lists every one of them."""
    trades_df = report.trades_for(portfolio_id)
    if trades_df.empty:
        return None

    # Don't coerce to pd.Timestamp here: equity_curve()'s index holds whatever
    # date type the snapshots used (plain datetime.date in practice), and
    # Timestamp/date never compare equal even for the same calendar day.
    xs, ys, texts, symbols, colors = [], [], [], [], []
    for trade_date, group in trades_df.groupby("date"):
        if trade_date not in normalized_curve.index:
            continue

        net = 0
        lines = []
        for _, row in group.iterrows():
            side_label = _side_label(row["side"])
            net += row["qty"] if side_label == "BUY" else -row["qty"]
            lines.append(f"{side_label} {row['qty']} {row['ticker']} @ ${row['price']:,.2f}")

        symbol = "triangle-up" if net > 0 else "triangle-down" if net < 0 else "diamond"
        color = _BUY_COLOR if net > 0 else _SELL_COLOR if net < 0 else _NEUTRAL_COLOR
        plural = "" if len(lines) == 1 else "s"
        header = f"{portfolio_id} — {trade_date} — {len(lines)} trade{plural}"

        xs.append(trade_date)
        ys.append(normalized_curve.loc[trade_date])
        symbols.append(symbol)
        colors.append(color)
        texts.append(f"<b>{header}</b><br>" + "<br>".join(lines))

    if not xs:
        return None

    return go.Scatter(
        x=xs,
        y=ys,
        mode="markers",
        marker=dict(symbol=symbols, size=13, color=colors, line=dict(width=1, color="black")),
        name=f"{portfolio_id} trades",
        hovertext=texts,
        hoverinfo="text",
        visible=False,
    )


def render(report: Report, title: str = "Backtest Report") -> go.Figure:
    """Build a 3-panel figure: normalized equity curves, drawdown, and a metrics table."""
    portfolio_ids = report.portfolio_ids()

    fig = make_subplots(
        rows=3,
        cols=1,
        row_heights=[0.45, 0.25, 0.30],
        vertical_spacing=0.08,
        specs=[[{"type": "xy"}], [{"type": "xy"}], [{"type": "table"}]],
        subplot_titles=("Relative Performance (Indexed to 100)", "Drawdown", "Summary Metrics"),
    )

    normalized_curves = {}
    for portfolio_id in portfolio_ids:
        curve = report.equity_curve(portfolio_id)
        normalized = curve / curve.iloc[0] * 100
        normalized_curves[portfolio_id] = normalized
        fig.add_trace(
            go.Scatter(x=normalized.index, y=normalized.values, mode="lines", name=portfolio_id),
            row=1,
            col=1,
        )

    for portfolio_id in portfolio_ids:
        drawdown = report.drawdown_curve(portfolio_id) * 100
        fig.add_trace(
            go.Scatter(
                x=drawdown.index,
                y=drawdown.values,
                mode="lines",
                name=f"{portfolio_id} drawdown",
                showlegend=False,
            ),
            row=2,
            col=1,
        )

    trade_trace_indices = []
    for portfolio_id in portfolio_ids:
        marker_trace = _trade_marker_trace(report, portfolio_id, normalized_curves[portfolio_id])
        if marker_trace is not None:
            fig.add_trace(marker_trace, row=1, col=1)
            trade_trace_indices.append(len(fig.data) - 1)

    summary = report.summary_all()
    metric_cols = [c for c in _METRIC_LABELS if c in summary.columns]
    header = ["Portfolio"] + [_METRIC_LABELS[c] for c in metric_cols]
    cells = [summary.index.tolist()]
    for col in metric_cols:
        if col in _CURRENCY_METRICS:
            fmt = lambda v: f"${v:,.2f}"
        elif col in _PERCENT_METRICS:
            fmt = lambda v: f"{v:.2%}"
        else:
            fmt = lambda v: f"{v:.2f}"
        cells.append([fmt(v) for v in summary[col]])

    fig.add_trace(
        go.Table(
            header=dict(values=header, fill_color="#1f2a44", font=dict(color="white"), align="left"),
            cells=dict(values=cells, align="left"),
        ),
        row=3,
        col=1,
    )

    fig.update_yaxes(title_text="Indexed value (start = 100)", row=1, col=1)
    fig.update_yaxes(title_text="Drawdown %", row=2, col=1)

    updatemenus = []
    if trade_trace_indices:
        updatemenus.append(
            dict(
                type="buttons",
                direction="right",
                showactive=True,
                x=1.0,
                xanchor="right",
                y=1.18,
                yanchor="top",
                buttons=[
                    dict(label="Show Trades", method="restyle", args=[{"visible": True}, trade_trace_indices]),
                    dict(label="Hide Trades", method="restyle", args=[{"visible": False}, trade_trace_indices]),
                ],
            )
        )

    fig.update_layout(
        title=title,
        template="plotly_white",
        height=1000,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(t=120),
        updatemenus=updatemenus,
    )
    return fig


def write_html(report: Report, path: str, title: str = "Backtest Report") -> None:
    render(report, title=title).write_html(path)
