"""Renders a Report into a clean, comparison-ready Plotly dashboard.

Kept separate from report.py so computing metrics never requires plotly installed;
only call into this module when you actually want a chart.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Union

import pandas as pd
import plotly.colors
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from .report import Report

_PALETTE = plotly.colors.qualitative.Plotly


@dataclass(frozen=True)
class RenderOptions:
    """Chart-rendering knobs, kept as one object so Presenters/config can pass
    them around as a unit instead of every new knob growing render()'s and
    every Presenter constructor's parameter list. Bare `RenderOptions()` (the
    default everywhere it's accepted) reproduces today's behavior exactly."""

    show_trades_default: bool = True
    height: Optional[int] = None
    template: str = "plotly_white"

_PERCENT_METRICS = {"total_return", "cagr", "volatility", "max_drawdown"}
_CURRENCY_METRICS = {"start_value", "end_value"}
_INT_METRICS = {"num_trades"}
_METRIC_LABELS = {
    "start_value": "Start Value",
    "end_value": "End Value",
    "total_return": "Total Return",
    "cagr": "CAGR",
    "volatility": "Volatility",
    "sharpe": "Sharpe",
    "max_drawdown": "Max Drawdown",
    "calmar": "Calmar",
    "num_trades": "# Trades",
}

# Public: callers that know a portfolio's long/short ticker pair (e.g. the CLI,
# reading it straight out of strategy config) can build a {ticker: color} map
# with these and pass it into render()/write_html() as `ticker_colors`, so
# trade markers are colored by *which vehicle*, not by that day's buy/sell
# direction. This module deliberately has no hardcoded ticker knowledge of its
# own -- "TQQQ is bullish" is a fact about a specific strategy's config, not
# something a generic report renderer should assume.
BUY_COLOR = "#2ca02c"
SELL_COLOR = "#d62728"
_NEUTRAL_COLOR = "#7f7f7f"


def _fill_rgba(hex_color: str, alpha: float = 0.2) -> str:
    """`hex_color` (e.g. "#636EFA") as a semi-transparent rgba() string, for a
    fill that matches a trace's own line color instead of a fixed one."""
    hex_color = hex_color.lstrip("#")
    r, g, b = (int(hex_color[i : i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha})"


def _format_qty(qty: float) -> str:
    """Large share counts (leveraged ETFs at penny prices can mean hundreds of
    millions of shares) abbreviated for a readable hover tooltip: 271372367 ->
    "271.4M". Small counts are shown exactly, unrounded."""
    abs_qty = abs(qty)
    for threshold, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if abs_qty >= threshold:
            return f"{qty / threshold:.1f}{suffix}"
    return f"{qty:g}"


def _side_label(side) -> str:
    return side.value if hasattr(side, "value") else str(side)


def _trade_marker_trace(
    report: Report,
    portfolio_id: str,
    normalized_curve: pd.Series,
    ticker_colors: Dict[str, str],
    fallback_color: str,
    visible_default: bool,
):
    """One marker per trading day this portfolio traded, positioned on its own
    (indexed) equity line. Multiple trades on the same day are grouped into a
    single marker whose hover text lists every one of them.

    Color = the ticker being ended up in that day (`ticker_colors[ticker]` if
    given, else `fallback_color`) -- not that day's net buy/sell direction,
    since a same-side resize (sell 100%, rebuy at a new %) is a real SELL
    order but isn't "bearish" the way a flip to the other ticker is. Arrow
    direction = whether that position got bigger or smaller: up for a fresh
    entry (from cash, or a flip into a ticker held at 0) or a resize where the
    rebuy is larger than what was just sold; down for an exit to cash or a
    resize into something smaller."""
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

        lines = []
        buy_row = None
        sell_row = None
        for _, row in group.iterrows():
            side_label = _side_label(row["side"])
            lines.append(f"{side_label} {_format_qty(row['qty'])} {row['ticker']} @ ${row['price']:,.2f}")
            if side_label == "BUY":
                buy_row = row
            elif side_label == "SELL":
                sell_row = row

        if buy_row is not None:
            target_ticker = buy_row["ticker"]
            if sell_row is not None and sell_row["ticker"] == target_ticker:
                buy_notional = buy_row["qty"] * buy_row["price"]
                sell_notional = sell_row["qty"] * sell_row["price"]
                symbol = (
                    "triangle-up" if buy_notional > sell_notional
                    else "triangle-down" if buy_notional < sell_notional
                    else "diamond"
                )
            else:
                symbol = "triangle-up"  # fresh entry from cash, or a flip into a ticker held at 0 -- an increase
        elif sell_row is not None:
            target_ticker = sell_row["ticker"]
            symbol = "triangle-down"  # exiting to cash -- nothing bought
        else:
            continue  # a trade group with neither a buy nor a sell shouldn't happen, but don't plot garbage if it does

        color = ticker_colors.get(target_ticker, fallback_color)
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
        marker=dict(symbol=symbols, size=13, color=colors, line=dict(width=1, color=fallback_color)),
        name=f"{portfolio_id} trades",
        hovertext=texts,
        hoverinfo="text",
        visible=visible_default,
    )


def render(
    report: Report,
    title: str = "Backtest Report",
    ticker_colors: Optional[Dict[str, str]] = None,
    prices: Optional[Dict[str, pd.Series]] = None,
    options: Optional[RenderOptions] = None,
) -> go.Figure:
    """Build the dashboard figure: normalized equity curves, drawdown, and a
    metrics table -- plus an optional raw ticker-price panel above the equity
    chart when `prices` is given (a mapping of ticker -> close-price Series).
    Each ticker gets its own legend entry there, so which ones are shown is a
    click away, same as the existing trade-marker toggle. Each series is
    truncated to `report`'s last completed snapshot date, so in --mode live
    the price panel builds up day-by-day alongside the equity/drawdown panels
    instead of showing its full (already-fetched) history immediately; a
    finished batch report's last snapshot date is the full range, so this is
    a no-op there. All panels share one x-axis, so zooming/panning any one of
    them (price, equity, drawdown) moves the others with it.

    `ticker_colors`: optional {ticker: color} map for trade markers (e.g.
    {"TQQQ": BUY_COLOR, "SQQQ": SELL_COLOR} for a long/short-pair strategy).
    A ticker not in the map falls back to that portfolio's own line color.

    `report.annotations` (populated by strategies calling Strategy.annotate(),
    e.g. LLMTradingStrategy marking a LoRA fine-tune) are drawn as dotted
    vertical lines on the equity chart, labeled with each one's text.

    `options`: a RenderOptions -- template/height/whether the trade-marker
    toggle starts shown or hidden. Defaults to RenderOptions() (today's
    behavior) when omitted."""
    options = options or RenderOptions()
    ticker_colors = ticker_colors or {}
    portfolio_ids = report.portfolio_ids()
    colors = {pid: _PALETTE[i % len(_PALETTE)] for i, pid in enumerate(portfolio_ids)}

    has_prices = bool(prices)
    titles = (["Ticker Prices"] if has_prices else []) + [
        "Relative Performance (Indexed to 100)",
        "Drawdown",
        "Summary Metrics",
    ]
    row_heights = [0.22, 0.33, 0.20, 0.25] if has_prices else [0.45, 0.25, 0.30]
    specs = [[{"type": "xy"}]] * (len(titles) - 1) + [[{"type": "table"}]]

    fig = make_subplots(
        rows=len(titles),
        cols=1,
        row_heights=row_heights,
        vertical_spacing=0.08,
        specs=specs,
        subplot_titles=tuple(titles),
        shared_xaxes=True,
    )

    row = 1
    if has_prices:
        # Truncated to whatever the equity/drawdown panels below are already
        # limited to (the last completed snapshot date) -- in --mode live this
        # is mid-run, so the price panel builds up day-by-day in lockstep with
        # them instead of spoiling the ending by showing the full, already-
        # fetched history immediately. A finished batch report's last snapshot
        # date IS the full range, so this is a no-op there.
        cutoff = None
        frame = report.to_frame()
        if not frame.empty:
            cutoff = pd.Timestamp(frame["date"].max())

        price_colors = {ticker: _PALETTE[i % len(_PALETTE)] for i, ticker in enumerate(prices)}
        for ticker, series in prices.items():
            visible = series[series.index <= cutoff] if cutoff is not None else series
            fig.add_trace(
                go.Scatter(
                    x=visible.index,
                    y=visible.values,
                    mode="lines",
                    name=ticker,
                    line=dict(color=price_colors[ticker]),
                ),
                row=row,
                col=1,
            )
        fig.update_yaxes(title_text="Price ($, log scale)", type="log", row=row, col=1)
        row += 1

    equity_row = row
    normalized_curves = {}
    for portfolio_id in portfolio_ids:
        curve = report.equity_curve(portfolio_id)
        normalized = curve / curve.iloc[0] * 100
        normalized_curves[portfolio_id] = normalized
        fig.add_trace(
            go.Scatter(
                x=normalized.index,
                y=normalized.values,
                mode="lines",
                name=portfolio_id,
                line=dict(color=colors[portfolio_id]),
            ),
            row=equity_row,
            col=1,
        )
    row += 1

    for annotation in report.annotations:
        ann_date = annotation.get("date")
        if ann_date is None:
            continue
        x = pd.Timestamp(ann_date)
        fig.add_vline(
            x=x,
            line_dash="dot",
            line_color=_NEUTRAL_COLOR,
            line_width=1,
            opacity=0.6,
            row=equity_row,
            col=1,
        )
        # Anchored to the top of the *plot's own* y-domain (not "paper"), so the
        # label hangs down into the plot -- the subplot title above it lives in
        # paper space at that same y=1 boundary and would otherwise sit right on
        # top of an add_vline(annotation_text=...) default placement.
        fig.add_annotation(
            x=x,
            y=1,
            yref="y domain",
            yanchor="top",
            xanchor="left",
            text=annotation.get("label", ""),
            showarrow=False,
            font=dict(size=10, color=_NEUTRAL_COLOR),
            row=equity_row,
            col=1,
        )

    drawdown_row = row
    for portfolio_id in portfolio_ids:
        drawdown = report.drawdown_curve(portfolio_id) * 100
        fig.add_trace(
            go.Scatter(
                x=drawdown.index,
                y=drawdown.values,
                mode="lines",
                name=f"{portfolio_id} drawdown",
                showlegend=False,
                line=dict(color=colors[portfolio_id]),
                fill="tozeroy",
                fillcolor=_fill_rgba(colors[portfolio_id]),
            ),
            row=drawdown_row,
            col=1,
        )
    row += 1

    table_row = row

    trade_trace_indices = []
    for portfolio_id in portfolio_ids:
        marker_trace = _trade_marker_trace(
            report, portfolio_id, normalized_curves[portfolio_id], ticker_colors, colors[portfolio_id],
            options.show_trades_default,
        )
        if marker_trace is not None:
            fig.add_trace(marker_trace, row=equity_row, col=1)
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
        elif col in _INT_METRICS:
            fmt = lambda v: f"{v:.0f}"
        else:
            fmt = lambda v: f"{v:.2f}"
        cells.append([fmt(v) for v in summary[col]])

    fig.add_trace(
        go.Table(
            header=dict(values=header, fill_color="#1f2a44", font=dict(color="white"), align="left"),
            cells=dict(values=cells, align="left"),
        ),
        row=table_row,
        col=1,
    )

    fig.update_yaxes(title_text="Indexed value (start = 100)", row=equity_row, col=1)
    fig.update_yaxes(title_text="Drawdown %", row=drawdown_row, col=1)

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
        template=options.template,
        height=options.height or (1250 if has_prices else 1000),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(t=120),
        updatemenus=updatemenus,
    )
    return fig


def write_html(
    report: Report,
    path: str,
    title: str = "Backtest Report",
    ticker_colors: Optional[Dict[str, str]] = None,
    prices: Optional[Dict[str, pd.Series]] = None,
    options: Optional[RenderOptions] = None,
) -> None:
    render(report, title=title, ticker_colors=ticker_colors, prices=prices, options=options).write_html(path)


def render_curves(
    curves: Union[pd.DataFrame, Dict[str, pd.Series]],
    trades: Optional[pd.DataFrame] = None,
    annotations: Optional[list] = None,
    title: str = "Backtest Report",
    ticker_colors: Optional[Dict[str, str]] = None,
    prices: Optional[Dict[str, pd.Series]] = None,
    options: Optional[RenderOptions] = None,
) -> go.Figure:
    """render(), straight from equity curves you already have -- no
    BacktestHarness/Strategy/Portfolio involved. `curves`/`trades`/`annotations`
    are exactly Report.from_curves()'s own arguments (see there for the exact
    shapes accepted and the "designed for a handful of named curves, not an
    unlabeled sweep" scope note); `Report` stays an implementation detail you
    never have to construct yourself:

        render_curves({"my_strategy": wealth_series}).show()
    """
    return render(Report.from_curves(curves, trades, annotations), title=title, ticker_colors=ticker_colors, prices=prices, options=options)
