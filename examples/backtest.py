"""
General config-driven backtest runner CLI. Every strategy — the one "under test"
and whatever it's compared against — is just an entry in `backtest.strategies`,
built purely from config via tam.registry.Registry(Strategy, name); this script
has no strategy-specific imports at all. See tam/strategy/*.py for the
strategies available out of the box (buy_and_hold, moving_average,
ma_crossover) and tam/backtest/config.py for how a spec becomes a
Strategy+Portfolio pair.

This is a thin argparse wrapper -- the actual runner lives in
tam.backtest.runner (run() below is that module's run()) so it ships as part
of the installable `tam` package, not just this repo's examples/. For
notebook/Colab use, see tam.backtest.runner.run_backtest() instead, which
returns the Report and renders inline rather than printing a Rich progress
UI and writing an HTML file.

Usage:
    python -m examples.backtest examples/moving_average_config.yaml
    python -m examples.backtest examples/ma_crossover_config.yaml
    python -m examples.backtest examples/trend_rotation_config.yaml --mode live
"""
from __future__ import annotations

import argparse
from pathlib import Path

from tam.backtest.runner import BacktestSettings, _validate_tickers_declared, run  # noqa: F401 -- re-exported for tests/back-compat


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path, help="Path to a backtest YAML config")
    parser.add_argument(
        "--mode",
        choices=["batch", "live"],
        default="batch",
        help=(
            "'batch' (default): run to completion, then write the static HTML report. "
            "'live': open a dashboard that updates as the backtest runs, polling "
            "backtest.checkpoint_path (must be set in the config)."
        ),
    )
    parser.add_argument(
        "--log-level",
        choices=["default", "verbose"],
        default="default",
        help=(
            "'default': quiet -- just the progress display (silences Dash/Werkzeug's "
            "per-request access log in --mode live). 'verbose': also show those logs, "
            "e.g. while debugging the live server itself."
        ),
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8050,
        help="Port for the --mode live dashboard (default 8050). Change this if that port's already in use.",
    )
    parser.add_argument(
        "--no-save",
        action="store_true",
        help=(
            "Skip writing a resumable checkpoint -- a crash mid-run loses all progress "
            "with no way to resume, instead of picking back up from the last checkpointed "
            "day. The final HTML report (and --mode live's dashboard) are unaffected; "
            "live mode still needs a checkpoint file to drive the dashboard, so it uses a "
            "throwaway temp one instead of the config's persistent artifacts dir. Only "
            "useful for short/disposable runs -- for anything long or unattended, leaving "
            "checkpointing on costs almost nothing and saves you from starting over."
        ),
    )
    args = parser.parse_args()
    run(args.config, mode=args.mode, verbose=args.log_level == "verbose", port=args.port, no_save=args.no_save)


if __name__ == "__main__":
    main()
