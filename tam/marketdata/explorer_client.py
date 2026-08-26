"""Python client for tam-data-explorer (data.tamquant.com) -- fetches
minute-bar Parquet files over that Worker's HTTP API, rather than R2's
S3-compatible surface directly. tam.marketdata.duckdb_query.open_duckdb()
remains the right tool for ad hoc SQL over the whole lake; this is for
"just get me one symbol-year as a DataFrame" from anywhere requests already
works, with no DuckDB/pyarrow-S3 setup at all.

Authenticates the same way curl or any other script would: a Cloudflare
Access Service Token (a Client ID + Client Secret pair, NOT a human GitHub
login) -- see https://data.tamquant.com/api-access for exactly how to
create one. Resolution order for the token (and the API base URL) mirrors
tam.discovery.auth.resolve_token(): explicit kwarg -> env var -> Colab
secret -> saved file.

    from tam.marketdata.explorer_client import fetch_dataframe

    df = fetch_dataframe("AAPL", 2024)
"""
from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path
from typing import Optional, Tuple

import pandas as pd
import requests

_ENV_VARS = {
    "service_token_id": "DATA_EXPLORER_SERVICE_TOKEN_ID",
    "service_token_secret": "DATA_EXPLORER_SERVICE_TOKEN_SECRET",
}
_API_URL_ENV_VAR = "DATA_EXPLORER_API_URL"
_DEFAULT_API_URL = "https://data.tamquant.com"


def credentials_file_path() -> Path:
    """~/.config/tam-data-explorer/credentials.json -- same ~/.config/<tool>/
    convention as tam.discovery.auth.token_file_path()/
    tam.marketdata.credentials.credentials_file_path(), holding
    {"service_token_id": ..., "service_token_secret": ...}. Nothing writes
    this file automatically (no `login` command for this client) -- create
    it by hand if you want the saved-file fallback."""
    return Path.home() / ".config" / "tam-data-explorer" / "credentials.json"


def _from_colab(env_var: str) -> Optional[str]:
    """Same broad-except pattern as tam.discovery.auth._from_colab /
    tam.marketdata.credentials._from_colab -- only attempted when actually
    running in Colab, and any failure (no such secret, no notebook access
    granted yet) just falls through to the next resolution source."""
    if "google.colab" not in sys.modules:
        return None
    try:
        from google.colab import userdata  # type: ignore
    except ImportError:
        return None
    try:
        value = userdata.get(env_var)
    except Exception:
        return None
    return value or None


def _from_file(field: str) -> Optional[str]:
    try:
        data = json.loads(credentials_file_path().read_text())
    except (OSError, json.JSONDecodeError):
        return None
    value = data.get(field)
    return value or None


def _resolve_field(field: str, explicit: Optional[str]) -> Optional[str]:
    if explicit:
        return explicit
    env_var = _ENV_VARS[field]
    return os.environ.get(env_var) or _from_colab(env_var) or _from_file(field)


def resolve_service_token(token_id: Optional[str] = None, token_secret: Optional[str] = None) -> Tuple[str, str]:
    """Resolution order for BOTH fields, checked independently (one could
    come from an env var and the other from the saved file): explicit
    kwarg -> env var -> Colab secret -> saved file. Raises a clear,
    actionable RuntimeError if either is still missing."""
    resolved_id = _resolve_field("service_token_id", token_id)
    resolved_secret = _resolve_field("service_token_secret", token_secret)
    if not resolved_id or not resolved_secret:
        raise RuntimeError(
            "No Data Explorer service token found. Pick one:\n"
            "  1. Pass token_id=.../token_secret=... directly.\n"
            f"  2. Set {_ENV_VARS['service_token_id']}/{_ENV_VARS['service_token_secret']} environment variables.\n"
            "  3. In Colab: add secrets under those exact names via the key-icon panel.\n"
            f"  4. Save both fields as JSON to {credentials_file_path()}.\n"
            "Create a service token from the Cloudflare Zero Trust dashboard -- see "
            "https://data.tamquant.com/api-access for exact steps."
        )
    return resolved_id, resolved_secret


def _headers(token_id: Optional[str], token_secret: Optional[str]) -> dict:
    resolved_id, resolved_secret = resolve_service_token(token_id, token_secret)
    # These are the exact headers Cloudflare Access looks for to authenticate
    # a Service Token -- distinct from the Cf-Access-Jwt-Assertion cookie/
    # header a browser session carries after an interactive GitHub login.
    return {"CF-Access-Client-Id": resolved_id, "CF-Access-Client-Secret": resolved_secret}


def _resolve_api_url(explicit: Optional[str]) -> str:
    return explicit or os.environ.get(_API_URL_ENV_VAR) or _DEFAULT_API_URL


def fetch_dataframe(
    symbol: str,
    year: int,
    *,
    token_id: Optional[str] = None,
    token_secret: Optional[str] = None,
    api_url: Optional[str] = None,
    timeout: float = 30.0,
) -> pd.DataFrame:
    """Downloads minute/{SYMBOL}/{year}.parquet (tam/marketdata/store.py's
    own partition layout) and returns it as a DataFrame -- one whole
    symbol-year file per call, no server-side filtering/aggregation (use
    tam.marketdata.duckdb_query.open_duckdb() directly for that)."""
    response = requests.get(
        f"{_resolve_api_url(api_url)}/api/download",
        params={"key": f"minute/{symbol.upper()}/{year}.parquet"},
        headers=_headers(token_id, token_secret),
        timeout=timeout,
    )
    response.raise_for_status()
    return pd.read_parquet(io.BytesIO(response.content))


def download_csv(
    symbol: str,
    year: int,
    dest_path: "str | Path",
    *,
    token_id: Optional[str] = None,
    token_secret: Optional[str] = None,
    api_url: Optional[str] = None,
    timeout: float = 60.0,
) -> Path:
    """Same file, saved as a CSV directly to `dest_path` -- for scripts that
    want the file on disk without pandas involved at all."""
    response = requests.get(
        f"{_resolve_api_url(api_url)}/api/file/csv",
        params={"key": f"minute/{symbol.upper()}/{year}.parquet"},
        headers=_headers(token_id, token_secret),
        timeout=timeout,
    )
    response.raise_for_status()
    dest = Path(dest_path)
    dest.write_bytes(response.content)
    return dest
