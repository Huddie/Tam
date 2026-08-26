"""upload() -- the Python SDK entry point. See tam.discovery's own package
docstring for the one-line pitch; this module covers the mechanics.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from .auth import resolve_token
from .git_info import capture_git_info
from .http import DiscoveryClient


@dataclass(frozen=True)
class UploadResult:
    url: str
    id: str
    version: int
    title: str
    type: str


def _read_html(path_or_figure: Any) -> str:
    """The artifact's HTML text, however it was given: an existing .html
    file's contents (path_or_figure is path-like), or a Plotly Figure's own
    to_html() output. Figure detection is duck-typed (hasattr(...,
    "to_html")) rather than a hard `import plotly.graph_objects` check --
    keeps this module importable in an environment without plotly, even
    though plotly is already a tam-quant base dependency, for the common
    case of publishing an already-rendered .html file with no plotly
    object in scope at all."""
    if hasattr(path_or_figure, "to_html"):
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
            "file or a Plotly Figure, not a directory (multi-file artifacts aren't "
            "supported yet)"
        )
    return path.read_text(encoding="utf-8")


def upload(
    path_or_figure: Union[str, Path, Any],
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

    Raises RuntimeError (from tam.discovery.auth.resolve_token) if no
    publishing token can be found, or (from tam.discovery.http.resolve_api_url)
    if no API URL is configured -- see each for the exact resolution order."""
    html = _read_html(path_or_figure)
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
    }
    if capture_git:
        version_fields.update(capture_git_info())

    created = client.create_version(discovery_id, **version_fields)
    version_id = created["version_id"]

    content_bytes = html.encode("utf-8")
    client.upload_artifact(created["upload_url"], created.get("upload_headers", {}), content_bytes)

    finalized = client.finalize_version(discovery_id, version_id, size_bytes=len(content_bytes))

    return UploadResult(
        url=finalized["url"],
        id=finalized["id"],
        version=finalized["version"],
        title=finalized["title"],
        type=discovery.get("type", type),
    )
