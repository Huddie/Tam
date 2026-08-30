"""Thin HTTP client for talking to the Discovery Worker API -- resolves the
base URL and attaches the bearer token, so upload.py/cli.py don't each
repeat that. Not a general-purpose API client; only the handful of calls
tam.discovery actually needs (see /tam-discovery's own route table for the
full API this is a client for).
"""

from __future__ import annotations

import os
import time

import requests

_ENV_VAR = "TAM_DISCOVERY_API_URL"
_DEFAULT_API_URL = "https://discovery.tamquant.com"


def resolve_api_url(explicit: str | None = None) -> str:
    """explicit `api_url=`/`--api-url` wins, then the TAM_DISCOVERY_API_URL
    env var, then the real production Discovery site -- overridable for
    anyone self-hosting their own separate Discovery instance, but nothing
    to configure for the common case of publishing to this one."""
    return explicit or os.environ.get(_ENV_VAR) or _DEFAULT_API_URL


def _with_retries(func, attempts: int = 3, base_delay: float = 1.0):
    """Retries `func()` on a transient failure -- a dropped connection/
    timeout, or a 5xx (server-side, likely transient) response -- with
    exponential backoff. Does NOT retry a 4xx (bad request, expired/
    revoked token, not found, ...): that's a real, non-transient problem
    a retry would just repeat, not fix, and silently masking e.g. a 401
    behind 3 retries would only make a real auth failure slower to
    surface. Re-raises the last exception once every attempt is
    exhausted."""
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            return func()
        except requests.exceptions.HTTPError as exc:
            if exc.response is not None and exc.response.status_code < 500:
                raise
            last_exc = exc
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
            last_exc = exc
        if attempt < attempts - 1:
            time.sleep(base_delay * (2**attempt))
    assert last_exc is not None
    raise last_exc


class DiscoveryClient:
    """A requests.Session pre-configured with the bearer token and base
    URL, plus the specific endpoints tam.discovery calls."""

    def __init__(self, token: str, api_url: str | None = None, timeout: float = 30.0):
        self._base_url = resolve_api_url(api_url).rstrip("/")
        self._timeout = timeout
        self._session = requests.Session()
        self._session.headers["Authorization"] = f"Bearer {token}"

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        def _do() -> requests.Response:
            response = self._session.request(method, f"{self._base_url}{path}", timeout=self._timeout, **kwargs)
            response.raise_for_status()
            return response

        return _with_retries(_do)

    def whoami(self) -> dict:
        return self._request("GET", "/api/publish/whoami").json()

    def create_discovery(self, *, title: str, type: str, name: str | None) -> dict:
        body = {"title": title, "type": type}
        if name:
            body["name"] = name
        return self._request("POST", "/api/publish/discoveries", json=body).json()

    def create_version(self, discovery_id: str, **fields) -> dict:
        return self._request("POST", f"/api/publish/discoveries/{discovery_id}/versions", json=fields).json()

    def finalize_version(self, discovery_id: str, version_id: str, *, size_bytes: int) -> dict:
        return self._request(
            "POST",
            f"/api/publish/discoveries/{discovery_id}/versions/{version_id}/finalize",
            json={"size_bytes": size_bytes},
        ).json()

    def list_discoveries(self, **params) -> dict:
        return self._request("GET", "/api/publish/discoveries", params=params).json()

    def get_discovery(self, slug_or_id: str) -> dict:
        return self._request("GET", f"/api/publish/discoveries/{slug_or_id}").json()

    def get_versions(self, slug_or_id: str) -> dict:
        return self._request("GET", f"/api/publish/discoveries/{slug_or_id}/versions").json()

    def upload_artifact(self, upload_url: str, upload_headers: dict, content: bytes) -> None:
        """PUTs directly to the presigned R2 URL the Worker handed back --
        NOT through this client's own authenticated session (the presigned
        URL carries its own signature-based auth; sending a bearer token
        here would be meaningless and the URL is typically a different
        host entirely)."""

        def _do() -> requests.Response:
            response = requests.put(upload_url, data=content, headers=upload_headers or {}, timeout=self._timeout)
            response.raise_for_status()
            return response

        _with_retries(_do)
