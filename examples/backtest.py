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
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from tam.backtest.config import build_strategies
from tam.backtest.harness import BacktestHarness, Progress
from tam.backtest.visualization import write_html
from tam.config import Config
from tam.data.providers import DataProvider
from tam.data.repository import DataRepository
from tam.data.storage import DataStore
from tam.registry import Registry


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


def _build_repository(data_settings: DataSettings) -> DataRepository:
    provider = Registry.get(DataProvider, data_settings.provider)
    store = Registry.create(DataStore, data_settings.store, data_settings.root)
    return DataRepository(provider, store)


def _validate_tickers_declared(backtest_settings: BacktestSettings) -> None:
    """Fail loudly at config-load time if a strategy trades a ticker that isn't in
    `backtest.tickers` — catches config drift before it becomes a cryptic crash
    deep inside whichever strategy factory happens to need price data first.
    Checks both a flat `params.ticker` and a `params.tickers` list, since some
    strategies (e.g. trend_rotation) trade more than one ticker at once."""
    declared = set(backtest_settings.tickers)
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


def _print_progress(progress: Progress) -> None:
    width = 30
    filled = int(width * progress.fraction)
    bar = "#" * filled + "-" * (width - filled)
    end = "\n" if progress.day_index == progress.total_days else ""
    print(
        f"\r[{bar}] {progress.day_index}/{progress.total_days} days "
        f"({progress.fraction:5.1%}) - {progress.current_date}",
        end=end,
        file=sys.stderr,
        flush=True,
    )


def run(config_path: Path) -> None:
    cfg = Config(config_path)
    data_settings = cfg.data(DataSettings)
    backtest_settings = cfg.backtest(BacktestSettings)
    _validate_tickers_declared(backtest_settings)

    repository = _build_repository(data_settings)
    start = date.fromisoformat(backtest_settings.start)
    end = date.fromisoformat(backtest_settings.end)

    tickers = list(backtest_settings.tickers)
    repository.ingest(tickers, start, end)
    history = repository.query(tickers[0], start, end)
    dates = [ts.date() for ts in history.index]

    strategies, portfolios = build_strategies(
        repository, backtest_settings.strategies, float(backtest_settings.cash)
    )

    harness = BacktestHarness(repository, strategies, portfolios, dates)
    report = harness.run(on_progress=_print_progress)

    print(report.summary_all())

    report_path = Path(backtest_settings.report_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    write_html(report, str(report_path), title=f"Backtest: {config_path.stem}")
    print(f"Report written to {report_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path, help="Path to a backtest YAML config")
    run(parser.parse_args().config)


if __name__ == "__main__":
    main()
