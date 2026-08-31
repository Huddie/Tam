"""Shared "how do I get a query connection" resolution chain -- extracted
from tam.research.data.sec.Sec's own _connect() (the original, and still
the only other, caller) so tam.Symbol can use the IDENTICAL logic without
either duplicating it or importing something SEC-specific for a completely
generic concern.

Resolution order: an explicit `local_root=` (or any other
tam.marketdata.duckdb_query.open_duckdb() kwarg, e.g. `bucket=`) wins
outright if given -- otherwise a `TAM_PAT` personal token (explicit
`token=` -> env var/.env -> Colab secret -> saved
~/.config/tam-data-explorer/token, see
tam.marketdata.explorer_client.resolve_token()), the same self-service,
READ-ONLY path this project recommends for daily_bars/eod_bars/sec_stmt in
an ordinary notebook. Raises a clear, actionable error if neither an
explicit override nor a token resolves to anything.
"""

from __future__ import annotations

import threading
from typing import Any, Optional


def resolve_connection(
    *,
    token: Optional[str] = None,
    api_url: Optional[str] = None,
    ttl_seconds: Optional[int] = None,
    local_root: Optional[str] = None,
    **open_duckdb_kwargs: Any,
):
    if local_root is not None or open_duckdb_kwargs:
        # Explicit local_root (tests, local dev) or raw R2 credentials/
        # bucket override requested -- wins outright, same as
        # tam.marketdata.duckdb_query's own module docstring recommends
        # for ingestion scripts.
        from .duckdb_query import open_duckdb

        return open_duckdb(local_root=local_root, **open_duckdb_kwargs)

    # Default: the same self-service TAM_PAT token path NOTEBOOK.md
    # recommends for daily_bars/eod_bars -- mints a short-lived, read-only
    # R2 credential behind the scenes, no raw R2_ACCOUNT_ID/
    # R2_ACCESS_KEY_ID/... needed. Only explorer_client's own
    # SqlConnection actually knows how to refresh that credential as it
    # nears expiry, which is why this delegates to it instead of
    # duplicating that logic here. No further fallback -- silently
    # reading whatever happens to be in a local `data/` directory if the
    # token isn't configured would be more likely to confuse (stale/
    # unrelated local fixtures) than help; better to fail clearly here.
    from .explorer_client import connect, resolve_token

    resolved_token = resolve_token(token, required=False)
    if resolved_token is None:
        raise RuntimeError(
            "No TAM_PAT personal token found (checked an explicit token=, the TAM_PAT "
            "environment variable/.env file, a Colab secret, and "
            "~/.config/tam-data-explorer/token). Pick one:\n"
            "  1. Pass token=... directly, or set the TAM_PAT environment variable "
            "(create one at https://data.tamquant.com/settings/tokens).\n"
            "  2. Pass local_root=... pointing at a local Parquet tree.\n"
            "  3. Pass bucket=... plus R2_ACCOUNT_ID/R2_ACCESS_KEY_ID/R2_SECRET_ACCESS_KEY "
            "env vars for raw R2 admin access."
        )
    return connect(token=resolved_token, api_url=api_url, ttl_seconds=ttl_seconds)


_shared_connection = None
_thread_local = threading.local()


def thread_local_connection():
    """One connection PER CALLING THREAD, built lazily on first use and
    reused after that -- the safe alternative to default_connection() for
    any caller that might run from a thread pool.

    default_connection() hands every default-configured caller in the
    process the SAME connection object, which is exactly right for a
    single-threaded notebook cell (see that function's own docstring) but
    unsafe the moment more than one thread calls it concurrently: a DuckDB
    connection isn't safe to use from multiple threads at once. Confirmed
    live -- tam.marketdata.eod_provider.MarketDataEodProvider originally
    used default_connection() and crashed the whole Python process with no
    catchable exception (a native crash, not a Python one) as soon as
    DataRepository.ingest()'s own ThreadPoolExecutor(max_workers=8)
    (tam/data/repository.py) fanned concurrent fetches out across real
    threads. Each thread minting its own connection here (and its own
    short-lived R2 credential the first time it's used) costs a little
    more than sharing one, but avoids that failure mode entirely."""
    if not hasattr(_thread_local, "connection"):
        _thread_local.connection = resolve_connection()
    return _thread_local.connection


def default_connection():
    """The one connection `tam.Symbol`/`tam.query()`/`tam.research.data.
    sec.Sec`'s own shared default instance all fall back to when NONE of
    them are given an explicit override (`con=`, `local_root=`, a raw R2
    kwarg, ...) -- built once via resolve_connection() on first use, then
    reused. Sharing this one connection (and, when it's the TAM_PAT path,
    one minted temporary R2 credential) across every default-configured
    caller in a process is the whole point -- constructing ten `Symbol(...)`
    instances in a notebook shouldn't mint ten separate credentials for
    the same read-only access."""
    global _shared_connection
    if _shared_connection is None:
        _shared_connection = resolve_connection()
    return _shared_connection


def is_missing_glob_error(exc: Exception, *path_hints: str) -> bool:
    """True if `exc` is DuckDB's own "IO Error: No files found that match
    the pattern ..." -- confirmed live as the exact, consistent wording
    for a Parquet glob matching zero files, e.g. before a given lake's
    first backfill. Originally a private copy inside
    tam.research.data.sec's own Sec class; promoted here since it's
    entirely generic (interpreting a DuckDB error, not anything SEC-
    specific) and tam.Symbol needs the identical check. If `path_hints`
    are given, they must ALL also appear in the message -- e.g.
    "reference", to distinguish "this whole layer is missing" (a real
    configuration problem) from "this dataset just has no rows yet" (a
    legitimate, expected state that should return empty, not raise)."""
    message = str(exc)
    if "No files found that match the pattern" not in message:
        return False
    return all(hint in message for hint in path_hints)
