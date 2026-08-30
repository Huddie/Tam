"""How a backtest's progress and results get shown to the user -- pulled out
behind an interface so the same underlying driving logic in runner.py works
identically whether it's presented in a terminal (Rich progress bars, a
static HTML file written to disk) or a notebook (no bars; the chart renders
directly in the cell's output) without runner.py ever branching on "am I in
a notebook" -- it just calls whichever Presenter it's given.

Two concrete presenters ship here: CliPresenter (examples/backtest.py) and
NotebookPresenter (tam.backtest.runner.run_backtest, for Colab/Jupyter),
plus DashNotebookPresenter, an opt-in alternative to NotebookPresenter's
live view (see its own docstring). A different presentation style (a
different notebook widget library, a web service's own progress UI, ...) is
just another Presenter subclass -- nothing in runner.py needs to change to
support it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from ..registry import Registry

if TYPE_CHECKING:
    import pandas as pd

    from .harness import BacktestHarness
    from .report import Report
    from .visualization import RenderOptions


class Presenter(ABC):
    @abstractmethod
    def run_batch(
        self,
        harness: BacktestHarness,
        total_days: int,
        checkpoint_path: str | None,
        checkpoint_every: int,
    ) -> Report:
        """Drive harness.run() to completion, presenting progress however
        this presenter does that (e.g. Rich bars, nothing at all), and
        return the finished Report. Does not display the report itself --
        see show_report, called separately by the caller once this returns."""

    @abstractmethod
    def show_report(
        self,
        report: Report,
        title: str,
        ticker_colors: dict[str, str],
        prices: dict[str, pd.Series],
    ) -> None:
        """Present a finished Report -- write an HTML file (CliPresenter),
        render inline (NotebookPresenter), or anything else a presenter
        wants to do with it."""

    @abstractmethod
    def run_live(
        self,
        harness: BacktestHarness,
        total_days: int,
        checkpoint_path: str,
        checkpoint_every: int,
        title: str,
        ticker_colors: dict[str, str],
        prices: dict[str, pd.Series],
        port: int,
        verbose: bool,
    ) -> None:
        """Start the harness running (typically on a background thread) and
        present a continuously-refreshing view of its progress -- an
        external Dash server (CliPresenter) or Dash's own inline notebook
        rendering (NotebookPresenter), or anything else a presenter wants."""


@Registry.register(Presenter, "cli")
class CliPresenter(Presenter):
    """Terminal presentation: a two-row Rich progress display (overall
    day-count, plus whatever a strategy reports via tam.status underneath --
    loading a model, fine-tuning gen N with its own iter progress, etc.),
    a printed summary table, a static HTML file written to `report_path`,
    and (run_live) a real Dash server in an actual browser tab."""

    def __init__(self, report_path: str, poll_seconds: float = 3.0, render_options: RenderOptions | None = None):
        from .visualization import RenderOptions

        self._report_path = report_path
        self._poll_seconds = poll_seconds
        self._render_options = render_options or RenderOptions()

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
        write_html(
            report,
            str(report_path),
            title=title,
            ticker_colors=ticker_colors,
            prices=prices,
            options=self._render_options,
        )
        print(f"Report written to {report_path}")

    def run_live(
        self, harness, total_days, checkpoint_path, checkpoint_every, title, ticker_colors, prices, port, verbose
    ):
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
        serve(
            checkpoint_path,
            title=title,
            poll_seconds=self._poll_seconds,
            ticker_colors=ticker_colors,
            prices=prices,
            port=port,
            verbose=verbose,
            options=self._render_options,
        )


@Registry.register(Presenter, "clear_output")
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
    empirically, not just from reading Dash's source).

    It also does NOT use IPython's display(display_id=...)/update_display()
    -- that's the textbook-correct way to redraw a rich output in place, but
    Colab's own frontend doesn't reliably implement update_display() for
    HTML/JS-backed rich content like a Plotly figure (confirmed empirically:
    it kept appending a new chart underneath the old one on every refresh,
    instead of replacing it -- a known Colab gap, not present in classic
    Jupyter/JupyterLab). Instead, run_live polls the same checkpoint file
    Dash's live server would have, and on every refresh clears the cell's
    entire output and redraws the chart from scratch via
    IPython.display.clear_output(wait=True) -- the same trick every "live
    matplotlib in Colab" tutorial uses for exactly this reason. wait=True
    defers the actual clear until the instant before the new output is
    ready, so there's no visible blank flash between frames. No server, no
    iframe, no separate URL, no proxy or display_id to misbehave."""

    def __init__(self, poll_seconds: float = 2.0, render_options: RenderOptions | None = None):
        from .visualization import RenderOptions

        self._poll_seconds = poll_seconds
        self._render_options = render_options or RenderOptions()

    def run_batch(self, harness, total_days, checkpoint_path, checkpoint_every):
        return harness.run(checkpoint_path=checkpoint_path, checkpoint_every=checkpoint_every)

    def show_report(self, report, title, ticker_colors, prices):
        from .visualization import render

        render(report, title=title, ticker_colors=ticker_colors, prices=prices, options=self._render_options).show()

    def run_live(
        self, harness, total_days, checkpoint_path, checkpoint_every, title, ticker_colors, prices, port, verbose
    ):
        import threading

        try:
            from IPython.display import (  # noqa: F401 -- availability check only; live_render below does the real import
                clear_output,
                display,
            )
        except ImportError as exc:
            raise ImportError(
                "run_backtest(..., live=True) needs IPython's display utilities -- always present "
                "already inside a real notebook kernel (Jupyter, Colab, ...); outside one, install "
                'the `notebook` extra: pip install "tam-quant[notebook]".'
            ) from exc

        from .live import live_render, report_from_checkpoint

        result: dict = {}

        def _run_backtest() -> None:
            result["report"] = harness.run(checkpoint_path=checkpoint_path, checkpoint_every=checkpoint_every)

        thread = threading.Thread(target=_run_backtest, daemon=True)
        thread.start()

        def next_frame():
            # Once the background thread finishes, prefer the harness's own
            # returned Report over one more checkpoint read -- the checkpoint
            # file is already unlinked by then (harness.run()'s own clean-finish
            # cleanup), and even before that, a poll mid-write could catch a
            # partial/stale day depending on checkpoint_every.
            if not thread.is_alive() and "report" in result:
                return result["report"]
            return report_from_checkpoint(checkpoint_path)

        live_render(
            next_frame,
            title=title,
            poll_seconds=self._poll_seconds,
            options=self._render_options,
            ticker_colors=ticker_colors,
            prices=prices,
            should_continue=thread.is_alive,
        )
        thread.join()


@Registry.register(Presenter, "native_dash")
class DashNotebookPresenter(NotebookPresenter):
    """Opt-in alternative to NotebookPresenter's default clear_output-based
    live view (see NotebookPresenter's own docstring for why that's the
    default, not this) -- embeds a real Dash server directly in the cell via
    jupyter_mode="inline", the same mechanism CliPresenter uses for a real
    browser tab, just rendered inline instead. Select via
    run_backtest(..., render_mode="native_dash").

    Kept available, not removed, for: (a) classic Jupyter/JupyterLab, where
    Dash's own docs describe this as fully supported (unlike Colab), and
    (b) in case Colab's own Dash-in-notebook support improves in the future
    -- switching back is a render_mode string, not a code change. Only
    run_live differs from NotebookPresenter; run_batch/show_report (the
    non-live path) are identical either way, so this subclasses rather than
    reimplementing them."""

    def __init__(
        self, jupyter_mode: str = "inline", poll_seconds: float = 3.0, render_options: RenderOptions | None = None
    ):
        from .visualization import RenderOptions

        self._jupyter_mode = jupyter_mode
        self._poll_seconds = poll_seconds
        self._render_options = render_options or RenderOptions()

    def run_live(
        self, harness, total_days, checkpoint_path, checkpoint_every, title, ticker_colors, prices, port, verbose
    ):
        import threading

        from .live import serve

        def _run_backtest() -> None:
            report = harness.run(checkpoint_path=checkpoint_path, checkpoint_every=checkpoint_every)
            self.show_report(report, title, ticker_colors, prices)

        thread = threading.Thread(target=_run_backtest, daemon=True)
        thread.start()

        serve(
            checkpoint_path,
            title=title,
            poll_seconds=self._poll_seconds,
            ticker_colors=ticker_colors,
            prices=prices,
            port=port,
            verbose=verbose,
            jupyter_mode=self._jupyter_mode,
            options=self._render_options,
        )
