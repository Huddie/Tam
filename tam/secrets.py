"""Secrets: a generic named-secret resolver for anything that doesn't
already have its own dedicated resolution chain (see tam.discovery.auth.
resolve_token() / tam.marketdata.credentials.resolve_r2_credentials() for
existing dedicated ones, e.g. the Discovery/Data-Explorer personal token or
R2 credentials) -- this is for everything else: a notebook's own
FRED_API_KEY, or any other one-off third-party API key you bring yourself.

Resolution order per name: env var (directly, or via a .env file found by
walking up from the current directory) -> (if running in Colab) that same
name as a Colab secret. Deliberately simpler than resolve_token's/
resolve_r2_credentials's fuller chains (no saved-file fallback) -- those
extra sources exist for credentials THIS PACKAGE itself issues and manages
end-to-end (a publishing token you get via `upload-discovery login`, R2
access via `save_r2_credentials()`); Secrets is for arbitrary third-party
keys with no such flow, where "env var, or a Colab secret" already covers
every place you'd realistically run a notebook::

    from tam import Secrets
    from fredapi import Fred

    fred = Fred(api_key=Secrets["FRED_API_KEY"])          # raises if missing
    key = Secrets.get("FRED_API_KEY")                     # None if missing, never raises
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable

from dotenv import dotenv_values, find_dotenv


def _from_dotenv(name: str) -> str | None:
    """python-dotenv's own find_dotenv() walks up from the current working
    directory looking for a .env file; dotenv_values() just parses it into a
    dict without touching os.environ. Same pattern as tam.discovery.auth's
    own copy of this -- kept independent rather than shared, per this
    codebase's "small independent pieces" convention."""
    path = find_dotenv(usecwd=True)
    if not path:
        return None
    return dotenv_values(path).get(name) or None


def _from_colab(name: str) -> str | None:
    """Colab's own recommended secret-storage mechanism (the key-icon panel
    in the notebook's left sidebar) -- only attempted when actually running
    in Colab. Same pattern as tam.discovery.auth._from_colab, parameterized
    over `name` since this resolves ARBITRARY secret names, not one fixed
    one."""
    if "google.colab" not in sys.modules:
        return None
    try:
        from google.colab import userdata  # type: ignore
    except ImportError:
        return None
    try:
        value = userdata.get(name)
    except Exception:
        # google.colab.userdata raises its own SecretNotFoundError/
        # NotebookAccessError for exactly this "not configured this way"
        # case -- caught broadly rather than importing those specific
        # names, since Colab's own API surface isn't this package's to
        # depend on tightly. Either way: fall through, don't raise.
        return None
    return value or None


class _Secrets:
    """Singleton accessor -- `Secrets["NAME"]` (raises a clear error if
    unresolvable) or `Secrets.get("NAME", default=None)` (never raises)."""

    def __getitem__(self, name: str) -> str:
        value = self.get(name)
        if value is None:
            raise KeyError(
                f"{name!r} not set. Pick one:\n"
                f"  1. Set the {name} environment variable (directly, or via a .env file).\n"
                f"  2. In Colab: add a secret named {name!r} via the key-icon panel in the "
                "left sidebar, and grant this notebook access to it."
            )
        return value

    def get(self, name: str, default: str | None = None) -> str | None:
        env_value = os.environ.get(name)
        if env_value:
            return env_value
        dotenv_value = _from_dotenv(name)
        if dotenv_value:
            return dotenv_value
        colab_value = _from_colab(name)
        if colab_value:
            return colab_value
        return default


Secrets = _Secrets()


def resolve_chain(*sources: Callable[[], str | None]) -> str | None:
    """Chain-of-responsibility primitive: try each zero-arg callable in
    `sources`, in order, returning the first one that comes back with
    something other than None. This is the shared shape behind every
    credential/config resolver in this codebase -- `Secrets.get()` above
    is one fixed instance of it (env var -> .env -> Colab secret);
    tam.marketdata.credentials.resolve_r2_credentials() and
    tam.marketdata.explorer_client.resolve_token() build their own longer
    chains (explicit kwarg -> ... -> a saved config file) out of this same
    primitive instead of each hand-rolling their own if/elif ladder.
    Individual sources (env var lookup, Colab secret lookup, a saved-file
    read, ...) stay as small independent functions per module -- only the
    "try these in order" glue is shared here."""
    for source in sources:
        value = source()
        if value is not None:
            return value
    return None
