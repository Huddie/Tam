"""
General config-driven backtest runner. Every strategy — the one "under test" and
whatever it's compared against — is just an entry in `backtest.strategies`, built
purely from config via tam.registry.Registry(Strategy, name); this script has no
strategy-specific imports at all. See tam/strategy/*.py for the strategies
available out of the box (buy_and_hold, moving_average, ma_crossover) and
tam/backtest/config.py for how a spec becomes a Strategy+Portfolio pair.

Usage:
    python -m examples.backtest examples/moving_average_config.yaml
    python -m examples.backtest examples/ma_crossover_config.yaml
    python -m examples.backtest examples/trend_rotation_config.yaml --mode live
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import tempfile
import threading
from datetime import date
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn
from rich.table import Table as RichTable

from tam.backtest.config import build_strategies
from tam.backtest.harness import BacktestHarness, Progress as RunProgress
from tam.backtest.visualization import BUY_COLOR, SELL_COLOR, write_html
from tam.config import Config
from tam.data.providers import DataProvider
from tam.data.repository import DataRepository
from tam.data.storage import DataStore
from tam.registry import Registry
from tam.status import set_reporter


class DataSettings:
    provider: str
    store: str
    root: str


class BacktestSettings:
    tickers: list
    start: str
    end: str
    cash: float
    report_path: str
    strategies: list
    checkpoint_path: str = None
    checkpoint_every: int = 1
    price_chart: dict = None


def _build_repository(data_settings: DataSettings) -> DataRepository:
    provider = Registry.get(DataProvider, data_settings.provider)
    store = Registry.create(DataStore, data_settings.store, data_settings.root)
    return DataRepository(provider, store)


def _config_hash(config_path: Path) -> str:
    return hashlib.sha256(config_path.read_bytes()).hexdigest()[:12]


def _artifacts_dir(config_path: Path) -> Path:
    """Every config gets its own artifacts folder, keyed by a hash of its exact
    contents -- so two configs (or two edited versions of the same config) never
    collide on the same checkpoint/adapter/log files just because they happen to
    share a filename. A byte-for-byte change (even a date range, even a
    comment) gets a fresh folder rather than silently resuming/reusing another
    run's state. Rooted next to the config file itself (config_path.parent /
    "output"), not a hardcoded "examples/output" relative to cwd -- otherwise a
    config living anywhere else (e.g. a test's tmp_path) would write real files
    into this repo's examples/output/ instead of staying self-contained."""
    return config_path.parent / "output" / config_path.stem / _config_hash(config_path)


def _ephemeral_checkpoint_path() -> str:
    """--no-save + --mode live still needs *some* checkpoint file -- it's the
    only channel the live dashboard has to poll the background run's progress
    -- but it belongs in a throwaway tempdir, not the config's persistent
    artifacts dir, and isn't meant to survive a crash for resuming: a fresh
    path every run, cleaned up by the OS eventually, not something anyone
    would know to point --checkpoint-path at afterward."""
    return str(Path(tempfile.mkdtemp(prefix="tam_live_")) / "checkpoint.pkl")


def _apply_artifact_defaults(backtest_settings: "BacktestSettings", artifacts_dir: Path) -> None:
    """Fill in checkpoint_path / each llm_trading strategy's log_path and
    lora.adapter_root from the config-hash-namespaced artifacts dir, but only
    where the config didn't already say explicitly -- an explicit value in
    the YAML always wins."""
    if not backtest_settings.checkpoint_path:
        backtest_settings.checkpoint_path = str(artifacts_dir / "checkpoint.pkl")

    for spec in backtest_settings.strategies:
        if spec.strategy == "llm_trading" and "log_path" not in spec.params:
            spec.params.setdefault("log_path", str(artifacts_dir / "llm_log" / f"{spec.portfolio_id}.csv"))
        lora = spec.params.get("lora")
        if lora is not None and "adapter_root" not in lora:
            lora.setdefault("adapter_root", str(artifacts_dir / "lora_adapters" / spec.portfolio_id))


def _ticker_colors(backtest_settings: "BacktestSettings") -> dict:
    """{ticker: color} for trade-marker coloring, derived from each strategy's
    own long_ticker/short_ticker config -- not hardcoded ticker names. A
    strategy without that concept (e.g. buy_and_hold) just contributes
    nothing, and its markers fall back to the portfolio's own line color."""
    colors = {}
    for spec in backtest_settings.strategies:
        long_ticker = spec.params.get("long_ticker")
        short_ticker = spec.params.get("short_ticker")
        if long_ticker:
            colors[long_ticker] = BUY_COLOR
        if short_ticker:
            colors[short_ticker] = SELL_COLOR
    return colors


def _collect_price_series(
    repository: DataRepository, backtest_settings: "BacktestSettings", start: date, end: date
) -> dict:
    """{ticker: close-price Series} for the optional price panel above the
    equity chart, if `backtest.price_chart.tickers` is set -- {} (chart
    omitted entirely) otherwise, since this is opt-in, not automatic."""
    if not backtest_settings.price_chart:
        return {}
    tickers = list(backtest_settings.price_chart.get("tickers", []))
    return {ticker: repository.query(ticker, start, end)["close"] for ticker in tickers}


def _validate_tickers_declared(backtest_settings: BacktestSettings) -> None:
    """Fail loudly at config-load time if a strategy trades a ticker that isn't in
    `backtest.tickers` — catches config drift before it becomes a cryptic crash
    deep inside whichever strategy factory happens to need price data first.
    Checks both a flat `params.ticker` and a `params.tickers` list, since some
    strategies (e.g. trend_rotation) trade more than one ticker at once. Also
    checks `backtest.price_chart.tickers`, the optional price panel's own list."""
    declared = set(backtest_settings.tickers)

    if backtest_settings.price_chart:
        missing = set(backtest_settings.price_chart.get("tickers", [])) - declared
        if missing:
            raise ValueError(
                f"backtest.price_chart uses ticker(s) {sorted(missing)}, which are missing from "
                f"backtest.tickers {sorted(declared)}"
            )

    for spec in backtest_settings.strategies:
        used = set()
        single = spec.params.get("ticker")
        if single:
            used.add(single)
        multiple = spec.params.get("tickers")
        if multiple:
            used.update(multiple)

        missing = used - declared
        if missing:
            raise ValueError(
                f"strategy {spec.strategy!r} (portfolio {spec.portfolio_id!r}) uses ticker(s) "
                f"{sorted(missing)}, which are missing from backtest.tickers {sorted(declared)}"
            )


def _print_banner(config_path: Path, config_hash: str, artifacts_dir: Path) -> None:
    grid = RichTable.grid(padding=(0, 2))
    grid.add_column(style="bold cyan", justify="right")
    grid.add_column()
    grid.add_row("Config", str(config_path))
    grid.add_row("Hash", config_hash)
    grid.add_row("Artifacts", str(artifacts_dir))
    Console().print(Panel(grid, title="[bold]Backtest[/bold]", border_style="cyan", expand=False))


def run(config_path: Path, mode: str = "batch", verbose: bool = False, port: int = 8050, no_save: bool = False) -> None:
    config_hash = _config_hash(config_path)
    artifacts_dir = _artifacts_dir(config_path)
    _print_banner(config_path, config_hash, artifacts_dir)

    cfg = Config(config_path)
    data_settings = cfg.data(DataSettings)
    backtest_settings = cfg.backtest(BacktestSettings)
    _validate_tickers_declared(backtest_settings)
    _apply_artifact_defaults(backtest_settings, artifacts_dir)
    if no_save:
        backtest_settings.checkpoint_path = _ephemeral_checkpoint_path() if mode == "live" else None

    repository = _build_repository(data_settings)
    start = date.fromisoformat(backtest_settings.start)
    end = date.fromisoformat(backtest_settings.end)

    tickers = list(backtest_settings.tickers)
    repository.ingest(tickers, start, end)
    history = repository.query(tickers[0], start, end)
    dates = [ts.date() for ts in history.index]
    price_series = _collect_price_series(repository, backtest_settings, start, end)

    strategies, portfolios = build_strategies(
        repository, backtest_settings.strategies, float(backtest_settings.cash)
    )
    harness = BacktestHarness(repository, strategies, portfolios, dates)

    if mode == "live":
        _run_live(harness, len(dates), backtest_settings, config_path, price_series, verbose, port)
    else:
        _run_batch(harness, len(dates), backtest_settings, config_path, price_series)


def _run_batch(
    harness: BacktestHarness,
    total_days: int,
    backtest_settings: BacktestSettings,
    config_path: Path,
    price_series: dict = None,
) -> None:
    """Runs to completion with a two-row live display: the overall day-count
    bar, and a second row underneath showing whatever's happening right now
    (loading a model, fine-tuning gen N with its own iter progress, etc.) --
    fed by whatever a strategy reports via tam.status, if anything does."""
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
                checkpoint_path=backtest_settings.checkpoint_path,
                checkpoint_every=backtest_settings.checkpoint_every,
            )
        finally:
            set_reporter(None)

    print(report.summary_all())
    _write_report(report, backtest_settings, config_path, price_series)


def _run_live(
    harness: BacktestHarness,
    total_days: int,
    backtest_settings: BacktestSettings,
    config_path: Path,
    price_series: dict = None,
    verbose: bool = False,
    port: int = 8050,
) -> None:
    """Runs the backtest on a background thread while the main thread serves a
    dashboard (tam.backtest.live) that polls the checkpoint the background run
    is writing -- the harness itself has no idea anything is watching it."""
    if not backtest_settings.checkpoint_path:
        raise ValueError("--mode live requires backtest.checkpoint_path to be set in the config")

    from tam.backtest.live import serve

    def _run_backtest() -> None:
        _run_batch(harness, total_days, backtest_settings, config_path, price_series)

    thread = threading.Thread(target=_run_backtest, daemon=True)
    thread.start()

    print(f"Live view: http://127.0.0.1:{port}  (backtest running in the background)", file=sys.stderr)
    serve(
        backtest_settings.checkpoint_path,
        title=f"Backtest (live): {config_path.stem}",
        ticker_colors=_ticker_colors(backtest_settings),
        prices=price_series,
        port=port,
        verbose=verbose,
    )


def _write_report(report, backtest_settings: BacktestSettings, config_path: Path, price_series: dict = None) -> None:
    report_path = Path(backtest_settings.report_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    write_html(
        report,
        str(report_path),
        title=f"Backtest: {config_path.stem}",
        ticker_colors=_ticker_colors(backtest_settings),
        prices=price_series,
    )
    print(f"Report written to {report_path}")


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
