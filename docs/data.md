# Data

Fetch and cache OHLCV-or-whatever history. Three small interfaces, each
independently pluggable via the [registry](getting-started.md#the-registry-pattern):

```python
class DataProvider(ABC):
    def fetch_eod(self, symbol: str, start: date, end: date) -> pd.DataFrame: ...

class DataStore(ABC):
    def exists(self, symbol: str) -> bool: ...
    def read(self, symbol: str) -> pd.DataFrame: ...
    def write(self, symbol: str, df: pd.DataFrame) -> None: ...
```

`DataRepository` composes a provider (fetch) and a store (cache), and is the
thing everything else actually calls:

```python
from datetime import date
from tam.data.providers import DataProvider
from tam.data.storage import DataStore
from tam.data.repository import DataRepository
from tam.registry import Registry

repo = DataRepository(
    Registry.get(DataProvider, "yfinance"),
    Registry.create(DataStore, "parquet", "data/eod"),
)
repo.ingest(["AAPL", "MSFT"], date(2020, 1, 1), date(2024, 1, 1))  # only fetches missing sub-ranges
df = repo.query("AAPL", date(2023, 1, 1), date(2023, 6, 1))         # cached in-memory after first read
```

Ships with `"yfinance"`/`"fmp"` providers and `"csv"`/`"parquet"` stores
(year-partitioned on disk: `<root>/<SYMBOL>/<year>.<ext>`). Add your own
data source or cache format with one `@Registry.register(...)` class —
nothing else in the codebase needs to change.

## Writing ingested data elsewhere

`DataRepository.write()` hands ingested data to a `RepoWriter` — a
separate `Registry` entry from `DataStore`'s own cache format. Some
writers write flat files (the two built in do, one per symbol — not
`DataStore`'s year-partitioned layout); nothing stops a custom one from
shipping rows to S3/a database/an in-memory object instead.

```python
from tam.data.writer import RepoWriter

paths = repo.write(Registry.create(RepoWriter, "csv", "out/eod"), ["AAPL", "MSFT"])
# -> {"AAPL": Path("out/eod/AAPL.csv"), "MSFT": Path("out/eod/MSFT.csv")}
```

## Standalone export — fetch, transform, flat file

No backtest involved at all — for when you just want one symbol's data in
your own hands, in one call:

```python
from datetime import date
from tam.data.export import export_history

export_history(
    "MU", date(2020, 1, 1), date(2024, 1, 1), "mu.csv",
    provider="yfinance",                                            # any registered DataProvider
    transform=lambda df: df.assign(ret=df["close"].pct_change()),   # any DataFrame -> DataFrame callable
)
```

This is the one-symbol, always-a-flat-file shortcut; reach for
`DataRepository.write(...)` above instead when you have several symbols
already ingested, or want a non-file `RepoWriter`.

Output format (`FileFormat`, shared with `RepoWriter` above) is also a
`Registry` entry (`"csv"`/`"parquet"` built in, inferred from `path`'s
suffix if `format=` is omitted) — register your own for feather/json/
whatever. Config-driven equivalent: `tam.data.export.run_export(config_path)`,
reading a `data:` + `export:` YAML section (see `examples/export_mu_config.yaml`);
`transform` stays Python-only since it's code, not YAML.
