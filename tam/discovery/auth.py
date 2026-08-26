"""Publishing-token resolution -- shared by tam.discovery.upload() and the
upload-discovery CLI, so both look in exactly the same places in exactly the
same order. Never prints or logs the token itself.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

_ENV_VAR = "TAM_DISCOVERY_TOKEN"
_COLAB_SECRET_NAME = "TAM_DISCOVERY_TOKEN"


def token_file_path() -> Path:
    """~/.config/upload-discovery/token -- deliberately the same path on
    every platform (not a platform-specific config dir) for simplicity,
    matching e.g. ~/.aws/credentials-style tools. What `upload-discovery
    login` writes, and what resolve_token()'s file fallback reads."""
    return Path.home() / ".config" / "upload-discovery" / "token"


def _from_colab() -> Optional[str]:
    """Colab's own recommended secret-storage mechanism (the key-icon panel
    in the notebook's left sidebar) -- persists across sessions without the
    token ever appearing in a saved cell output, unlike pasting it directly
    into a cell. Only attempted when actually running in Colab."""
    if "google.colab" not in sys.modules:
        return None
    try:
        from google.colab import userdata  # type: ignore
    except ImportError:
        return None
    try:
        value = userdata.get(_COLAB_SECRET_NAME)
    except Exception:
        # google.colab.userdata raises its own SecretNotFoundError (no such
        # secret) / NotebookAccessError (secret exists but this notebook
        # hasn't been granted access yet) for exactly this "not configured
        # this way" case -- caught broadly rather than importing those
        # specific names, since colab's own API surface here isn't this
        # package's to depend on tightly. Either way: fall through to the
        # next resolution source, don't raise.
        return None
    return value or None


def _from_file() -> Optional[str]:
    try:
        text = token_file_path().read_text().strip()
    except OSError:
        return None
    return text or None


def resolve_token(explicit: Optional[str] = None) -> str:
    """Resolution order: an explicit `token=`/`--token` argument, then the
    TAM_DISCOVERY_TOKEN env var, then (if running in Colab) that same name
    as a Colab secret, then whatever `upload-discovery login` last saved to
    disk. Raises a clear, actionable RuntimeError listing every option if
    none of them produced anything -- publishing should never fail with a
    bare "no token" and no indication of how to fix it."""
    if explicit:
        return explicit

    env_value = os.environ.get(_ENV_VAR)
    if env_value:
        return env_value

    colab_value = _from_colab()
    if colab_value:
        return colab_value

    file_value = _from_file()
    if file_value:
        return file_value

    raise RuntimeError(
        "No Discovery publishing token found. Pick one:\n"
        "  1. Pass token=... directly (or --token to the CLI).\n"
        f"  2. Set the {_ENV_VAR} environment variable.\n"
        f"  3. In Colab: add a secret named {_COLAB_SECRET_NAME} via the key-icon panel "
        "in the left sidebar, and grant this notebook access to it.\n"
        "  4. Run `upload-discovery login` (get a token first from your Discovery "
        "site's /settings/tokens page)."
    )
