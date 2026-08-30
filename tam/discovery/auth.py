"""Publishing-token resolution -- shared by tam.discovery.upload() and the
upload-discovery CLI, so both look in exactly the same places in exactly the
same order. Never prints or logs the token itself.

The env var/Colab secret is named TAM_PAT ("personal access token"), not
something Discovery-specific -- the same token also authenticates against
Data Explorer (tam.marketdata.explorer_client uses the identical name for
exactly this reason), so naming it after just one of the two sites it
works on would be misleading.
"""

from __future__ import annotations

from pathlib import Path

from ..secrets import Secrets, resolve_chain

_ENV_VAR = "TAM_PAT"
_COLAB_SECRET_NAME = "TAM_PAT"


def token_file_path() -> Path:
    """~/.config/upload-discovery/token -- deliberately the same path on
    every platform (not a platform-specific config dir) for simplicity,
    matching e.g. ~/.aws/credentials-style tools. What `upload-discovery
    login` writes, and what resolve_token()'s file fallback reads."""
    return Path.home() / ".config" / "upload-discovery" / "token"


def _from_file() -> str | None:
    try:
        text = token_file_path().read_text().strip()
    except OSError:
        return None
    return text or None


def resolve_token(explicit: str | None = None, *, required: bool = True) -> str | None:
    """Resolution order: an explicit `token=`/`--token` argument, then
    TAM_PAT via tam.Secrets (env var, directly or via a .env file, then a
    Colab secret if running in Colab), then whatever `upload-discovery
    login` last saved to disk. `required=True` (the default) raises a
    clear, actionable RuntimeError listing every option if none of them
    produced anything -- publishing should never fail with a bare "no
    token" and no indication of how to fix it. Pass `required=False` to
    get None back instead, e.g. to try this chain first and fall back to
    something else entirely if it comes up empty."""
    value = resolve_chain(
        lambda: explicit,
        lambda: Secrets.get(_ENV_VAR),
        _from_file,
    )
    if value is None and required:
        raise RuntimeError(
            "No Discovery publishing token found. Pick one:\n"
            "  1. Pass token=... directly (or --token to the CLI).\n"
            f"  2. Set the {_ENV_VAR} environment variable (directly, or via a .env file).\n"
            f"  3. In Colab: add a secret named {_COLAB_SECRET_NAME} via the key-icon panel "
            "in the left sidebar, and grant this notebook access to it.\n"
            "  4. Run `upload-discovery login` (get a token first from your Discovery "
            "site's /settings/tokens page)."
        )
    return value
