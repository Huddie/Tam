"""Global, minimal hook for reporting "what's happening right now" from deep
inside a strategy (e.g. a LoRA fine-tune pass) up to whatever's presenting
progress to the user -- without those strategies needing to know or care
whether they're being run from the CLI, a test, or anything else.

Defaults to a no-op, so nothing needs this to be wired up. examples/backtest.py
wires a real one in via set_reporter() to drive a second progress row for
"current activity" underneath the overall day-count bar.
"""

from __future__ import annotations

from collections.abc import Callable

Reporter = Callable[[str, int | None, int | None], None]


def _noop(text: str, current: int | None, total: int | None) -> None:
    pass


_reporter: Reporter = _noop


def set_reporter(reporter: Reporter | None) -> None:
    global _reporter
    _reporter = reporter or _noop


def report(text: str, current: int | None = None, total: int | None = None) -> None:
    """Report a status line, optionally with (current, total) for a
    determinate sub-progress bar (e.g. LoRA iter N/100) -- omit both for an
    indeterminate step (e.g. "loading model...")."""
    _reporter(text, current, total)
