"""How a backtest's progress and results get shown to the user -- pulled out
behind an interface so the same underlying driving logic in runner.py works
identically whether it's presented in a terminal (Rich progress bars, a
static HTML file written to disk) or a notebook (no bars; the chart renders
directly in the cell's output) without runner.py ever branching on "am I in
a notebook" -- it just calls whichever Presenter it's given.

Two concrete presenters ship here: CliPresenter (examples/backtest.py) and
NotebookPresenter (tam.backtest.runner.run_backtest, for Colab/Jupyter). A
third presentation style (a different notebook widget library, a web
service's own progress UI, ...) is just another Presenter subclass -- nothing
in runner.py needs to change to support it.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Dict, Optional

if TYPE_CHECKING:
    import pandas as pd

    from .harness import BacktestHarness
    from .report import Report


class Presenter(ABC):
    @abstractmethod
    def run_batch(
        self,
        harness: "BacktestHarness",
        total_days: int,
        checkpoint_path: Optional[str],
        checkpoint_every: int,
    ) -> "Report":
        """Drive harness.run() to completion, presenting progress however
        this presenter does that (e.g. Rich bars, nothing at all), and
        return the finished Report. Does not display the report itself --
        see show_report, called separately by the caller once this returns."""

    @abstractmethod
    def show_report(
        self,
        report: "Report",
        title: str,
        ticker_colors: Dict[str, str],
        prices: Dict[str, "pd.Series"],
    ) -> None:
        """Present a finished Report -- write an HTML file (CliPresenter),
        render inline (NotebookPresenter), or anything else a presenter
        wants to do with it."""

    @abstractmethod
    def run_live(
        self,
        harness: "BacktestHarness",
        total_days: int,
        checkpoint_path: str,
        checkpoint_every: int,
        title: str,
        ticker_colors: Dict[str, str],
        prices: Dict[str, "pd.Series"],
        port: int,
        verbose: bool,
    ) -> None:
        """Start the harness running (typically on a background thread) and
        present a continuously-refreshing view of its progress -- an
        external Dash server (CliPresenter) or Dash's own inline notebook
        rendering (NotebookPresenter), or anything else a presenter wants."""


class CliPresenter(Presenter):
    """Terminal presentation: a two-row Rich progress display (overall
    day-count, plus whatever a strategy reports via tam.status underneath --
    loading a model, fine-tuning gen N with its own iter progress, etc.),
    a printed summary table, a static HTML file written to `report_path`,
    and (run_live) a real Dash server in an actual browser tab."""

    def __init__(self, report_path: str):
        self._report_path = report_path

    def run_batch(self, harness, total_days, checkpoint_path, checkpoint_every):
        from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn

        from ..status import set_reporter
        from .harness import Progress as RunProgress

        columns = (
            TextColumn("[bold blue]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
        )
        with Progress(*columns) as progress_ui:
            day_task = progress_ui.add_task("Backtest", total=total_days)
            activity_task = progress_ui.add_task("idle", total=None)

            def on_progress(run_progress: RunProgress) -> None:
                progress_ui.update(
                    day_task, completed=run_progress.day_index, description=f"Backtest — {run_progress.current_date}"
                )

            def reporter(text: str, current, total) -> None:
                kwargs = {"description": text}
                if total is not None:
                    kwargs["total"] = total
                if current is not None:
                    kwargs["completed"] = current
                progress_ui.update(activity_task, **kwargs)

            set_reporter(reporter)
            try:
                report = harness.run(
                    on_progress=on_progress,
                    checkpoint_path=checkpoint_path,
                    checkpoint_every=checkpoint_every,
                )
            finally:
                set_reporter(None)

        print(report.summary_all())
        return report

    def show_report(self, report, title, ticker_colors, prices):
        from pathlib import Path

        from .visualization import write_html

        report_path = Path(self._report_path)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        write_html(report, str(report_path), title=title, ticker_colors=ticker_colors, prices=prices)
        print(f"Report written to {report_path}")

    def run_live(self, harness, total_days, checkpoint_path, checkpoint_every, title, ticker_colors, prices, port, verbose):
        import sys
        import threading

        from .live import serve

        def _run_backtest() -> None:
            # Same batch flow as run_batch + show_report -- a live run still
            # leaves the usual static HTML report behind once the background
            # run finishes, exactly like a plain batch run would.
            report = harness.run(checkpoint_path=checkpoint_path, checkpoint_every=checkpoint_every)
            self.show_report(report, title, ticker_colors, prices)

        thread = threading.Thread(target=_run_backtest, daemon=True)
        thread.start()

        print(f"Live view: http://127.0.0.1:{port}  (backtest running in the background)", file=sys.stderr)
        serve(checkpoint_path, title=title, ticker_colors=ticker_colors, prices=prices, port=port, verbose=verbose)


class NotebookPresenter(Presenter):
    """Notebook/Colab presentation: no progress bars (a notebook cell is a
    poor place for an animated terminal UI, and every notebook host redraws
    that differently or not at all) -- run_batch is silent until it returns.
    The finished report renders directly in the current cell's output via
    Plotly's own rich-display protocol (the same thing fig.show() uses under
    the hood), rather than being written to a file for the user to
    separately open.

    run_live deliberately does NOT use Dash (unlike CliPresenter, which opens
    a real Dash server for a real browser tab) -- Dash's own inline-in-
    notebook support depends on detecting a hosted notebook's reverse proxy
    correctly, and Colab specifically is a documented no-op case for Dash's
    own proxy-autodetection helper (jupyter_dash.infer_jupyter_proxy_config:
    "No op when ... in_colab"), which in practice reproduces as a
    completely blank cell -- no banner, no graph, nothing (confirmed
    empirically, not just from reading Dash's source). Instead, run_live
    polls the same checkpoint file Dash's live server would have, and
    redraws the SAME figure render() always produces by re-displaying it
    into the same notebook output slot via IPython's own
    display()/update_display(display_id=...) -- the same rich-display
    mechanism the non-live path already uses successfully, just refreshed
    periodically instead of drawn once. No server, no iframe, no separate
    URL, no proxy to misdetect."""

    _POLL_SECONDS = 2.0

    def run_batch(self, harness, total_days, checkpoint_path, checkpoint_every):
        return harness.run(checkpoint_path=checkpoint_path, checkpoint_every=checkpoint_every)

    def show_report(self, report, title, ticker_colors, prices):
        from .visualization import render

        render(report, title=title, ticker_colors=ticker_colors, prices=prices).show()

    def run_live(self, harness, total_days, checkpoint_path, checkpoint_every, title, ticker_colors, prices, port, verbose):
        import threading
        import time
        import uuid

        try:
            from IPython.display import display, update_display
        except ImportError as exc:
            raise ImportError(
                "run_backtest(..., live=True) needs IPython's display utilities -- always present "
                "already inside a real notebook kernel (Jupyter, Colab, ...); outside one, install "
                "the `notebook` extra: pip install \"tam-quant[notebook]\"."
            ) from exc

        from .live import report_from_checkpoint
        from .visualization import render

        result: dict = {}

        def _run_backtest() -> None:
            result["report"] = harness.run(checkpoint_path=checkpoint_path, checkpoint_every=checkpoint_every)

        thread = threading.Thread(target=_run_backtest, daemon=True)
        thread.start()

        # A fresh id per call -- this is the notebook output slot every
        # display()/update_display() call below redraws in place, rather
        # than appending a new plot underneath on every refresh.
        display_id = f"tam-live-{uuid.uuid4().hex}"
        shown = False

        def _redraw(report) -> None:
            nonlocal shown
            if report is None or not report.snapshots:
                return
            fig = render(report, title=title, ticker_colors=ticker_colors, prices=prices)
            if shown:
                update_display(fig, display_id=display_id)
            else:
                display(fig, display_id=display_id)
                shown = True

        while thread.is_alive():
            _redraw(report_from_checkpoint(checkpoint_path))
            time.sleep(self._POLL_SECONDS)

        thread.join()
        _redraw(result.get("report"))
