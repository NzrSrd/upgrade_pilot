"""Local-path resolver: use a checkout in place, read-only."""

import subprocess
from collections.abc import Sequence
from pathlib import Path

from upgradepilot.models.errors import RepoUnavailableError
from upgradepilot.services.repo.guards import resolve_local_path
from upgradepilot.services.repo.workspace import Workspace

_GIT_TIMEOUT_SECONDS = 15


def read_commit_sha(root: Path) -> str | None:
    """Current HEAD sha, or None when there is no sha to report.

    None covers two distinct, legitimate cases -- for the same reason as
    `Workspace.git_log`, this process does not try to tell them apart by
    parsing localized git stderr text:

    - `root` is not a git repository at all (no `.git` directory).
    - `root` is a git repository with no commits yet, so `git rev-parse
      HEAD` exits non-zero.

    A hung git process is different from both and is not swallowed: it
    raises `RepoUnavailableError` rather than masquerading as "no sha".
    """
    if not (root / ".git").exists():
        return None
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RepoUnavailableError(
            "Reading the repository's current commit took too long and was aborted.",
            detail=f"git rev-parse HEAD timed out after {_GIT_TIMEOUT_SECONDS}s root={root}",
        ) from exc
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


def open_local_repository(path: str, *, allowed_roots: Sequence[Path]) -> Workspace:
    """Open a local checkout as a Workspace. Never deletes the directory."""
    root = resolve_local_path(path, allowed_roots)
    return Workspace(root=root, commit_sha=read_commit_sha(root), cleanup_dir=None)
