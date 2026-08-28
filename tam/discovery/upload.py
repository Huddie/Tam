"""upload() -- the Python SDK entry point. See tam.discovery's own package
docstring for the one-line pitch; this module covers the mechanics.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, Union, runtime_checkable

from .auth import resolve_token
from .git_info import capture_git_info
from .http import DiscoveryClient


@runtime_checkable
class Uploadable(Protocol):
    """Anything tam.discovery.upload() can publish directly, alongside a
    plain path to an existing .html file: must produce a self-contained
    HTML string via to_html(full_html=..., include_plotlyjs=...) -- the
    exact signature plotly.graph_objects.Figure already has, and the one
    tam.charting.ChartCall/ChartPipeline both implement by
    delegating to their own rendered Figure.

    A Protocol (structural typing), not an ABC, on purpose -- go.Figure is
    a third-party type we can't make literally inherit from a base class
    of ours; @runtime_checkable lets `isinstance(x, Uploadable)` still work
    against it based on shape alone, same as this module's previous plain
    `hasattr(x, "to_html")` check, just self-documenting and checkable by
    callers/type-checkers instead of implicit.
    """

    def to_html(self, *args: Any, **kwargs: Any) -> str: ...


@dataclass(frozen=True)
class UploadResult:
    url: str
    id: str
    version: int
    title: str
    type: str


def _read_html(path_or_figure: Union[str, Path, Uploadable]) -> str:
    """The artifact's HTML text, however it was given: an existing .html
    file's contents (path_or_figure is path-like), or an Uploadable's own
    to_html() output (a Plotly Figure, a ChartCall/ChartPipeline, or any
    other object satisfying that Protocol). Keeps this module importable
    in an environment without plotly, even though plotly is already a
    tam-quant base dependency, for the common case of publishing an
    already-rendered .html file with no plotly object in scope at all."""
    if isinstance(path_or_figure, Uploadable):
        # CDN mode (not inline): every Discovery viewer already needs
        # internet access to reach the site itself, so there's no offline-
        # viewing requirement -- inlining plotly.js would just add several
        # MB per artifact for zero benefit. Matches the same convention
        # tam.backtest.tearsheet's own multi-chart HTML already uses
        # (include_plotlyjs="cdn"). full_html=True since the artifact IS
        # the entire page Discovery serves, not a fragment embedded in a
        # larger one.
        return path_or_figure.to_html(full_html=True, include_plotlyjs="cdn")

    path = Path(path_or_figure)
    if not path.is_file():
        raise ValueError(
            f"{path} is not a file -- tam.discovery.upload() publishes a single .html "
            "file or anything satisfying the Uploadable protocol (a Plotly Figure, a "
            "ChartCall/ChartPipeline, ...), not a directory (multi-file artifacts aren't "
            "supported yet)"
        )
    return path.read_text(encoding="utf-8")


def upload(
    path_or_figure: Union[str, Path, Uploadable],
    *,
    title: str,
    type: str = "dashboard",
    name: Optional[str] = None,
    description: Optional[str] = None,
    tags: Optional[List[str]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    source_file: Optional[str] = None,
    token: Optional[str] = None,
    api_url: Optional[str] = None,
    capture_git: bool = True,
    timeout: float = 30.0,
) -> UploadResult:
    """Publishes `path_or_figure` (a path to an existing .html file, or a
    Plotly Figure -- serialized to a self-contained HTML string first) to
    Discovery. `name`, if given, assigns/reuses a stable slug whose URL
    always resolves to whichever version is latest; the version's OWN URL
    (returned as `.url` here) never changes regardless. `type` groups this
    discovery with others of the same kind in the catalog (free-form,
    default "dashboard" -- not a fixed enum, matches how tags work). `tags`
    and `metadata` are stored verbatim (tags get server-side normalized and
    deduped, e.g. "After Hours"/"after-hours" collapse to one tag).

    Git provenance (commit/branch/repo/dirty-tree flag) is captured
    automatically from the CURRENT process's working directory unless
    `capture_git=False` -- best-effort, never raises if not in a git repo
    or `git` isn't installed.

    Re-publishing byte-identical content is cheap, not just correct: the
    server hashes the artifact (sha256, computed here and sent as
    `content_hash`) and short-circuits before any upload happens if it's
    already seen those exact bytes -- either as an existing version of THIS
    SAME discovery (a pure no-op: returns the existing version, uploads
    nothing, creates nothing) or as some other version's artifact entirely
    (a genuinely new version row for this discovery, but pointed at the
    already-existing R2 object instead of re-uploading it). Either way,
    calling upload() again with unchanged content never re-uploads the same
    bytes twice.

    Raises RuntimeError (from tam.discovery.auth.resolve_token) if no
    publishing token can be found, or (from tam.discovery.http.resolve_api_url)
    if no API URL is configured -- see each for the exact resolution order."""
    html = _read_html(path_or_figure)
    content_bytes = html.encode("utf-8")
    content_hash = hashlib.sha256(content_bytes).hexdigest()

    resolved_token = resolve_token(token)
    client = DiscoveryClient(resolved_token, api_url=api_url, timeout=timeout)

    discovery = client.create_discovery(title=title, type=type, name=name)
    discovery_id = discovery["discovery_id"]

    version_fields: Dict[str, Any] = {
        "title": title,
        "description": description,
        "tags": tags or [],
        "metadata": metadata or {},
        "source_file": source_file,
        "content_hash": content_hash,
    }
    if capture_git:
        version_fields.update(capture_git_info())

    created = client.create_version(discovery_id, **version_fields)

    # already_exists means the server fully handled this (either a no-op
    # re-publish, or an immediate finalize against an already-existing R2
    # object) -- created IS the finished result, nothing left to upload.
    if created.get("already_exists"):
        return UploadResult(
            url=created["url"],
            id=created["version_id"],
            version=created["version"],
            title=created["title"],
            type=discovery.get("type", type),
        )

    version_id = created["version_id"]
    client.upload_artifact(created["upload_url"], created.get("upload_headers", {}), content_bytes)
    finalized = client.finalize_version(discovery_id, version_id, size_bytes=len(content_bytes))

    return UploadResult(
        url=finalized["url"],
        id=finalized["id"],
        version=finalized["version"],
        title=finalized["title"],
        type=discovery.get("type", type),
    )
