"""Data stores: persist and retrieve one symbol's OHLCV history as CSV, local
Parquet, or R2 Parquet, partitioned by year (<root or bucket-prefix>/<SYMBOL>/
<year>.<ext>). An incremental daily ingest then only reads/rewrites the
year(s) actually touched by new data instead of the symbol's entire history.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Callable, List, Optional, TypeVar

import pandas as pd

from ..registry import Registry
from .schema import DATE, OHLCV_COLUMNS, empty_ohlcv_frame

_T = TypeVar("_T")


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

    def write_completeness_bytes(self, symbol: str, year: int, data: bytes) -> None:
        """Persist a completeness-index JSON sidecar (see
        tam.data.completeness) for one symbol-year. Default: a no-op -- a
        custom third-party DataStore that doesn't override this just
        doesn't get a completeness sidecar written."""
        return None

    def read_completeness_bytes(self, symbol: str, year: int) -> Optional[bytes]:
        """Raw bytes of one symbol-year's completeness sidecar, or None if
        it doesn't exist. Default: None, matching write_completeness_bytes()'s
        own "opt-in via override" default."""
        return None


class MultiDataStore(DataStore):
    """Fans a single write() out to every store in `stores` -- so a caller
    wanting BOTH a local cache AND R2 (see scripts/backfill_sp500_eod.py)
    doesn't have to run DataRepository.ingest() -- and therefore re-fetch
    from the provider -- once per destination. read()/exists() only consult
    the FIRST store ("primary"): unlike write, there's no unambiguous way to
    combine two stores' answers to "does this exist"/"what's the history"
    if they ever disagree, so this only fans out the one operation where
    "do it to all of them" has one obvious meaning."""

    def __init__(self, stores: List[DataStore]):
        if not stores:
            raise ValueError("MultiDataStore needs at least one store")
        self._stores = list(stores)

    def exists(self, symbol: str) -> bool:
        return self._stores[0].exists(symbol)

    def read(self, symbol: str) -> pd.DataFrame:
        return self._stores[0].read(symbol)

    def write(self, symbol: str, df: pd.DataFrame) -> None:
        for store in self._stores:
            store.write(symbol, df)


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
        self._write_completeness(symbol, year, merged)

    def _write_completeness(self, symbol: str, year: int, merged: pd.DataFrame) -> None:
        from .completeness import compute_completeness

        index = compute_completeness(symbol, year, merged)
        if index is not None:
            self.write_completeness_bytes(symbol, year, index.to_json().encode("utf-8"))

    def write_completeness_bytes(self, symbol: str, year: int, data: bytes) -> None:
        from .completeness import completeness_sidecar_suffix

        path = self._symbol_dir(symbol) / f"{year}{completeness_sidecar_suffix()}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def read_completeness_bytes(self, symbol: str, year: int) -> Optional[bytes]:
        from .completeness import completeness_sidecar_suffix

        path = self._symbol_dir(symbol) / f"{year}{completeness_sidecar_suffix()}"
        if not path.exists():
            return None
        return path.read_bytes()

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


def _with_retries(func: Callable[[], _T], attempts: int = 5, base_delay: float = 2.0) -> _T:
    """Retries `func` up to `attempts` times with exponential backoff --
    same reasoning and same parameters as tam.marketdata.store's own copy of
    this (R2 has been observed returning a transient internal error on an
    operation that otherwise succeeded, and a plain network blip needs a
    retry window generous enough to ride out, not just a couple seconds).
    Duplicated here rather than imported -- small independent pieces per
    subpackage, matching this codebase's existing convention (see e.g. the
    three separate _from_dotenv() copies across tam.discovery.auth,
    tam.marketdata.explorer_client, tam.marketdata.credentials)."""
    last_exc: Optional[Exception] = None
    for attempt in range(attempts):
        try:
            return func()
        except Exception as exc:  # noqa: BLE001 -- any transient network/S3 error should retry, not just specific ones
            last_exc = exc
            if attempt < attempts - 1:
                time.sleep(base_delay * (2**attempt))
    assert last_exc is not None
    raise last_exc


@Registry.register(DataStore, "r2_parquet")
class R2DataStore(DataStore):
    """Cloudflare R2, via boto3 directly -- NOT pyarrow.fs.S3FileSystem, same
    reasoning as tam.marketdata.store.R2MinuteBarStore (that module's
    docstring: R2 has been observed failing pyarrow's S3FileSystem's
    always-multipart uploads; boto3's plain put_object()/get_object() do a
    single PUT/GET for objects this small instead).

    `prefix` default "eod" -- <bucket>/eod/<SYMBOL>/<year>.parquet, the same
    per-symbol-per-year layout as ParquetStore's local <root>/<SYMBOL>/
    <year>.parquet, just inside the SAME R2 bucket the minute-bar pipeline
    already writes to (under "minute/" there instead) -- one bucket, two
    non-overlapping prefixes, not a second bucket to provision.

    Reuses tam.marketdata.credentials' R2Credentials/resolve_r2_credentials
    rather than duplicating credential resolution: it's generic S3-style
    credentials for the same physical account/bucket, not anything minute-
    bar-specific, despite living in that module.
    """

    def __init__(self, credentials=None, prefix: str = "eod", client=None):
        from ..marketdata.credentials import resolve_r2_credentials

        self._credentials = credentials or resolve_r2_credentials()
        self._prefix = prefix.rstrip("/")
        # `client=` is a test-only seam (inject a fake S3 client instead of a
        # real boto3 one), matching R2MinuteBarStore's own convention.
        self._client = client or self._build_client()

    def _build_client(self):
        import boto3
        from botocore.config import Config

        session = boto3.Session(
            aws_access_key_id=self._credentials.access_key_id,
            aws_secret_access_key=self._credentials.secret_access_key,
        )
        # region_name="auto" -- R2 rejects a real AWS region name outright;
        # see R2MinuteBarStore._build_client's own comment for why this is
        # required, not optional.
        return session.client(
            "s3",
            endpoint_url=self._credentials.endpoint,
            region_name="auto",
            config=Config(signature_version="s3v4"),
        )

    def _symbol_prefix(self, symbol: str) -> str:
        return f"{self._prefix}/{symbol.upper()}/"

    def _key(self, symbol: str, year: int) -> str:
        return f"{self._symbol_prefix(symbol)}{year}.parquet"

    def _partition_years(self, symbol: str) -> List[int]:
        def _list() -> List[int]:
            found = []
            paginator = self._client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self._credentials.bucket, Prefix=self._symbol_prefix(symbol)):
                for obj in page.get("Contents", []):
                    name = obj["Key"].rsplit("/", 1)[-1]
                    if name.endswith(".parquet"):
                        found.append(int(name[: -len(".parquet")]))
            return found

        return sorted(_with_retries(_list))

    def exists(self, symbol: str) -> bool:
        return bool(self._partition_years(symbol))

    def read(self, symbol: str) -> pd.DataFrame:
        years = self._partition_years(symbol)
        if not years:
            return empty_ohlcv_frame()
        frames = [self._read_object(self._key(symbol, year)) for year in years]
        return pd.concat(frames).sort_index()

    def write(self, symbol: str, df: pd.DataFrame) -> None:
        if df.empty:
            return
        for year, group in df.groupby(df.index.year):
            self._upsert_partition(symbol, int(year), group)

    def _upsert_partition(self, symbol: str, year: int, group: pd.DataFrame) -> None:
        key = self._key(symbol, year)
        existing = self._read_object_if_exists(key)
        if existing is not None:
            merged = pd.concat([existing, group])
            merged = merged[~merged.index.duplicated(keep="last")].sort_index()
        else:
            merged = group.sort_index()
        self._write_object(key, merged)
        self._write_completeness(symbol, year, merged)

    def _write_completeness(self, symbol: str, year: int, merged: pd.DataFrame) -> None:
        from .completeness import compute_completeness

        index = compute_completeness(symbol, year, merged)
        if index is not None:
            self.write_completeness_bytes(symbol, year, index.to_json().encode("utf-8"))

    def write_completeness_bytes(self, symbol: str, year: int, data: bytes) -> None:
        from .completeness import completeness_sidecar_suffix

        key = f"{self._symbol_prefix(symbol)}{year}{completeness_sidecar_suffix()}"

        def _put() -> None:
            self._client.put_object(Bucket=self._credentials.bucket, Key=key, Body=data)

        _with_retries(_put)

    def read_completeness_bytes(self, symbol: str, year: int) -> Optional[bytes]:
        from botocore.exceptions import ClientError

        from .completeness import completeness_sidecar_suffix

        key = f"{self._symbol_prefix(symbol)}{year}{completeness_sidecar_suffix()}"

        def _get() -> Optional[bytes]:
            try:
                response = self._client.get_object(Bucket=self._credentials.bucket, Key=key)
                return response["Body"].read()
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey"):
                    return None
                raise

        return _with_retries(_get)

    def _object_exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        def _head() -> bool:
            try:
                self._client.head_object(Bucket=self._credentials.bucket, Key=key)
                return True
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey"):
                    return False
                raise

        return _with_retries(_head)

    def _read_object_if_exists(self, key: str) -> Optional[pd.DataFrame]:
        if not self._object_exists(key):
            return None
        return self._read_object(key)

    def _read_object(self, key: str) -> pd.DataFrame:
        import io

        import pyarrow.parquet as pq

        def _get() -> bytes:
            response = self._client.get_object(Bucket=self._credentials.bucket, Key=key)
            return response["Body"].read()

        body = _with_retries(_get)
        table = pq.read_table(io.BytesIO(body))
        df = table.to_pandas()
        df = df.set_index(DATE)
        return df[OHLCV_COLUMNS]

    def _write_object(self, key: str, df: pd.DataFrame) -> None:
        import io

        import pyarrow as pa
        import pyarrow.parquet as pq

        table = pa.Table.from_pandas(df.reset_index(), preserve_index=False)
        buffer = io.BytesIO()
        pq.write_table(table, buffer)
        data = buffer.getvalue()

        def _put() -> None:
            self._client.put_object(Bucket=self._credentials.bucket, Key=key, Body=data)

        _with_retries(_put)
