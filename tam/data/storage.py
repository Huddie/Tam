"""Data stores: persist and retrieve one symbol's OHLCV history as CSV or Parquet,
partitioned by year on disk (data/eod/<SYMBOL>/<year>.<ext>). An incremental daily
ingest then only reads/rewrites the year(s) actually touched by new data instead of
the symbol's entire history.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import List

import pandas as pd

from ..registry import Registry
from .schema import DATE, empty_ohlcv_frame


class DataStore(ABC):
    """Persists and retrieves one symbol's history, indexed by date.

    `write` is an UPSERT: it merges the given rows into whatever's already on
    disk for the year(s) those rows fall in, leaving other years untouched.
    """

    @abstractmethod
    def exists(self, symbol: str) -> bool: ...

    @abstractmethod
    def read(self, symbol: str) -> pd.DataFrame: ...

    @abstractmethod
    def write(self, symbol: str, df: pd.DataFrame) -> None: ...


class _YearPartitionedStore(DataStore):
    """Shared partition-by-year mechanics; subclasses supply the file read/write primitive."""

    def __init__(self, root: str | Path, suffix: str):
        self._root = Path(root)
        self._suffix = suffix

    def _symbol_dir(self, symbol: str) -> Path:
        return self._root / symbol.upper()

    def _partition_path(self, symbol: str, year: int) -> Path:
        return self._symbol_dir(symbol) / f"{year}{self._suffix}"

    def _partition_years(self, symbol: str) -> List[int]:
        symbol_dir = self._symbol_dir(symbol)
        if not symbol_dir.exists():
            return []
        return sorted(int(path.stem) for path in symbol_dir.glob(f"*{self._suffix}"))

    def exists(self, symbol: str) -> bool:
        return bool(self._partition_years(symbol))

    def read(self, symbol: str) -> pd.DataFrame:
        years = self._partition_years(symbol)
        if not years:
            return empty_ohlcv_frame()
        frames = [self._read_file(self._partition_path(symbol, year)) for year in years]
        return pd.concat(frames).sort_index()

    def write(self, symbol: str, df: pd.DataFrame) -> None:
        if df.empty:
            return
        for year, group in df.groupby(df.index.year):
            self._upsert_partition(symbol, int(year), group)

    def _upsert_partition(self, symbol: str, year: int, group: pd.DataFrame) -> None:
        path = self._partition_path(symbol, year)
        if path.exists():
            existing = self._read_file(path)
            merged = pd.concat([existing, group])
            merged = merged[~merged.index.duplicated(keep="last")].sort_index()
        else:
            merged = group.sort_index()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._write_file(path, merged)

    def _read_file(self, path: Path) -> pd.DataFrame:
        raise NotImplementedError

    def _write_file(self, path: Path, df: pd.DataFrame) -> None:
        raise NotImplementedError


@Registry.register(DataStore, "parquet")
class ParquetStore(_YearPartitionedStore):
    def __init__(self, root: str | Path):
        super().__init__(root, suffix=".parquet")

    def _read_file(self, path: Path) -> pd.DataFrame:
        return pd.read_parquet(path)

    def _write_file(self, path: Path, df: pd.DataFrame) -> None:
        df.to_parquet(path)


@Registry.register(DataStore, "csv")
class CsvStore(_YearPartitionedStore):
    def __init__(self, root: str | Path):
        super().__init__(root, suffix=".csv")

    def _read_file(self, path: Path) -> pd.DataFrame:
        return pd.read_csv(path, index_col=DATE, parse_dates=[DATE])

    def _write_file(self, path: Path, df: pd.DataFrame) -> None:
        df.to_csv(path, index_label=DATE)
