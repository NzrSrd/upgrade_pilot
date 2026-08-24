"""Local-path resolver: use a checkout in place, read-only."""

from collections.abc import Sequence
from pathlib import Path

from upgradepilot.services.repo.guards import resolve_local_path
from upgradepilot.services.repo.workspace import Workspace, probe_head_sha

_GIT_TIMEOUT_SECONDS = 15


def read_commit_sha(root: Path) -> str | None:
    """Current HEAD sha, or None when there is no sha to report.

    None covers two distinct, legitimate cases:

    - `root` is not a git repository at all (no `.git` directory). No
      subprocess is spawned for this case.
    - `root` is a git repository with no commits yet.

    Both are told apart from "the repository is unusable" (corrupted,
    permission denied, an incompatible git) by `probe_head_sha`'s
    documented git exit-code contract, not by parsing stderr text. A
    hung git process is different from all of the above and is not
    swallowed either: it raises `RepoUnavailableError`.
    """
    if not (root / ".git").exists():
        return None
    return probe_head_sha(root, timeout=_GIT_TIMEOUT_SECONDS)


def open_local_repository(path: str, *, allowed_roots: Sequence[Path]) -> Workspace:
    """Open a local checkout as a Workspace. Never deletes the directory.

    `cleanup_dir=None` is load-bearing, not incidental: it is what makes
    `Workspace.cleanup()` a no-op here, which is in turn what makes
    `WorkspaceManager.open` safe to call `cleanup()` on a cap failure
    without destroying a user's own checkout. See
    `test_open_cap_rejection_never_deletes_a_local_checkout`.

    NOTE: this does not enforce the repository size caps. Only
    `WorkspaceManager.open` does. See that method's docstring.
    """
    root = resolve_local_path(path, allowed_roots)
    return Workspace(root=root, commit_sha=read_commit_sha(root), cleanup_dir=None)
