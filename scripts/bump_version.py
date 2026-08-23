"""Computes the next patch version for a PyPI package by querying the index
for whatever's currently published, and writes it into pyproject.toml.

Not published (404 from PyPI) -> falls back to pyproject.toml's own current
version untouched, so the first-ever publish just uses whatever's already
there instead of crashing on a package that doesn't exist yet.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import requests

PACKAGE_NAME = "tam-quant"
PYPROJECT_PATH = Path(__file__).resolve().parent.parent / "pyproject.toml"
_VERSION_RE = re.compile(r'^version\s*=\s*"([^"]+)"', re.MULTILINE)


def _current_pyproject_version() -> str:
    match = _VERSION_RE.search(PYPROJECT_PATH.read_text())
    if not match:
        raise ValueError(f"couldn't find a version = \"...\" line in {PYPROJECT_PATH}")
    return match.group(1)


def _latest_published_version() -> str | None:
    response = requests.get(f"https://pypi.org/pypi/{PACKAGE_NAME}/json", timeout=10)
    if response.status_code == 404:
        return None  # never published -- not an error
    response.raise_for_status()
    return response.json()["info"]["version"]


def _next_patch(version: str) -> str:
    parts = version.split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        raise ValueError(f"expected a strict major.minor.patch version, got {version!r}")
    major, minor, patch = parts
    return f"{major}.{minor}.{int(patch) + 1}"


def _write_version(new_version: str) -> None:
    text = PYPROJECT_PATH.read_text()
    updated, count = _VERSION_RE.subn(f'version = "{new_version}"', text, count=1)
    if count != 1:
        raise ValueError(f"expected exactly one version = \"...\" line in {PYPROJECT_PATH}, replaced {count}")
    PYPROJECT_PATH.write_text(updated)


def main() -> None:
    published = _latest_published_version()
    if published is None:
        new_version = _current_pyproject_version()
        print(f"{PACKAGE_NAME} not yet on PyPI -- using pyproject.toml's current version {new_version}", file=sys.stderr)
    else:
        new_version = _next_patch(published)
        print(f"latest published: {published} -> bumping to {new_version}", file=sys.stderr)

    _write_version(new_version)
    print(new_version)  # stdout: the one line Make captures


if __name__ == "__main__":
    main()
