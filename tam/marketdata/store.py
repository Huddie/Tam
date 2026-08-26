"""MinuteBarStore: persist and retrieve one symbol's 1-minute OHLCV history,
partitioned by year -- <root>/<SYMBOL>/<year>.parquet, the SAME layout and
UPSERT-by-year-partition semantics tam.data.storage._YearPartitionedStore
already uses for end-of-day data (see that module's own docstring), just
generalized from a local pathlib.Path root to any pyarrow.fs.FileSystem
root. That generalization is the whole reason this isn't simply reusing
tam.data.storage.DataStore directly: the SAME store class then runs,
unmodified, against a pyarrow.fs.LocalFileSystem in tests/local dev and a
pyarrow.fs.S3FileSystem (tam.marketdata.filesystem.r2_filesystem) in
production -- no separate "R2Store" class to keep in sync with a
"LocalStore" one, and no new abstraction beyond what tam.registry.Registry
already gives every other pluggable piece of this project.

An incremental daily ingest only reads/rewrites the year(s) actually touched
by new rows, same reasoning as the EOD store's own docstring: a year of
1-minute bars is small (~98k rows/symbol), but the point-in-time universe
runs to hundreds of symbols, and re-writing everyone's full 20-year history
on every day's ingest would be needlessly slow.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, List, Optional

import pandas as pd

from ..registry import Registry
from .credentials import R2Credentials, resolve_r2_credentials
from .filesystem import r2_bucket_path, r2_filesystem
from .schema import MINUTE_BAR_COLUMNS, TS, empty_minute_bar_frame, ensure_utc_index

if TYPE_CHECKING:
    import pyarrow.fs


class MinuteBarStore(ABC):
    """Persists and retrieves one symbol's minute-bar history, indexed by a
    tz-aware UTC `ts`.

    `write` is an UPSERT: it merges the given rows into whatever's already
    stored for the year(s) those rows fall in, leaving other years
    untouched -- exactly tam.data.storage.DataStore's own contract, just for
    minute bars.
    """

    @abstractmethod
    def exists(self, symbol: str) -> bool: ...

    @abstractmethod
    def read(self, symbol: str) -> pd.DataFrame: ...

    @abstractmethod
    def write(self, symbol: str, df: pd.DataFrame) -> None: ...


class ParquetFileSystemStore(MinuteBarStore):
    """Year-partitioned Parquet via any pyarrow.fs.FileSystem + a root path
    prefix (filesystem-relative -- e.g. "tam-market-data/minute" for R2, or
    a bare local directory for pyarrow.fs.LocalFileSystem; NOT a
    pathlib.Path). `symbol` is kept as an actual column inside every file,
    redundant with its path but making each file self-describing for a
    cross-symbol glob query (e.g. every symbol's 2015 bars at once) without
    relying on filename parsing.
    """

    def __init__(self, filesystem: "pyarrow.fs.FileSystem", root: "str | Path"):
        self._fs = filesystem
        self._root = str(root).rstrip("/")

    def _symbol_dir(self, symbol: str) -> str:
        return f"{self._root}/{symbol.upper()}"

    def _partition_path(self, symbol: str, year: int) -> str:
        return f"{self._symbol_dir(symbol)}/{year}.parquet"

    def _partition_years(self, symbol: str) -> List[int]:
        import pyarrow.fs as fs

        selector = fs.FileSelector(self._symbol_dir(symbol), recursive=False, allow_not_found=True)
        years = []
        for info in self._fs.get_file_info(selector):
            if info.base_name.endswith(".parquet"):
                years.append(int(info.base_name[: -len(".parquet")]))
        return sorted(years)

    def exists(self, symbol: str) -> bool:
        return bool(self._partition_years(symbol))

    def read(self, symbol: str) -> pd.DataFrame:
        years = self._partition_years(symbol)
        if not years:
            return empty_minute_bar_frame()
        frames = [self._read_file(self._partition_path(symbol, year)) for year in years]
        return pd.concat(frames).sort_index()

    def write(self, symbol: str, df: pd.DataFrame) -> None:
        if df.empty:
            return
        df = ensure_utc_index(df)
        for year, group in df.groupby(df.index.year):
            self._upsert_partition(symbol, int(year), group)

    def _upsert_partition(self, symbol: str, year: int, group: pd.DataFrame) -> None:
        path = self._partition_path(symbol, year)
        if self._file_exists(path):
            existing = self._read_file(path)
            merged = pd.concat([existing, group])
            merged = merged[~merged.index.duplicated(keep="last")].sort_index()
        else:
            merged = group.sort_index()
        self._write_file(path, merged)

    def _file_exists(self, path: str) -> bool:
        import pyarrow.fs as fs

        return self._fs.get_file_info(path).type != fs.FileType.NotFound

    def _read_file(self, path: str) -> pd.DataFrame:
        import pyarrow.parquet as pq

        with self._fs.open_input_file(path) as handle:
            table = pq.read_table(handle)
        df = table.to_pandas()
        df = df.set_index(TS)
        df.index = pd.DatetimeIndex(df.index).tz_convert("UTC")
        df.index.name = TS
        return df[MINUTE_BAR_COLUMNS]

    def _write_file(self, path: str, df: pd.DataFrame) -> None:
        import pyarrow as pa
        import pyarrow.parquet as pq

        self._fs.create_dir(path.rsplit("/", 1)[0], recursive=True)
        table = pa.Table.from_pandas(df.reset_index(), preserve_index=False)
        with self._fs.open_output_stream(path) as handle:
            pq.write_table(table, handle)


@Registry.register(MinuteBarStore, "local_parquet")
class LocalMinuteBarStore(ParquetFileSystemStore):
    """Plain local disk -- what tests and local dev use; identical
    partition layout/UPSERT behavior to the R2-backed store below, so
    validating a small backfill locally before pointing it at R2 (see
    MARKETDATA.md) exercises the exact same code path."""

    def __init__(self, root: "str | Path"):
        import pyarrow.fs as fs

        super().__init__(fs.LocalFileSystem(), root)


@Registry.register(MinuteBarStore, "r2_parquet")
class R2MinuteBarStore(ParquetFileSystemStore):
    """Cloudflare R2, via pyarrow's S3FileSystem (see tam.marketdata.filesystem
    -- no boto3 dependency). `credentials`, if omitted, resolves the usual
    way (tam.marketdata.credentials.resolve_r2_credentials: kwarg -> env var
    -> Colab secret -> saved file). `prefix` is the path inside the bucket
    (default "minute", i.e. <bucket>/minute/<SYMBOL>/<year>.parquet) --
    a sibling "reference" prefix holds the point-in-time universe/security-
    master tables (see tam.marketdata.reference), so both live in one bucket
    without colliding.
    """

    def __init__(self, credentials: Optional[R2Credentials] = None, prefix: str = "minute"):
        self._credentials = credentials or resolve_r2_credentials()
        super().__init__(r2_filesystem(self._credentials), r2_bucket_path(self._credentials, prefix))
