"""R2 filesystem + DuckDB access -- both built on the S3-compatible surface
Cloudflare R2 exposes, deliberately WITHOUT adding boto3 as a new
dependency: pyarrow (already a hard dependency of this project) ships its
own S3FileSystem, which is enough for every read/write tam.marketdata's
MinuteBarStore needs. DuckDB's `httpfs` extension speaks the same protocol
directly from SQL, so no Python-side client is needed for querying at all --
just this module's configure_duckdb_r2() to point it at the right endpoint
and hand it credentials.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from .credentials import R2Credentials

if TYPE_CHECKING:
    import duckdb
    import pyarrow.fs


def r2_filesystem(credentials: R2Credentials) -> "pyarrow.fs.S3FileSystem":
    """A pyarrow S3FileSystem pointed at this account's R2 endpoint --
    passed to MinuteBarStore for either ingestion writes or ad hoc Python-
    side reads (interactive querying should prefer DuckDB's httpfs instead;
    see open_r2_duckdb() below). `region="auto"` is R2's own documented
    value -- R2 has no real regions, but the S3 protocol requires the field
    to be present."""
    import pyarrow.fs as fs

    return fs.S3FileSystem(
        endpoint_override=credentials.endpoint,
        access_key=credentials.access_key_id,
        secret_key=credentials.secret_access_key,
        scheme="https",
        region="auto",
    )


def r2_bucket_path(credentials: R2Credentials, *parts: str) -> str:
    """`<bucket>/<parts...>` -- the path shape pyarrow's filesystem API
    expects (no `s3://` scheme prefix; that's a DuckDB-SQL-string thing, see
    r2_uri() below)."""
    return "/".join((credentials.bucket, *parts))


def r2_uri(credentials: R2Credentials, *parts: str) -> str:
    """`s3://<bucket>/<parts...>` -- the URI shape DuckDB's `read_parquet()`/
    `COPY ... TO` SQL expects, as opposed to pyarrow's filesystem-relative
    path shape (r2_bucket_path() above)."""
    return "s3://" + r2_bucket_path(credentials, *parts)


def configure_duckdb_r2(con: "duckdb.DuckDBPyConnection", credentials: R2Credentials) -> None:
    """Installs/loads `httpfs` and points it at this account's R2 endpoint
    with the given credentials -- after this call, `con` can read/write
    `s3://<bucket>/...` paths directly in SQL. One shared setup path for
    local dev, Colab, and anywhere else DuckDB runs against this same
    bucket -- see tam.marketdata.duckdb_query.open_duckdb() for the
    higher-level entry point most callers should use instead of calling
    this directly."""
    con.sql("INSTALL httpfs; LOAD httpfs;")
    con.sql(
        f"""
        SET s3_endpoint = '{credentials.account_id}.r2.cloudflarestorage.com';
        SET s3_access_key_id = '{credentials.access_key_id}';
        SET s3_secret_access_key = '{credentials.secret_access_key}';
        SET s3_region = 'auto';
        SET s3_url_style = 'path';
        SET s3_use_ssl = true;
        """
    )
