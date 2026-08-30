"""`%backtest <config.yaml> [flags]` -- run a config-driven backtest from a
notebook cell without writing `from tam.backtest.runner import run_backtest`
by hand every time.

    %load_ext tam.notebook.magic
    %backtest config.yaml
    %backtest config.yaml --live
    %backtest config.yaml --live --render-mode native_dash --poll-seconds 5
    report = %backtest config.yaml   # capture the returned Report, same as
                                      # any other IPython line magic

Deliberately does NOT import IPython at module scope -- `load_ipython_extension`
only ever runs *from inside* IPython's own `%load_ext` machinery, which always
passes a live `ipython` shell object in, so there's nothing to import here.
That keeps `import tam.notebook.magic` itself safe even where IPython isn't
installed (e.g. this repo's own test suite) -- only actually loading the
extension inside a real kernel needs it, same as `run_backtest(..., live=True)`
already requires the `notebook` extra outside one.
"""

from __future__ import annotations

import argparse
import shlex

from ..backtest.report import Report
from ..backtest.runner import run_backtest


def _bool(value: str) -> bool:
    return value.lower() not in ("0", "false", "no")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="%backtest", add_help=False)
    parser.add_argument("config", help="Path to a backtest YAML config")
    parser.add_argument("--live", action="store_true", help="Redraw the chart in place as the backtest runs")
    parser.add_argument(
        "--render-mode",
        dest="render_mode",
        default=None,
        help='Which registered Presenter drives --live (default: the config\'s own report.presenter, or "clear_output")',
    )
    parser.add_argument(
        "--poll-seconds", dest="poll_seconds", type=float, default=None, help="--live refresh cadence, in seconds"
    )
    parser.add_argument(
        "--show-trades",
        dest="show_trades_default",
        type=_bool,
        default=None,
        help="Whether the equity chart's trade markers start shown (default) or hidden",
    )
    parser.add_argument("--port", type=int, default=8050)
    return parser


def run_backtest_magic(line: str) -> Report | None:
    args = _parser().parse_args(shlex.split(line))
    presenter_kwargs = {"poll_seconds": args.poll_seconds} if args.poll_seconds is not None else None
    return run_backtest(
        args.config,
        live=args.live,
        port=args.port,
        render_mode=args.render_mode,
        presenter_kwargs=presenter_kwargs,
        show_trades_default=args.show_trades_default,
    )


def load_ipython_extension(ipython) -> None:
    ipython.register_magic_function(run_backtest_magic, "line", "backtest")
