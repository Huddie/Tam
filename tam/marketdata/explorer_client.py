"""Python client for tam-data-explorer (data.tamquant.com) -- a personal API
token (self-service, create/revoke your own at data.tamquant.com/settings/
tokens, exactly like tam.discovery.auth's own publishing tokens) gets you
either a single file as a DataFrame (fetch_dataframe(), no SQL engine setup
at all), or a full SQL connection over the whole lake (connect()).

connect() works by minting a short-lived, READ-ONLY, real R2 S3 credential
scoped to just the tam-data bucket (Cloudflare's own R2 temporary-credentials
scheme, see /tam-data-explorer/src/worker/lib/r2-credentials.ts) -- your
personal token is what's allowed to mint these, so revoking it stops new
ones from being issued (an already-minted credential still works until its
own short TTL expires; R2 has no separate revocation for those). The
returned connection transparently mints a fresh credential and re-points
itself at it whenever the current one is close to expiring, so a
long-running notebook session doesn't just stop working partway through.
This gives the SAME glob/multi-file SQL access as
tam.marketdata.duckdb_query.open_duckdb() (identical macros -- daily_bars,
rollup_bars, rolling_volatility, ...), just without ever handling the real,
permanent R2 account credentials yourself. What's actually running the
queries (DuckDB, today) is an implementation detail, not part of this
module's public API.

    from tam.marketdata.explorer_client import fetch_dataframe, connect

    df = fetch_dataframe("AAPL", 2024)                     # one file, plain HTTP
    con = connect()                                         # full lake, real SQL
    con.sql("SELECT * FROM daily_bars('AAPL') ORDER BY day").df()
"""
from __future__ import annotations

import io
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import pandas as pd
import requests
from dotenv import dotenv_values, find_dotenv

from .duckdb_query import _register_macros

_ENV_VAR = "DATA_EXPLORER_TOKEN"
_API_URL_ENV_VAR = "DATA_EXPLORER_API_URL"
_DEFAULT_API_URL = "https://data.tamquant.com"

# How long before a minted credential's real expiry to proactively refresh --
# comfortably longer than one query should ever take, so a query started
# just before expiry doesn't get cut off mid-flight by R2 itself.
_REFRESH_MARGIN = timedelta(seconds=60)


def credentials_file_path() -> Path:
    """~/.config/tam-data-explorer/token -- same ~/.config/<tool>/ convention
    as tam.discovery.auth.token_file_path(), a single plain-text token file.
    Nothing writes this automatically (no `login` command for this client
    yet) -- create it by hand if you want the saved-file fallback."""
    return Path.home() / ".config" / "tam-data-explorer" / "token"


def _from_colab() -> Optional[str]:
    """Same broad-except pattern as tam.discovery.auth._from_colab -- only
    attempted when actually running in Colab; any failure (no such secret,
    no notebook access granted yet) just falls through to the next
    resolution source."""
    if "google.colab" not in sys.modules:
        return None
    try:
        from google.colab import userdata  # type: ignore
    except ImportError:
        return None
    try:
        value = userdata.get(_ENV_VAR)
    except Exception:
        return None
    return value or None


def _from_file() -> Optional[str]:
    try:
        text = credentials_file_path().read_text().strip()
    except OSError:
        return None
    return text or None


def _from_dotenv() -> Optional[str]:
    """python-dotenv's own find_dotenv() walks up from the current working
    directory looking for a .env file (so this works whether a script runs
    from a project's root or one of its subdirectories); dotenv_values()
    just parses it into a dict without touching os.environ, since loading
    the WHOLE file into the process environment is a bigger behavior change
    than "find my token" calls for."""
    path = find_dotenv(usecwd=True)
    if not path:
        return None
    return dotenv_values(path).get(_ENV_VAR) or None


def resolve_token(explicit: Optional[str] = None) -> str:
    """Resolution order: explicit kwarg -> DATA_EXPLORER_TOKEN env var
    (directly, or via a .env file) -> Colab secret (same name,
    auto-detected -- nothing to configure differently just because you're
    in Colab) -> ~/.config/tam-data-explorer/token. Raises a clear,
    actionable RuntimeError listing every option if none of them produced
    anything."""
    if explicit:
        return explicit
    value = os.environ.get(_ENV_VAR) or _from_dotenv() or _from_colab() or _from_file()
    if value:
        return value
    raise RuntimeError(
        "No Data Explorer personal token found. Pick one:\n"
        "  1. Pass token=... directly.\n"
        f"  2. Set the {_ENV_VAR} environment variable (directly, or via a .env file).\n"
        f"  3. In Colab: add a secret named {_ENV_VAR} via the key-icon panel.\n"
        f"  4. Save it as plain text to {credentials_file_path()}.\n"
        "Create a token at https://data.tamquant.com/settings/tokens (requires GitHub login)."
    )


def _headers(token: Optional[str]) -> dict:
    return {"Authorization": f"Bearer {resolve_token(token)}"}


def _resolve_api_url(explicit: Optional[str]) -> str:
    return explicit or os.environ.get(_API_URL_ENV_VAR) or _DEFAULT_API_URL


def _raise_for_status(response: requests.Response) -> None:
    """Same as response.raise_for_status(), but surfaces the Worker's own
    `{"error": "..."}` JSON body (see tam-data-explorer's lib/errors.ts)
    in the exception message -- plain raise_for_status() only reports the
    status code/reason phrase, discarding exactly the detail that explains
    *why* (e.g. "failed to mint temporary R2 credentials: ...")."""
    if response.ok:
        return
    try:
        detail = response.json().get("error")
    except Exception:
        detail = None
    message = f"{response.status_code} {response.reason} for {response.url}"
    if detail:
        message += f" -- {detail}"
    raise requests.exceptions.HTTPError(message, response=response)


def fetch_dataframe(
    symbol: str,
    year: int,
    *,
    token: Optional[str] = None,
    api_url: Optional[str] = None,
    timeout: float = 30.0,
) -> pd.DataFrame:
    """Downloads minute/{SYMBOL}/{year}.parquet (tam/marketdata/store.py's
    own partition layout) and returns it as a DataFrame -- one whole
    symbol-year file per call, no server-side filtering/aggregation (use
    connect() for that, or tam.marketdata.duckdb_query.open_duckdb()
    directly if you already have real R2 credentials)."""
    response = requests.get(
        f"{_resolve_api_url(api_url)}/api/token/download",
        params={"key": f"minute/{symbol.upper()}/{year}.parquet"},
        headers=_headers(token),
        timeout=timeout,
    )
    _raise_for_status(response)
    return pd.read_parquet(io.BytesIO(response.content))


def download_csv(
    symbol: str,
    year: int,
    dest_path: "str | Path",
    *,
    token: Optional[str] = None,
    api_url: Optional[str] = None,
    timeout: float = 60.0,
) -> Path:
    """Same file, saved as a CSV directly to `dest_path` -- for scripts that
    want the file on disk without pandas involved at all."""
    response = requests.get(
        f"{_resolve_api_url(api_url)}/api/token/file/csv",
        params={"key": f"minute/{symbol.upper()}/{year}.parquet"},
        headers=_headers(token),
        timeout=timeout,
    )
    _raise_for_status(response)
    dest = Path(dest_path)
    dest.write_bytes(response.content)
    return dest


def _mint_credentials(token: Optional[str], api_url: Optional[str], ttl_seconds: Optional[int], timeout: float) -> dict:
    body = {"ttlSeconds": ttl_seconds} if ttl_seconds else {}
    response = requests.post(
        f"{_resolve_api_url(api_url)}/api/token/credentials",
        json=body,
        headers=_headers(token),
        timeout=timeout,
    )
    _raise_for_status(response)
    return response.json()


def _parse_expires_at(value: str) -> datetime:
    # datetime.fromisoformat() only started accepting a bare "Z" suffix in
    # Python 3.11 -- this repo's own floor is 3.10 (pyproject.toml), so the
    # "Z" -> "+00:00" swap keeps this working on both.
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class SqlConnection:
    """Wraps a real query-engine connection (DuckDB today -- an
    implementation detail, not part of this class's contract) so that every
    call transparently mints a fresh R2 credential and re-points the
    connection at it once the current one is close to expiring. Everything
    other than that refresh check -- `.sql()`, `.execute()`, `.close()`,
    whatever the underlying connection offers -- passes straight through."""

    def __init__(self, token: Optional[str], api_url: Optional[str], ttl_seconds: Optional[int], timeout: float):
        self._token = token
        self._api_url = api_url
        self._ttl_seconds = ttl_seconds
        self._timeout = timeout
        self._expires_at: Optional[datetime] = None
        self._con = self._connect()

    def _connect(self) -> Any:
        import duckdb

        con = duckdb.connect()
        con.sql("INSTALL httpfs; LOAD httpfs;")
        self._apply_credentials(con)
        _register_macros(con)
        return con

    def _apply_credentials(self, con: Any) -> None:
        credentials = _mint_credentials(self._token, self._api_url, self._ttl_seconds, self._timeout)
        con.sql(
            f"""
            SET s3_endpoint = '{credentials["endpoint"].removeprefix("https://")}';
            SET s3_access_key_id = '{credentials["accessKeyId"]}';
            SET s3_secret_access_key = '{credentials["secretAccessKey"]}';
            SET s3_session_token = '{credentials["sessionToken"]}';
            SET s3_region = 'auto';
            SET s3_url_style = 'path';
            SET s3_use_ssl = true;
            """
        )
        con.sql(f"SET VARIABLE minute_root = 's3://{credentials['bucket']}/minute'")
        self._expires_at = _parse_expires_at(credentials["expiresAt"])

    def _ensure_fresh(self) -> None:
        if self._expires_at is None or datetime.now(timezone.utc) >= self._expires_at - _REFRESH_MARGIN:
            self._apply_credentials(self._con)

    def sql(self, *args, **kwargs):
        self._ensure_fresh()
        return self._con.sql(*args, **kwargs)

    def execute(self, *args, **kwargs):
        self._ensure_fresh()
        return self._con.execute(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        # Anything not explicitly overridden above (close(), etc.) passes
        # straight through to the real connection -- this class only needs
        # to intercept the query-running entry points.
        return getattr(self._con, name)


def connect(
    *,
    token: Optional[str] = None,
    api_url: Optional[str] = None,
    ttl_seconds: Optional[int] = None,
    timeout: float = 30.0,
) -> SqlConnection:
    """A SQL connection over the FULL minute-bar lake -- same macros as
    tam.marketdata.duckdb_query.open_duckdb() (daily_bars, weekly_bars,
    rollup_bars, daily_returns, rolling_volatility -- see that module's own
    docstring), authenticated via your personal token instead of raw R2
    account credentials, and self-refreshing (see SqlConnection above) so it
    keeps working across a long notebook session rather than just expiring
    partway through. `ttl_seconds` (default 900, capped at 3600
    server-side) only controls how long each individual minted credential
    lives for -- irrelevant to how long you can keep using the connection
    itself."""
    return SqlConnection(token, api_url, ttl_seconds, timeout)
