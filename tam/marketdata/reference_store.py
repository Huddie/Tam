"""ReferenceStore: persist and retrieve the six Massive/Polygon reference
datasets -- splits, dividends, IPOs, short volume, short interest, float.
Grouped under two R2 top-level prefixes by what the data represents (see
this module's own _DATASET_GROUPS below): `corporate_actions/` (splits,
dividends, IPOs -- things a company actively does) and `positioning/`
(short volume, short interest, float -- things about how shares are held
or traded). Sibling to `minute/`/`eod/`, not nested under either.

Four datasets (splits, dividends, short_volume, short_interest) are
append-only and year-partitioned -- `<group>/<dataset>/<year>.parquet` --
UPSERTed the same way tam.marketdata.store.R2MinuteBarStore already
upserts minute bars (read existing year, concat, dedup, rewrite), just
deduplicated on each dataset's own natural key (see _DEDUP_KEYS) instead
of a timestamp index, since these aren't indexed by a single always-unique
timestamp the way bars are. Two datasets (ipos, float) have no history/
cursor concept at all -- `<group>/<dataset>/all.parquet`, wholesale
overwritten on every write.

Two concrete backends, same reasoning as tam.marketdata.store's own split:
- LocalReferenceStore: plain local disk, for tests and local dev.
- R2ReferenceStore: Cloudflare R2, via boto3 directly (NOT
  pyarrow.fs.S3FileSystem) -- see tam.marketdata.store's own module
  docstring for why (R2 was observed failing pyarrow's multipart-upload
  handshake in production; boto3's single-shot put_object()/get_object()
  never touches multipart for objects this size).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional

import pandas as pd

from . import reference_schema as schema
from .credentials import R2Credentials, resolve_r2_credentials
from .store import _with_retries

if TYPE_CHECKING:
    pass

_MANIFEST_FILENAME = "_manifest.json"

# Which top-level R2 prefix each dataset lives under -- see module docstring.
_DATASET_GROUPS: Dict[str, str] = {
    "splits": "corporate_actions",
    "dividends": "corporate_actions",
    "ipos": "corporate_actions",
    "short_volume": "positioning",
    "short_interest": "positioning",
    "float": "positioning",
}

# ipos/float have no history/cursor concept -- a single file, wholesale
# overwritten every run (see reference_provider.py's fetch_ipos()/
# fetch_float() docstrings for why). Everything else is append-only and
# year-partitioned.
_SNAPSHOT_DATASETS = {"ipos", "float"}

# The date column each append-only dataset's rows are year-partitioned by.
_DATE_COLUMNS: Dict[str, str] = {
    "splits": schema.SPLIT_EXECUTION_DATE,
    "dividends": schema.DIVIDEND_EX_DIVIDEND_DATE,
    "short_volume": schema.SHORT_VOLUME_DATE,
    "short_interest": schema.SHORT_INTEREST_SETTLEMENT_DATE,
}

# The natural unique key each append-only dataset dedups on when merging a
# freshly-fetched batch into an existing year partition -- splits/dividends
# carry a real vendor-assigned `id`; short volume/interest don't, so
# (ticker, date) is the natural key instead (one row per ticker per
# reporting date).
_DEDUP_KEYS: Dict[str, List[str]] = {
    "splits": [schema.SPLIT_ID],
    "dividends": [schema.DIVIDEND_ID],
    "short_volume": [schema.TICKER, schema.SHORT_VOLUME_DATE],
    "short_interest": [schema.TICKER, schema.SHORT_INTEREST_SETTLEMENT_DATE],
}

_COLUMNS: Dict[str, List[str]] = {
    "splits": schema.SPLIT_COLUMNS,
    "dividends": schema.DIVIDEND_COLUMNS,
    "ipos": schema.IPO_COLUMNS,
    "short_volume": schema.SHORT_VOLUME_COLUMNS,
    "short_interest": schema.SHORT_INTEREST_COLUMNS,
    "float": schema.FLOAT_COLUMNS,
}


class ReferenceStore(ABC):
    """Persists and retrieves one of the six reference datasets (by name
    -- "splits" | "dividends" | "ipos" | "short_volume" | "short_interest"
    | "float"). `write()` is an UPSERT for the four append-only datasets
    (merges into whatever's already stored, deduplicating on that
    dataset's natural key) and a wholesale overwrite for the two
    full-refresh ones (ipos, float) -- callers don't need to know which;
    `write()` looks it up via _SNAPSHOT_DATASETS itself."""

    @abstractmethod
    def read(self, dataset: str) -> pd.DataFrame: ...

    @abstractmethod
    def write(self, dataset: str, df: pd.DataFrame) -> None: ...

    def read_manifest_bytes(self, group: str) -> Optional[bytes]:
        """Raw bytes of `group`'s manifest ("corporate_actions" |
        "positioning") -- see tam.marketdata.reference_ingest.Manifest.
        Default: no manifest support (every run re-does everything, which
        write()'s own dedup-on-merge makes safe, just not a fast resume)."""
        return None

    def write_manifest_bytes(self, group: str, data: bytes) -> None:
        return None


class LocalReferenceStore(ReferenceStore):
    """Plain local disk -- what tests and local dev use. Same key layout
    as R2ReferenceStore (<root>/<group>/<dataset>/<year-or-'all'>.parquet),
    just plain file I/O rather than boto3, so validating a local backfill
    before pointing it at R2 exercises the equivalent code path."""

    def __init__(self, root: "str | Path"):
        self._root = Path(root)

    def _path(self, dataset: str, year: Optional[int] = None) -> Path:
        group = _DATASET_GROUPS[dataset]
        name = "all.parquet" if dataset in _SNAPSHOT_DATASETS else f"{year}.parquet"
        return self._root / group / dataset / name

    def _dataset_dir(self, dataset: str) -> Path:
        return self._root / _DATASET_GROUPS[dataset] / dataset

    def _partition_years(self, dataset: str) -> List[int]:
        directory = self._dataset_dir(dataset)
        if not directory.exists():
            return []
        years = []
        for path in directory.glob("*.parquet"):
            if path.stem.isdigit():
                years.append(int(path.stem))
        return sorted(years)

    def read(self, dataset: str) -> pd.DataFrame:
        columns = _COLUMNS[dataset]
        if dataset in _SNAPSHOT_DATASETS:
            path = self._path(dataset)
            if not path.exists():
                return schema.empty_frame(columns)
            return pd.read_parquet(path)[columns]
        years = self._partition_years(dataset)
        if not years:
            return schema.empty_frame(columns)
        frames = [pd.read_parquet(self._path(dataset, year))[columns] for year in years]
        return pd.concat(frames, ignore_index=True)

    def write(self, dataset: str, df: pd.DataFrame) -> None:
        if dataset in _SNAPSHOT_DATASETS:
            self._write_file(self._path(dataset), df)
            return
        if df.empty:
            return
        date_col = _DATE_COLUMNS[dataset]
        years = pd.to_datetime(df[date_col]).dt.year
        for year, group in df.groupby(years):
            self._upsert_partition(dataset, int(year), group)

    def _upsert_partition(self, dataset: str, year: int, group: pd.DataFrame) -> None:
        path = self._path(dataset, year)
        if path.exists():
            existing = pd.read_parquet(path)
            combined = pd.concat([existing, group], ignore_index=True)
        else:
            combined = group
        combined = combined.drop_duplicates(subset=_DEDUP_KEYS[dataset], keep="last")
        self._write_file(path, combined)

    def _write_file(self, path: Path, df: pd.DataFrame) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        dataset = path.parent.name
        table_columns = _COLUMNS.get(dataset)
        if table_columns is not None:
            import pyarrow as pa

            table = pa.Table.from_pandas(df.reindex(columns=table_columns), schema=schema.pyarrow_schema(table_columns), preserve_index=False)
            import pyarrow.parquet as pq

            pq.write_table(table, path)
        else:
            df.to_parquet(path, index=False)

    def _manifest_path(self, group: str) -> Path:
        return self._root / group / _MANIFEST_FILENAME

    def read_manifest_bytes(self, group: str) -> Optional[bytes]:
        path = self._manifest_path(group)
        if not path.exists():
            return None
        return path.read_bytes()

    def write_manifest_bytes(self, group: str, data: bytes) -> None:
        path = self._manifest_path(group)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


class R2ReferenceStore(ReferenceStore):
    """Cloudflare R2, via boto3 directly -- see module docstring for why.
    `credentials`, if omitted, resolves the usual way
    (tam.marketdata.credentials.resolve_r2_credentials). Every R2 call is
    wrapped in tam.marketdata.store's shared `_with_retries` (imported,
    not duplicated -- this one genuinely is the same retry policy for the
    same underlying reason, unlike the vendor-key-resolution helpers,
    which differ enough per-vendor-product to stay independent copies)."""

    def __init__(self, credentials: Optional[R2Credentials] = None, client=None):
        self._credentials = credentials or resolve_r2_credentials()
        self._client = client or self._build_client()

    def _build_client(self):
        import boto3
        from botocore.config import Config

        session = boto3.Session(
            aws_access_key_id=self._credentials.access_key_id,
            aws_secret_access_key=self._credentials.secret_access_key,
        )
        return session.client(
            "s3",
            endpoint_url=self._credentials.endpoint,
            region_name="auto",
            config=Config(signature_version="s3v4"),
        )

    def _key(self, dataset: str, year: Optional[int] = None) -> str:
        group = _DATASET_GROUPS[dataset]
        name = "all.parquet" if dataset in _SNAPSHOT_DATASETS else f"{year}.parquet"
        return f"{group}/{dataset}/{name}"

    def _dataset_prefix(self, dataset: str) -> str:
        return f"{_DATASET_GROUPS[dataset]}/{dataset}/"

    def _manifest_key(self, group: str) -> str:
        return f"{group}/{_MANIFEST_FILENAME}"

    def _partition_years(self, dataset: str) -> List[int]:
        def _list() -> List[int]:
            found = []
            paginator = self._client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self._credentials.bucket, Prefix=self._dataset_prefix(dataset)):
                for obj in page.get("Contents", []):
                    name = obj["Key"].rsplit("/", 1)[-1]
                    stem = name[: -len(".parquet")] if name.endswith(".parquet") else ""
                    if stem.isdigit():
                        found.append(int(stem))
            return found

        return sorted(_with_retries(_list))

    def read(self, dataset: str) -> pd.DataFrame:
        columns = _COLUMNS[dataset]
        if dataset in _SNAPSHOT_DATASETS:
            df = self._read_object_if_exists(self._key(dataset))
            return df if df is not None else schema.empty_frame(columns)
        years = self._partition_years(dataset)
        if not years:
            return schema.empty_frame(columns)
        frames = [self._read_object(self._key(dataset, year)) for year in years]
        return pd.concat(frames, ignore_index=True)

    def write(self, dataset: str, df: pd.DataFrame) -> None:
        if dataset in _SNAPSHOT_DATASETS:
            self._write_object(self._key(dataset), df, dataset)
            return
        if df.empty:
            return
        date_col = _DATE_COLUMNS[dataset]
        years = pd.to_datetime(df[date_col]).dt.year
        for year, group in df.groupby(years):
            self._upsert_partition(dataset, int(year), group)

    def _upsert_partition(self, dataset: str, year: int, group: pd.DataFrame) -> None:
        key = self._key(dataset, year)
        existing = self._read_object_if_exists(key)
        combined = pd.concat([existing, group], ignore_index=True) if existing is not None else group
        combined = combined.drop_duplicates(subset=_DEDUP_KEYS[dataset], keep="last")
        self._write_object(key, combined, dataset)

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
        return table.to_pandas()

    def _write_object(self, key: str, df: pd.DataFrame, dataset: str) -> None:
        import io

        import pyarrow as pa
        import pyarrow.parquet as pq

        columns = _COLUMNS[dataset]
        table = pa.Table.from_pandas(df.reindex(columns=columns), schema=schema.pyarrow_schema(columns), preserve_index=False)
        buffer = io.BytesIO()
        pq.write_table(table, buffer)
        data = buffer.getvalue()

        def _put() -> None:
            self._client.put_object(Bucket=self._credentials.bucket, Key=key, Body=data)

        _with_retries(_put)

    def read_manifest_bytes(self, group: str) -> Optional[bytes]:
        from botocore.exceptions import ClientError

        def _get() -> Optional[bytes]:
            try:
                response = self._client.get_object(Bucket=self._credentials.bucket, Key=self._manifest_key(group))
                return response["Body"].read()
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey"):
                    return None
                raise

        return _with_retries(_get)

    def write_manifest_bytes(self, group: str, data: bytes) -> None:
        def _put() -> None:
            self._client.put_object(Bucket=self._credentials.bucket, Key=self._manifest_key(group), Body=data)

        _with_retries(_put)
