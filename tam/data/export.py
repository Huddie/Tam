"""Standalone data export: fetch -> optional transform -> write a single flat
file. Independent of the backtest engine entirely -- no Strategy, Portfolio,
or BacktestHarness involved. Reuses the same DataProvider/DataStore/
DataRepository machinery `tam.backtest.runner` uses for a backtest's own data
ingestion, just aimed at a plain flat-file handoff instead of feeding a
harness. Not tied to any particular column shape -- whatever a DataProvider
returns for a symbol (OHLCV today; anything else a future/custom DataProvider
returns tomorrow) passes straight through to `transform` and the output file
untouched. Nothing here assumes "OHLC" specifically -- see `tam/data/
providers.py` if you want a provider with a different shape.

    from datetime import date
    from tam.data.export import export_history

    export_history(
        "MU", date(2020, 1, 1), date(2024, 1, 1), "mu.csv",
        transform=lambda df: df.assign(ret=df["close"].pct_change()),
    )

`cache_store`/`cache_root` are the SAME kind of year-partitioned store
`tam.backtest.runner` uses to avoid re-fetching from the provider on repeat
calls -- that's an internal ingestion cache, not the file this function
writes. `path` is always a single flat file in whatever `format` (or its own
suffix) says, ready to hand to a plain `pd.read_csv`/`pd.read_parquet` in a
script that has nothing else to do with this package.

The output format itself is a Registry(FileFormat, name) entry, not a
hardcoded if/else -- exactly the same self-registering idiom DataProvider/
DataStore already use, so a project can add e.g. "feather"/"json" the same
way it'd add a DataProvider: one @Registry.register(FileFormat, "name") class,
no changes needed here.

For the config-driven CLI (examples/export_data.py), see run_export() below --
same `data:` config section a backtest config already uses (provider/store/root),
plus a new `export:` section for the declarative ticker/date-range/path/format.
`transform` stays Python-only (arbitrary code can't live in YAML); the CLI covers
fetch+write only -- call export_history()/run_export(transform=...) directly
from a script or notebook for that.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Callable, Optional

import pandas as pd

from ..config import Config
from ..registry import Registry
from .format import FileFormat
from .providers import DataProvider
from .repository import DataRepository
from .storage import DataStore


def export_history(
    symbol: str,
    start: date,
    end: date,
    path: str,
    *,
    provider: str = "yfinance",
    cache_store: str = "parquet",
    cache_root: str = "data/eod",
    transform: Optional[Callable[[pd.DataFrame], pd.DataFrame]] = None,
    format: Optional[str] = None,
) -> Path:
    """Fetch `symbol`'s history for [start, end] (via Registry(DataProvider,
    provider), caching through Registry(DataStore, cache_store) at cache_root so
    a repeat call doesn't re-hit the provider), run it through `transform` if
    given (any plain DataFrame -> DataFrame callable -- add columns, resample,
    filter, whatever -- works the same regardless of what columns the provider
    returned), and write the result to `path` as one flat file.

    `format` picks the Registry(FileFormat, ...) entry ("csv"/"parquet" ship
    built in) -- inferred from `path`'s own suffix when omitted, e.g. "mu.csv"
    needs no explicit format=.
    """
    repository = DataRepository(Registry.get(DataProvider, provider), Registry.create(DataStore, cache_store, cache_root))
    repository.ingest([symbol], start, end)
    df = repository.query(symbol, start, end)

    if transform is not None:
        df = transform(df)

    out_path = Path(path)
    fmt = format or out_path.suffix.lstrip(".")
    try:
        file_format = Registry.get(FileFormat, fmt)
    except KeyError:
        raise ValueError(f"format must be one of {sorted(Registry.names(FileFormat))}, got {fmt!r}") from None

    out_path.parent.mkdir(parents=True, exist_ok=True)
    file_format.write(df, out_path)
    return out_path


class DataSettings:
    """Same shape/section (`data:`) a backtest config already declares --
    reused as-is here so one config file can drive either a backtest or a
    plain export (or both) without duplicating provider/store/root."""

    provider: str
    store: str
    root: str


class ExportSettings:
    """The `export:` config section -- purely declarative (symbol/date
    range/output path/format); a transform is Python code, so it has no YAML
    representation and isn't part of this section at all."""

    symbol: str
    start: str
    end: str
    path: str
    format: str = None


def run_export(config_path, transform: Optional[Callable[[pd.DataFrame], pd.DataFrame]] = None) -> Path:
    """Config-driven counterpart to export_history() -- reads `data:` + `export:`
    from `config_path` (see DataSettings/ExportSettings) and calls export_history()
    with them. `transform`, if given, is applied exactly like export_history()'s
    own `transform` -- it's a Python argument here, not a config field."""
    config_path = Path(config_path)
    cfg = Config(config_path)
    data_settings = cfg.data(DataSettings)
    export_settings = cfg.export(ExportSettings)

    return export_history(
        export_settings.symbol,
        date.fromisoformat(export_settings.start),
        date.fromisoformat(export_settings.end),
        export_settings.path,
        provider=data_settings.provider,
        cache_store=data_settings.store,
        cache_root=data_settings.root,
        transform=transform,
        format=export_settings.format,
    )
