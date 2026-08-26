"""Thin HTTP client for talking to the Discovery Worker API -- resolves the
base URL and attaches the bearer token, so upload.py/cli.py don't each
repeat that. Not a general-purpose API client; only the handful of calls
tam.discovery actually needs (see /tam-discovery's own route table for the
full API this is a client for).
"""
from __future__ import annotations

import os
from typing import Optional

import requests

_ENV_VAR = "TAM_DISCOVERY_API_URL"


def resolve_api_url(explicit: Optional[str] = None) -> str:
    """explicit `api_url=`/`--api-url` wins, then the TAM_DISCOVERY_API_URL
    env var. No hardcoded production default here -- unlike the token,
    there's no safe placeholder to fall back to silently; a caller with
    neither gets a clear error instead of silently talking to a fictional
    endpoint."""
    resolved = explicit or os.environ.get(_ENV_VAR)
    if not resolved:
        raise RuntimeError(
            f"No Discovery API URL configured -- pass api_url=... (or --api-url to the "
            f"CLI), or set the {_ENV_VAR} environment variable to your Discovery site's "
            "own URL (e.g. https://discovery.example.com)."
        )
    return resolved


class DiscoveryClient:
    """A requests.Session pre-configured with the bearer token and base
    URL, plus the specific endpoints tam.discovery calls."""

    def __init__(self, token: str, api_url: Optional[str] = None, timeout: float = 30.0):
        self._base_url = resolve_api_url(api_url).rstrip("/")
        self._timeout = timeout
        self._session = requests.Session()
        self._session.headers["Authorization"] = f"Bearer {token}"

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        response = self._session.request(method, f"{self._base_url}{path}", timeout=self._timeout, **kwargs)
        response.raise_for_status()
        return response

    def whoami(self) -> dict:
        return self._request("GET", "/api/publish/whoami").json()

    def create_discovery(self, *, title: str, type: str, name: Optional[str]) -> dict:
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
        response = requests.put(upload_url, data=content, headers=upload_headers or {}, timeout=self._timeout)
        response.raise_for_status()
