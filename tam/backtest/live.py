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
from typing import Callable, Dict, Optional

import pandas as pd

from .report import Report
from .visualization import RenderOptions, render


def report_from_checkpoint(checkpoint_path: str) -> Optional[Report]:
    """Reconstruct a partial Report from whatever's in the checkpoint right
    now, or None if it doesn't exist yet (e.g. day 1 hasn't completed) --
    also None if it existed a moment ago but is gone by the time we get to
    read it: the writer (BacktestHarness.run(), on its own thread) unlinks
    the checkpoint on a clean finish, and a slow/laggy filesystem (a
    Drive-mounted config directory in Colab, say) widens the window between
    this function's own exists() check and open() enough for that race to
    actually happen in practice, not just in theory."""
    path = Path(checkpoint_path)
    if not path.exists():
        return None
    try:
        with path.open("rb") as handle:
            state = pickle.load(handle)
    except FileNotFoundError:
        return None

    trades = [
        {**trade, "portfolio": portfolio_id}
        for portfolio_id, portfolio_state in state["portfolios"].items()
        for trade in portfolio_state["trades"]
    ]
    return Report(state["snapshots"], trades, state.get("annotations", []))


def live_render(
    next_frame: Callable[[], Optional[Report]],
    title: str = "Backtest (live)",
    poll_seconds: float = 2.0,
    options: Optional[RenderOptions] = None,
    ticker_colors: Optional[Dict[str, str]] = None,
    prices: Optional[Dict[str, "pd.Series"]] = None,
    should_continue: Callable[[], bool] = lambda: True,
) -> None:
    """Generalized notebook clear_output()/display() redraw loop: pulls
    `next_frame()` every `poll_seconds` (`None` = "nothing new yet, keep
    showing the last frame") and redraws it, until `should_continue()` is
    False -- then does one final redraw and returns. Blocking.

    This is exactly what NotebookPresenter.run_live already does for a
    running BacktestHarness (there, `next_frame` is a closure around
    report_from_checkpoint() and `should_continue` is a background thread's
    `is_alive`) -- pulled out here so ANY Report-producing callable works the
    same way, with no BacktestHarness/Strategy/checkpoint file required: e.g.
    a vectorized numpy backtest extending a Series each tick, wrapped as
    `lambda: Report.from_curves({"my_strategy": running_series})`, redraws
    exactly like a real backtest's live view.

    Needs the `notebook` extra (IPython) outside a real notebook kernel, same
    as run_backtest(..., live=True)."""
    import time

    try:
        from IPython.display import clear_output, display
    except ImportError as exc:
        raise ImportError(
            "live_render needs IPython's display utilities -- always present already inside a real "
            "notebook kernel (Jupyter, Colab, ...); outside one, install the `notebook` extra: "
            'pip install "tam-quant[notebook]".'
        ) from exc

    last_report: Optional[Report] = None

    def _redraw(report: Optional[Report]) -> None:
        nonlocal last_report
        if report is not None:
            last_report = report
        if last_report is None or not last_report.snapshots:
            return
        fig = render(last_report, title=title, ticker_colors=ticker_colors, prices=prices, options=options)
        clear_output(wait=True)
        display(fig)

    while should_continue():
        _redraw(next_frame())
        time.sleep(poll_seconds)

    _redraw(next_frame())


def serve(
    checkpoint_path: Optional[str] = None,
    title: str = "Backtest (live)",
    poll_seconds: float = 3.0,
    port: int = 8050,
    verbose: bool = False,
    ticker_colors: Optional[Dict[str, str]] = None,
    prices: Optional[Dict[str, "pd.Series"]] = None,
    jupyter_mode: Optional[str] = None,
    options: Optional[RenderOptions] = None,
    next_frame: Optional[Callable[[], Optional[Report]]] = None,
) -> None:
    """Blocking (unless `jupyter_mode` says otherwise -- see below): serves a
    dashboard at http://127.0.0.1:<port> that re-reads the checkpoint every
    `poll_seconds` and redraws the same figure visualization.render() would
    produce for the final report -- just from whatever's completed so far.
    Keeps showing the last good read after the checkpoint is removed on a
    clean finish, rather than reverting to blank.

    `next_frame`, if given, replaces the checkpoint-file polling entirely --
    same Report-producing callable live_render() takes, so a fully custom
    live Dash view (no BacktestHarness/checkpoint file at all) works via
    `serve(next_frame=my_callable)`. `checkpoint_path` is then optional,
    used only for the "backtest finished" status line below; omit it and
    that line just never appears. Exactly one of `checkpoint_path`/
    `next_frame` must be given.

    A real browser-tab dashboard, via a real Dash server -- this is what
    --mode live (the CLI) uses (jupyter_mode=None, the default: blocks,
    serves a normal HTTP dashboard for a separate browser tab).

    `jupyter_mode` renders inline in a notebook cell instead (passed straight
    through to Dash's own `app.run()`) -- this is NOT what
    tam.backtest.runner.run_backtest(..., live=True) uses by default: Dash's
    own inline-in-notebook support depends on correctly detecting a hosted
    notebook's reverse proxy, and Colab specifically doesn't support that
    (confirmed both by Dash's own source -- jupyter_dash.
    infer_jupyter_proxy_config is a documented no-op "when ... in_colab" --
    and empirically, rendering a completely blank cell). run_backtest's
    default live view instead redraws the chart via IPython's own
    clear_output()/display() (see tam/backtest/presenter.py's
    NotebookPresenter, or live_render() directly), which doesn't depend on
    Dash or proxy detection at all. This jupyter_mode path is kept available
    as an explicit opt-in (run_backtest(..., render_mode="native_dash"), see
    DashNotebookPresenter) for classic Jupyter/JupyterLab -- where Dash's own
    docs describe this as fully supported -- or in case Colab's own
    Dash-in-notebook support improves later.

    `prices`, if given, is the same already-fetched historical price data
    write_html()'s optional top panel supports -- passed in whole, but
    render() itself truncates each series to whatever date the equity/
    drawdown panels have reached so far, so the price panel builds up in
    lockstep with them instead of spoiling the ending upfront.

    Flask/Werkzeug's per-request access log (one line per poll, forever) is
    silenced by default -- it drowns out the rich progress display the
    backtest itself is drawing in the same terminal. Pass verbose=True (or
    --log-level verbose on the CLI) to see it, e.g. while debugging the live
    server itself."""
    if (checkpoint_path is None) == (next_frame is None):
        raise ValueError("serve() needs exactly one of checkpoint_path or next_frame")
    if next_frame is None:
        next_frame = lambda: report_from_checkpoint(checkpoint_path)  # noqa: E731

    try:
        import dash
        from dash import dcc, html
        from dash.dependencies import Input, Output
    except ImportError as exc:
        raise ImportError(
            "native_dash / --mode live needs the `live` extra (adds dash): run `uv sync --extra live` "
            "and retry, or in a notebook `!pip install -q \"tam-quant[live]\"` -- the `notebook` extra "
            "alone does NOT include dash. If you're in a notebook and don't specifically need a real "
            "Dash server, render_mode=\"clear_output\" (the default) needs no extra dependency at all."
        ) from exc

    if jupyter_mode is not None:
        # A no-op outside a notebook context, and (per Dash's own source)
        # also a no-op specifically in Colab -- but harmless either way, and
        # still useful for classic Jupyter/JupyterLab behind a proxy.
        from dash import jupyter_dash

        jupyter_dash.infer_jupyter_proxy_config()

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
        fresh = next_frame()
        if fresh is not None:
            last_report["value"] = fresh
        report = last_report["value"]

        if report is None or not report.snapshots:
            return {}, "waiting for the first completed day..."

        fig = render(report, title=title, ticker_colors=ticker_colors, prices=prices, options=options)
        frame = report.to_frame()
        last_date = frame["date"].max()
        day_count = frame["date"].nunique()
        status = f"through {last_date} — day {day_count}"
        if checkpoint_path is not None and not Path(checkpoint_path).exists():
            status += " -- backtest finished, showing final state"
        return fig, status

    run_kwargs = {"port": port, "debug": False, "threaded": True}
    if jupyter_mode is not None:
        run_kwargs["jupyter_mode"] = jupyter_mode
    app.run(**run_kwargs)
