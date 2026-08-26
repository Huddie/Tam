"""R2 credential resolution -- mirrors the layered resolution order
tam.discovery.auth.resolve_token() already established for Discovery's own
publishing token (explicit kwarg -> env var -> Colab secret -> saved config
file), so anyone who's already used that knows exactly where to look for
these too. Kept independent of tam.discovery.auth itself (that module
resolves ONE bearer token for a Cloudflare-Worker-mediated API; this
resolves several raw S3-style credential fields for direct R2 access -- a
different enough shape that sharing code across them would mean more
indirection than it saves) rather than importing from it.

R2 access needs the full layered treatment (down to a saved file and a Colab
secret) because it's read from a research notebook, not just a local/CI
ingestion job -- unlike a provider API key (see tam.data.providers.FMPProvider
for the simpler existing convention: an env var with a constructor override,
nothing more), which is only ever used from wherever the backfill itself
runs.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import dotenv_values, find_dotenv

_FIELD_ENV_VARS = {
    "account_id": "R2_ACCOUNT_ID",
    "access_key_id": "R2_ACCESS_KEY_ID",
    "secret_access_key": "R2_SECRET_ACCESS_KEY",
    "bucket": "R2_BUCKET",
}


def credentials_file_path() -> Path:
    """~/.config/tam-marketdata/r2_credentials.json -- same ~/.config/<tool>/
    convention as tam.discovery.auth.token_file_path(), just one JSON file
    holding several fields instead of a single plain-text token."""
    return Path.home() / ".config" / "tam-marketdata" / "r2_credentials.json"


def _from_colab(env_var: str) -> Optional[str]:
    """Colab's key-icon secret panel, checked under the SAME name as the env
    var (e.g. a secret literally named "R2_ACCESS_KEY_ID") -- only attempted
    when actually running in Colab. See tam.discovery.auth._from_colab for
    the identical pattern and why failures here are swallowed broadly rather
    than importing Colab's own specific exception types."""
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
        payload = json.loads(credentials_file_path().read_text())
    except (OSError, json.JSONDecodeError):
        return None
    value = payload.get(field)
    return value or None


def _from_dotenv(env_var: str) -> Optional[str]:
    """python-dotenv's own find_dotenv() walks up from the current working
    directory looking for a .env file (so this works whether a script runs
    from the repo root or one of its subdirectories); dotenv_values() just
    parses it into a dict without touching os.environ, since loading the
    WHOLE file into the process environment is a bigger behavior change
    than "find this one credential" calls for. Same pattern as
    tam.discovery.auth._from_dotenv / tam.marketdata.explorer_client's own
    copy -- kept as its own copy here too rather than shared, per this
    module's own docstring on staying independent of tam.discovery.auth."""
    path = find_dotenv(usecwd=True)
    if not path:
        return None
    return dotenv_values(path).get(env_var) or None


def _resolve_field(field: str, explicit: Optional[str]) -> Optional[str]:
    if explicit:
        return explicit
    env_var = _FIELD_ENV_VARS[field]
    env_value = os.environ.get(env_var)
    if env_value:
        return env_value
    dotenv_value = _from_dotenv(env_var)
    if dotenv_value:
        return dotenv_value
    colab_value = _from_colab(env_var)
    if colab_value:
        return colab_value
    return _from_file(field)


@dataclass(frozen=True)
class R2Credentials:
    account_id: str
    access_key_id: str
    secret_access_key: str
    bucket: str

    @property
    def endpoint(self) -> str:
        """R2's S3-compatible endpoint for this account -- the same URL both
        pyarrow.fs.S3FileSystem and DuckDB's httpfs extension need (see
        tam.marketdata.filesystem)."""
        return f"https://{self.account_id}.r2.cloudflarestorage.com"


def resolve_r2_credentials(
    *,
    account_id: Optional[str] = None,
    access_key_id: Optional[str] = None,
    secret_access_key: Optional[str] = None,
    bucket: Optional[str] = None,
) -> R2Credentials:
    """Resolution order per field: the matching keyword argument here, then
    its R2_* env var (directly, or via a .env file found by walking up from
    the current directory), then (if running in Colab) that same env var's
    name as a Colab secret, then whatever save_r2_credentials() last wrote
    to ~/.config/tam-marketdata/r2_credentials.json. Raises one clear,
    actionable RuntimeError naming every field still missing after all
    sources, rather than a bare KeyError on the first one it happens to hit."""
    explicit = {"account_id": account_id, "access_key_id": access_key_id, "secret_access_key": secret_access_key, "bucket": bucket}
    resolved = {field: _resolve_field(field, explicit[field]) for field in _FIELD_ENV_VARS}

    missing = [field for field, value in resolved.items() if not value]
    if missing:
        env_hints = "\n".join(f"  - {_FIELD_ENV_VARS[field]}" for field in missing)
        raise RuntimeError(
            f"Missing R2 credential field(s): {', '.join(missing)}. Pick one:\n"
            "  1. Pass them directly as keyword arguments.\n"
            f"  2. Set the corresponding environment variable(s) (directly, or via a .env file):\n{env_hints}\n"
            "  3. In Colab: add secrets with those exact names via the key-icon panel "
            "in the left sidebar, and grant this notebook access to them.\n"
            "  4. Call save_r2_credentials(...) once to save them to "
            f"{credentials_file_path()}."
        )
    return R2Credentials(**resolved)


def save_r2_credentials(credentials: R2Credentials) -> Path:
    """Writes `credentials` to ~/.config/tam-marketdata/r2_credentials.json
    (permissions tightened to owner-read/write where supported) -- the same
    kind of one-time local save `upload-discovery login` does for its own
    token, so a credential set can live outside any .env file/shell profile
    and still be picked up by every future resolve_r2_credentials() call."""
    path = credentials_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "account_id": credentials.account_id,
                "access_key_id": credentials.access_key_id,
                "secret_access_key": credentials.secret_access_key,
                "bucket": credentials.bucket,
            },
            indent=2,
        )
    )
    try:
        path.chmod(0o600)
    except OSError:
        # Non-POSIX filesystems may not support chmod -- the file is still
        # saved, just without the permission tightening.
        pass
    return path
