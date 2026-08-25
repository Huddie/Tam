"""RepoWriter: writes a whole DataRepository's data out somewhere -- deliberately
more general than tam.data.format.FileFormat (one DataFrame -> one path). Some
writers will write to flat files (the two built in do); nothing about the
interface assumes that -- a RepoWriter could just as easily ship rows to S3, a
database table, or return an in-memory object instead. That's the whole reason
this exists as its own Registry(RepoWriter, ...) type rather than reusing
FileFormat directly: DataRepository.write() doesn't know or care which.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict

import pandas as pd

from ..registry import Registry
from .format import FileFormat


class RepoWriter(ABC):
    """Writes `{symbol: DataFrame}` (everything DataRepository.write() was
    asked to persist) out somewhere, however that writer defines "somewhere."
    Return value is intentionally writer-specific (a path per symbol, a URL, a
    row count, None, ...) -- callers that care inspect what their own chosen
    writer documents, same as any other Registry(RepoWriter, ...) entry."""

    @abstractmethod
    def write(self, data: Dict[str, pd.DataFrame]) -> Any: ...


class _FlatFileRepoWriter(RepoWriter):
    """Shared mechanics for "one flat file per symbol in `root`" -- NOT the
    DataStore's own year-partitioned cache layout, just <root>/<SYMBOL>.<ext>,
    ready for an external script to read directly. Delegates the actual
    serialization to Registry(FileFormat, format_name), so a new file format
    registered there is automatically available as a RepoWriter too."""

    def __init__(self, root: str | Path, format_name: str):
        self._root = Path(root)
        self._format_name = format_name
        self._format: FileFormat = Registry.get(FileFormat, format_name)

    def write(self, data: Dict[str, pd.DataFrame]) -> Dict[str, Path]:
        self._root.mkdir(parents=True, exist_ok=True)
        paths = {}
        for symbol, df in data.items():
            path = self._root / f"{symbol.upper()}.{self._format_name}"
            self._format.write(df, path)
            paths[symbol] = path
        return paths


@Registry.register(RepoWriter, "csv")
class CsvRepoWriter(_FlatFileRepoWriter):
    def __init__(self, root: str | Path):
        super().__init__(root, "csv")


@Registry.register(RepoWriter, "parquet")
class ParquetRepoWriter(_FlatFileRepoWriter):
    def __init__(self, root: str | Path):
        super().__init__(root, "parquet")
