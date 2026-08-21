"""Live-updating view of an in-progress backtest.

Polls the same checkpoint file BacktestHarness.run(checkpoint_path=...) already
writes every `checkpoint_every` days -- the backtest loop itself needs zero
awareness that anything is watching it. Kept out of visualization.py/report.py
so those stay dependency-light; this module needs the `live` extra
(`uv sync --extra live`, adds `dash`) since most runs just want the static
HTML report from write_html and shouldn't need Dash installed for that.
"""
from __future__ import annotations

import logging
import pickle
from pathlib import Path
from typing import Optional

from .report import Report
from .visualization import render


def report_from_checkpoint(checkpoint_path: str) -> Optional[Report]:
    """Reconstruct a partial Report from whatever's in the checkpoint right
    now, or None if it doesn't exist yet (e.g. day 1 hasn't completed)."""
    path = Path(checkpoint_path)
    if not path.exists():
        return None
    with path.open("rb") as handle:
        state = pickle.load(handle)

    trades = [
        {**trade, "portfolio": portfolio_id}
        for portfolio_id, portfolio_state in state["portfolios"].items()
        for trade in portfolio_state["trades"]
    ]
    return Report(state["snapshots"], trades)


def serve(
    checkpoint_path: str,
    title: str = "Backtest (live)",
    poll_seconds: float = 3.0,
    port: int = 8050,
    verbose: bool = False,
) -> None:
    """Blocking: serves a dashboard at http://127.0.0.1:<port> that re-reads
    the checkpoint every `poll_seconds` and redraws the same figure
    visualization.render() would produce for the final report -- just from
    whatever's completed so far. Keeps showing the last good read after the
    checkpoint is removed on a clean finish, rather than reverting to blank.

    Flask/Werkzeug's per-request access log (one line per poll, forever) is
    silenced by default -- it drowns out the rich progress display the
    backtest itself is drawing in the same terminal. Pass verbose=True (or
    --log-level verbose on the CLI) to see it, e.g. while debugging the server
    itself."""
    try:
        import dash
        from dash import dcc, html
        from dash.dependencies import Input, Output
    except ImportError as exc:
        raise ImportError(
            "`--mode live` needs the `live` extra: run `uv sync --extra live` (adds dash) and retry."
        ) from exc

    if not verbose:
        logging.getLogger("werkzeug").setLevel(logging.ERROR)

    app = dash.Dash(__name__)
    app.layout = html.Div(
        [
            html.Div(id="status", style={"fontFamily": "monospace", "padding": "8px"}),
            dcc.Graph(id="figure", style={"height": "95vh"}),
            dcc.Interval(id="tick", interval=int(poll_seconds * 1000)),
        ]
    )
    last_report: dict = {"value": None}

    @app.callback(Output("figure", "figure"), Output("status", "children"), Input("tick", "n_intervals"))
    def _refresh(_):
        fresh = report_from_checkpoint(checkpoint_path)
        if fresh is not None:
            last_report["value"] = fresh
        report = last_report["value"]

        if report is None or not report.snapshots:
            return {}, "waiting for the first completed day..."

        fig = render(report, title=title)
        last_date = report.to_frame()["date"].max()
        status = f"through {last_date}"
        if not Path(checkpoint_path).exists():
            status += " -- backtest finished, showing final state"
        return fig, status

    app.run(port=port, debug=False, threaded=True)
    app.layout = html.Div(
        [
            html.Div(id="status", style={"fontFamily": "monospace", "padding": "8px"}),
            dcc.Graph(id="figure", style={"height": "95vh"}),
            dcc.Interval(id="tick", interval=int(poll_seconds * 1000)),
        ]
    )
    last_report: dict = {"value": None}

    @app.callback(Output("figure", "figure"), Output("status", "children"), Input("tick", "n_intervals"))
    def _refresh(_):
        fresh = report_from_checkpoint(checkpoint_path)
        if fresh is not None:
            last_report["value"] = fresh
        report = last_report["value"]

        if report is None or not report.snapshots:
            return {}, "waiting for the first completed day..."

        fig = render(report, title=title)
        last_date = report.to_frame()["date"].max()
        status = f"through {last_date}"
        if not Path(checkpoint_path).exists():
            status += " -- backtest finished, showing final state"
        return fig, status

    app.run(port=port, debug=False, threaded=True)
