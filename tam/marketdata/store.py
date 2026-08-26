"""MinuteBarStore: persist and retrieve one symbol's 1-minute OHLCV history,
partitioned by year -- <root>/<SYMBOL>/<year>.parquet, the SAME layout and
UPSERT-by-year-partition semantics tam.data.storage._YearPartitionedStore
already uses for end-of-day data (see that module's own docstring).

Two concrete backends, DELIBERATELY not sharing one implementation anymore:

- LocalMinuteBarStore: plain local disk, via pyarrow.fs.LocalFileSystem
  (ParquetFileSystemStore below) -- what tests and local dev use.
- R2MinuteBarStore: Cloudflare R2, via boto3 directly, NOT pyarrow's
  S3FileSystem. They used to share one pyarrow.fs.FileSystem-generalized
  implementation, on the theory that "any pyarrow filesystem" made local
  and R2 interchangeable for free. In production that generalization
  actively caused failures: pyarrow's S3FileSystem ALWAYS performs a
  multipart upload for open_output_stream() writes (not controllable via
  its public API, regardless of file size or write-call pattern), and R2
  was observed failing to reliably complete that multipart handshake --
  "NO_SUCH_UPLOAD during CompleteMultipartUpload" / an internal-error
  response on an otherwise-successful upload -- crashing a real multi-hour
  backfill outright. boto3's plain put_object()/get_object() do a single
  PUT/GET for objects this small (a few hundred KB), never touching
  multipart at all -- the same approach Massive's own "Flat Files
  Quickstart" docs demonstrate for the read side of this same pipeline.
  Local disk I/O has no multipart concept at all, so LocalMinuteBarStore
  has no reason to change.

An incremental daily ingest only reads/rewrites the year(s) actually touched
by new rows, same reasoning as the EOD store's own docstring: a year of
1-minute bars is small (~98k rows/symbol), but the point-in-time universe
runs to hundreds of symbols, and re-writing everyone's full 20-year history
on every day's ingest would be needlessly slow.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Callable, List, Optional, TypeVar

import pandas as pd

from ..registry import Registry
from .credentials import R2Credentials, resolve_r2_credentials
from .schema import MINUTE_BAR_COLUMNS, TS, empty_minute_bar_frame, ensure_utc_index

if TYPE_CHECKING:
    import pyarrow.fs

_MANIFEST_FILENAME = "_manifest.json"
_T = TypeVar("_T")


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

    def read_manifest_bytes(self) -> Optional[bytes]:
        """Raw bytes of this store's ingestion-resumability manifest (see
        tam.marketdata.ingest._Manifest), or None if it doesn't exist yet.
        Default: no manifest support at all -- a store that doesn't
        override this always looks like "nothing ingested yet" on resume,
        which is still CORRECT (every day gets redone, and write()'s UPSERT
        makes that safe), just not a fast resume. Overridden by both
        concrete stores below via their own real backing storage, so this
        default only matters for a custom third-party MinuteBarStore that
        hasn't opted in."""
        return None

    def write_manifest_bytes(self, data: bytes) -> None:
        """Persist `data` as this store's manifest. Default: a no-op,
        matching read_manifest_bytes()'s "no manifest support" default."""
        return None


def _with_retries(func: Callable[[], _T], attempts: int = 5, base_delay: float = 2.0) -> _T:
    """Retries `func` up to `attempts` times with exponential backoff --
    hardening against both a transient, non-retriable-by-boto3-itself R2
    failure (an "internal error" on an operation that otherwise succeeded)
    AND a plain transient network blip (DNS resolution failure, WiFi drop,
    laptop sleep/wake) -- confirmed in production for both: a multi-hour
    unattended backfill hit a DNS NameResolutionError partway through and
    needs a retry window generous enough to actually ride that out, not
    just the ~3 seconds the original 3-attempt/1s-base default gave it.
    5 attempts at 2/4/8/16s backoff is ~30s of total retry window. Re-raises
    the last exception if every attempt fails; never swallows a genuine,
    persistent error silently."""
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


class ParquetFileSystemStore(MinuteBarStore):
    """Year-partitioned Parquet via any pyarrow.fs.FileSystem + a root path
    prefix (filesystem-relative -- e.g. a bare local directory for
    pyarrow.fs.LocalFileSystem; NOT a pathlib.Path). `symbol` is kept as an
    actual column inside every file, redundant with its path but making
    each file self-describing for a cross-symbol glob query (e.g. every
    symbol's 2015 bars at once) without relying on filename parsing.

    Only ever used for LOCAL disk today (see module docstring for why R2
    moved off this class entirely) -- kept generalized over
    pyarrow.fs.FileSystem anyway since local disk genuinely has no
    reliability issue this generalization would paper over.
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
        buffer = pa.BufferOutputStream()
        pq.write_table(table, buffer)
        with self._fs.open_output_stream(path) as handle:
            handle.write(buffer.getvalue())

    def read_manifest_bytes(self) -> Optional[bytes]:
        path = f"{self._root}/{_MANIFEST_FILENAME}"
        if not self._file_exists(path):
            return None
        with self._fs.open_input_file(path) as handle:
            return handle.readall()

    def write_manifest_bytes(self, data: bytes) -> None:
        path = f"{self._root}/{_MANIFEST_FILENAME}"
        self._fs.create_dir(self._root, recursive=True)
        with self._fs.open_output_stream(path) as handle:
            handle.write(data)


@Registry.register(MinuteBarStore, "local_parquet")
class LocalMinuteBarStore(ParquetFileSystemStore):
    """Plain local disk -- what tests and local dev use; identical
    partition layout/UPSERT behavior to the R2-backed store below, so
    validating a small backfill locally before pointing it at R2 (see
    MARKETDATA.md) exercises the equivalent code path (not the literal same
    class as R2MinuteBarStore anymore -- see module docstring)."""

    def __init__(self, root: "str | Path"):
        import pyarrow.fs as fs

        super().__init__(fs.LocalFileSystem(), root)


@Registry.register(MinuteBarStore, "r2_parquet")
class R2MinuteBarStore(MinuteBarStore):
    """Cloudflare R2, via boto3 directly (NOT pyarrow.fs.S3FileSystem -- see
    module docstring for why). `credentials`, if omitted, resolves the
    usual way (tam.marketdata.credentials.resolve_r2_credentials: kwarg ->
    env var -> Colab secret -> saved file). `prefix` is the path inside the
    bucket (default "minute", i.e. <bucket>/minute/<SYMBOL>/<year>.parquet).

    Every R2 call is wrapped in a bounded retry (_with_retries) -- R2 has
    been observed returning a transient internal error on an operation that
    otherwise succeeded; retrying a few times with backoff is cheap
    insurance against exactly that, while still raising (not silently
    swallowing) a persistent failure.
    """

    def __init__(self, credentials: Optional[R2Credentials] = None, prefix: str = "minute", client=None):
        self._credentials = credentials or resolve_r2_credentials()
        self._prefix = prefix.rstrip("/")
        # `client=` is a test-only seam (inject a fake S3 client instead of
        # a real boto3 one, matching this project's fakes-over-mocking-
        # libraries convention elsewhere) -- production callers never pass
        # it.
        self._client = client or self._build_client()

    def _build_client(self):
        import boto3
        from botocore.config import Config

        session = boto3.Session(
            aws_access_key_id=self._credentials.access_key_id,
            aws_secret_access_key=self._credentials.secret_access_key,
        )
        # region_name="auto" -- R2 REJECTS a real AWS region name outright
        # (InvalidRegionName; it only accepts "auto" or one of its own
        # continent tokens), unlike some other S3-compatible services (e.g.
        # Massive's own endpoint, which is why the boto3 snippet in their
        # docs doesn't need to set this at all -- it just doesn't validate
        # the field). Without this, boto3 falls back to whatever region is
        # configured/default on the machine running this (observed: a
        # plain AWS region like "us-west-2" from local AWS config/env),
        # which R2 then rejects. Matches tam.marketdata.filesystem.
        # r2_filesystem()'s own region="auto" for the pyarrow client.
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

    def _manifest_key(self) -> str:
        return f"{self._prefix}/{_MANIFEST_FILENAME}"

    def _partition_years(self, symbol: str) -> List[int]:
        years = []

        def _list() -> List[int]:
            found = []
            paginator = self._client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self._credentials.bucket, Prefix=self._symbol_prefix(symbol)):
                for obj in page.get("Contents", []):
                    name = obj["Key"].rsplit("/", 1)[-1]
                    if name.endswith(".parquet"):
                        found.append(int(name[: -len(".parquet")]))
            return found

        years = _with_retries(_list)
        return sorted(years)

    def exists(self, symbol: str) -> bool:
        return bool(self._partition_years(symbol))

    def read(self, symbol: str) -> pd.DataFrame:
        years = self._partition_years(symbol)
        if not years:
            return empty_minute_bar_frame()
        frames = [self._read_object(self._key(symbol, year)) for year in years]
        return pd.concat(frames).sort_index()

    def write(self, symbol: str, df: pd.DataFrame) -> None:
        if df.empty:
            return
        df = ensure_utc_index(df)
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
        df = df.set_index(TS)
        df.index = pd.DatetimeIndex(df.index).tz_convert("UTC")
        df.index.name = TS
        return df[MINUTE_BAR_COLUMNS]

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

    def read_manifest_bytes(self) -> Optional[bytes]:
        from botocore.exceptions import ClientError

        def _get() -> Optional[bytes]:
            try:
                response = self._client.get_object(Bucket=self._credentials.bucket, Key=self._manifest_key())
                return response["Body"].read()
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey"):
                    return None
                raise

        return _with_retries(_get)

    def write_manifest_bytes(self, data: bytes) -> None:
        def _put() -> None:
            self._client.put_object(Bucket=self._credentials.bucket, Key=self._manifest_key(), Body=data)

        _with_retries(_put)
