"""Best-effort git provenance capture for a published discovery -- commit,
branch, remote URL, and whether the working tree was dirty. Never raises:
publishing must succeed the same way whether or not the caller happens to be
inside a git repo, or has git installed at all.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import List, Optional


def _run(args: List[str], cwd: Optional[Path]) -> str:
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=5, check=True)
    return result.stdout.strip()


def capture_git_info(cwd: Optional[Path] = None) -> dict:
    """{git_commit, git_branch, git_repo, git_dirty} for the git repo
    containing `cwd` (defaults to the current process's own working
    directory -- this is provenance about the code that RAN, not wherever
    the artifact file happens to live). Returns {} if `cwd` isn't inside a
    git repo, or `git` isn't installed at all -- either way, upload() still
    succeeds, just without this metadata."""
    try:
        commit = _run(["git", "rev-parse", "HEAD"], cwd)
        branch = _run(["git", "branch", "--show-current"], cwd)
        dirty = bool(_run(["git", "status", "--porcelain"], cwd))
    except Exception:
        return {}

    try:
        # A repo with no configured remote (or no `origin` specifically)
        # exits nonzero here -- that alone shouldn't discard the
        # commit/branch/dirty info above, so this is its own try/except
        # rather than folded into the block above.
        repo = _run(["git", "config", "--get", "remote.origin.url"], cwd)
    except Exception:
        repo = None

    return {"git_commit": commit, "git_branch": branch, "git_dirty": dirty, "git_repo": repo}
