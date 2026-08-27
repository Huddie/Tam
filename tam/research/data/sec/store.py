"""SecStore: R2-backed persistence for the SEC data lake's Parquet layers.

Four layers, each with its own partitioning rationale (see tam/research/
data/sec's package docstring and the approved plan for the full reasoning):

- reference: ONE small file, no partitioning (SEC's own company_tickers
  bulk file is already tiny).
- submissions (filing metadata) and facts (raw XBRL) and financials
  (derived/normalized): partitioned by fiscal_year (facts additionally by
  taxonomy) -- NOT by CIK. CIK stays a plain column. Partitioning by
  CIK-as-directory would recreate the "millions of tiny files" problem
  this design deliberately avoids (SEC filings are sparse -- a handful a
  year per company -- unlike the daily price bars tam.data/tam.marketdata
  partition by symbol).
- raw_filings: one object per (accession_number, document) -- the
  progressive, on-demand-only cache; never bulk-populated.

Every write is an UPSERT scoped to "this CIK's rows within this
partition" (read the partition, drop this CIK's existing rows, append the
new ones, write back) -- NOT a row-level content-hash dedup. A company's
full company-facts refetch is SEC's own complete current picture
(including past accession numbers for since-restated values), so
wholesale-replacing that company's slice of a shared partition is the
correct, safe upsert granularity -- the same reasoning
tam.marketdata.store's date-keyed upserts use, just keyed by CIK here
since a partition spans many companies instead of many days.
"""
from __future__ import annotations

import io
import time
from typing import Callable, List, Optional, TypeVar

import pandas as pd

from ....marketdata.credentials import R2Credentials, resolve_r2_credentials
from . import schema

_T = TypeVar("_T")


def _with_retries(func: Callable[[], _T], attempts: int = 5, base_delay: float = 2.0) -> _T:
    """Same reasoning and shape as tam.data.storage's/tam.marketdata.store's
    own copies of this -- duplicated rather than imported, small
    independent pieces per subpackage, matching this codebase's existing
    convention."""
    last_exc: Optional[Exception] = None
    for attempt in range(attempts):
        try:
            return func()
        except Exception as exc:  # noqa: BLE001 -- any transient network/S3 error should retry
            last_exc = exc
            if attempt < attempts - 1:
                time.sleep(base_delay * (2**attempt))
    assert last_exc is not None
    raise last_exc


class SecStore:
    """Cloudflare R2, via boto3 directly -- same reasoning as
    tam.marketdata.store.R2MinuteBarStore/tam.data.storage.R2DataStore
    (R2 has been observed failing pyarrow.fs.S3FileSystem's always-
    multipart uploads; boto3's plain put_object()/get_object() do a
    single PUT/GET for objects this small instead).

    `prefix` default "sec" -- <bucket>/sec/..., the same bucket
    tam.marketdata/tam.data already write to (under "minute/"/"eod/"
    there), under its own non-overlapping prefix.

    Reuses tam.marketdata.credentials' R2Credentials/resolve_r2_credentials
    -- generic S3-style credentials for the same physical account/bucket,
    not anything minute-bar-specific despite living in that module.
    """

    def __init__(self, credentials: Optional[R2Credentials] = None, prefix: str = "sec", client=None):
        self._credentials = credentials or resolve_r2_credentials()
        self._prefix = prefix.rstrip("/")
        # `client=` is a test-only seam (inject a fake S3 client instead of
        # a real boto3 one), matching R2DataStore's/R2MinuteBarStore's own
        # convention.
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

    # -- low-level object I/O ------------------------------------------------

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

    def _read_bytes(self, key: str) -> Optional[bytes]:
        from botocore.exceptions import ClientError

        def _get() -> Optional[bytes]:
            try:
                response = self._client.get_object(Bucket=self._credentials.bucket, Key=key)
                return response["Body"].read()
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey"):
                    return None
                raise

        return _with_retries(_get)

    def _write_bytes(self, key: str, data: bytes) -> None:
        def _put() -> None:
            self._client.put_object(Bucket=self._credentials.bucket, Key=key, Body=data)

        _with_retries(_put)

    def _read_parquet(self, key: str, columns: List[str]) -> pd.DataFrame:
        body = self._read_bytes(key)
        if body is None:
            return pd.DataFrame(columns=columns)
        import pyarrow.parquet as pq

        table = pq.read_table(io.BytesIO(body))
        return table.to_pandas()[columns]

    def _write_parquet(self, key: str, df: pd.DataFrame) -> None:
        import pyarrow as pa
        import pyarrow.parquet as pq

        table = pa.Table.from_pandas(df, preserve_index=False)
        buffer = io.BytesIO()
        pq.write_table(table, buffer)
        self._write_bytes(key, buffer.getvalue())

    def _upsert_by_cik(self, key: str, cik: int, new_rows: pd.DataFrame, columns: List[str]) -> None:
        """Read `key`'s existing partition (if any), drop every row for
        `cik`, append `new_rows`, write back -- see module docstring for
        why this (not row-level content-hash dedup) is the correct upsert
        granularity here."""
        existing = self._read_parquet(key, columns)
        remaining = existing[existing[schema.CIK] != cik] if not existing.empty else existing
        merged = pd.concat([remaining, new_rows[columns]], ignore_index=True)
        self._write_parquet(key, merged)

    # -- reference ------------------------------------------------------------

    def _reference_key(self) -> str:
        return f"{self._prefix}/reference/company_tickers.parquet"

    def read_reference(self) -> pd.DataFrame:
        return self._read_parquet(self._reference_key(), schema.REFERENCE_COLUMNS)

    def write_reference(self, df: pd.DataFrame) -> None:
        self._write_parquet(self._reference_key(), df[schema.REFERENCE_COLUMNS])

    # -- submissions (filing metadata) ----------------------------------------

    def _submissions_key(self, fiscal_year: int) -> str:
        return f"{self._prefix}/submissions/fiscal_year={fiscal_year}/filings.parquet"

    def read_submissions(self, fiscal_year: int) -> pd.DataFrame:
        return self._read_parquet(self._submissions_key(fiscal_year), schema.SUBMISSIONS_COLUMNS)

    def write_submissions(self, cik: int, fiscal_year: int, df: pd.DataFrame) -> None:
        if df.empty:
            return
        self._upsert_by_cik(self._submissions_key(fiscal_year), cik, df, schema.SUBMISSIONS_COLUMNS)

    # -- raw XBRL facts ---------------------------------------------------------

    def _facts_key(self, taxonomy: str, fiscal_year: int) -> str:
        return f"{self._prefix}/facts/taxonomy={taxonomy}/fiscal_year={fiscal_year}/facts.parquet"

    def read_facts(self, taxonomy: str, fiscal_year: int) -> pd.DataFrame:
        return self._read_parquet(self._facts_key(taxonomy, fiscal_year), schema.FACTS_COLUMNS)

    def write_facts(self, cik: int, taxonomy: str, fiscal_year: int, df: pd.DataFrame) -> None:
        if df.empty:
            return
        self._upsert_by_cik(self._facts_key(taxonomy, fiscal_year), cik, df, schema.FACTS_COLUMNS)

    def list_facts_partitions(self) -> List["tuple[str, int]"]:
        """Every (taxonomy, fiscal_year) pair with an actual facts.parquet
        object on R2 right now -- scripts/rebuild_sec_financials.py uses
        this instead of guessing a fixed year range, since the real range
        depends on how far back the curated universe's backfill actually
        reached."""
        prefix = f"{self._prefix}/facts/"

        def _list() -> List["tuple[str, int]"]:
            partitions = []
            paginator = self._client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self._credentials.bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    parts = obj["Key"][len(prefix) :].split("/")
                    if len(parts) == 3 and parts[0].startswith("taxonomy=") and parts[1].startswith("fiscal_year="):
                        partitions.append((parts[0][len("taxonomy=") :], int(parts[1][len("fiscal_year=") :])))
            return partitions

        return _with_retries(_list)

    # -- normalized financials (derived, rebuildable) --------------------------

    def _financials_key(self, fiscal_year: int) -> str:
        return f"{self._prefix}/financials/fiscal_year={fiscal_year}/financials.parquet"

    def read_financials(self, fiscal_year: int) -> pd.DataFrame:
        return self._read_parquet(self._financials_key(fiscal_year), schema.FINANCIALS_COLUMNS)

    def write_financials(self, cik: int, fiscal_year: int, df: pd.DataFrame) -> None:
        if df.empty:
            return
        self._upsert_by_cik(self._financials_key(fiscal_year), cik, df, schema.FINANCIALS_COLUMNS)

    # -- filing documents (progressive, on-demand cache) ------------------------

    def _filing_document_key(self, accession_number: str, document: str) -> str:
        return f"{self._prefix}/raw_filings/{accession_number}/{document}"

    def filing_document_exists(self, accession_number: str, document: str) -> bool:
        return self._object_exists(self._filing_document_key(accession_number, document))

    def read_filing_document(self, accession_number: str, document: str) -> Optional[bytes]:
        return self._read_bytes(self._filing_document_key(accession_number, document))

    def write_filing_document(self, accession_number: str, document: str, data: bytes) -> None:
        self._write_bytes(self._filing_document_key(accession_number, document), data)

    # -- manifest (see manifest.py's Manifest class) ----------------------------

    def _manifest_key(self) -> str:
        return f"{self._prefix}/_manifest.json"

    def read_manifest_bytes(self) -> Optional[bytes]:
        return self._read_bytes(self._manifest_key())

    def write_manifest_bytes(self, data: bytes) -> None:
        self._write_bytes(self._manifest_key(), data)
