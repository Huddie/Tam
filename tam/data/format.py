"""File formats: serialize a DataFrame to one flat file. Kept dependency-free
of DataRepository/DataStore (no imports from either) so both `tam.data.export`
(one symbol -> one file) and `tam.data.writer` (a whole repository -> a
RepoWriter's own destination) can build on the same Registry(FileFormat, ...)
entries without a circular import between them.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

import pandas as pd

from ..registry import Registry


class FileFormat(ABC):
    """Writes a DataFrame to one flat file, whatever shape its columns are --
    the file-format counterpart to DataProvider (fetch)/DataStore (cache)."""

    @abstractmethod
    def write(self, df: pd.DataFrame, path: Path) -> None: ...


@Registry.register(FileFormat, "csv")
class CsvFormat(FileFormat):
    def write(self, df: pd.DataFrame, path: Path) -> None:
        df.to_csv(path)


@Registry.register(FileFormat, "parquet")
class ParquetFormat(FileFormat):
    def write(self, df: pd.DataFrame, path: Path) -> None:
        df.to_parquet(path)
