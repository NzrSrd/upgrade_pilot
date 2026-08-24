"""Shallow-clone resolver for public repositories.

Depth defaults above 1 because churn signals need history. Credential
prompting is disabled so a private repository fails fast rather than
hanging the run waiting on stdin.
"""

import shutil
import subprocess
import uuid
from pathlib import Path

from upgradepilot.models.errors import RepoUnavailableError
from upgradepilot.services.repo.guards import validate_clone_url
from upgradepilot.services.repo.local import read_commit_sha
from upgradepilot.services.repo.workspace import Workspace

_STDERR_DETAIL_BUDGET = 500
"""`stderr` is untrusted, unbounded remote output -- a hostile or merely
broken remote can emit megabytes of it, and this lands in `AppError.detail`,
which is logged. Keep only the tail: the most recent lines are the ones
most likely to name the actual failure."""

_NON_INTERACTIVE_GIT_ENV = {
    # No credential prompting: a private repository must fail fast rather
    # than hang the run waiting on stdin.
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_ASKPASS": "/usr/bin/true",
    # Neither system nor global config may be consulted. This is a SECURITY
    # control, not tidiness: `url.<base>.insteadOf` rewrites a clone URL
    # after our allowlist has approved it, so a rewrite rule in either file
    # would bypass `validate_clone_url` entirely. Verified against real git
    # 2.50.1: with a global `insteadOf` rule reachable, `https://github.com/...`
    # became `https://REWRITTEN.invalid/...`. Do NOT remove these two lines,
    # and do NOT add HOME to this environment expecting the omission to keep
    # protecting us -- HOME being unset is not what stops the rewrite,
    # GIT_CONFIG_GLOBAL=/dev/null is.
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "PATH": "/usr/bin:/bin:/usr/local/bin",
}


def clone_repository(
    url: str,
    dest_parent: Path,
    *,
    depth: int,
    allowed_schemes: frozenset[str],
    timeout: int = 180,
) -> Workspace:
    """Shallow-clone `url` into a fresh directory under `dest_parent`."""
    # Must run before any subprocess is spawned: this is the whole point of
    # the allowlist. Use the returned value, never `url` -- validate_clone_url
    # normalises its input (lower-cased scheme, stripped ASCII whitespace),
    # and the normalised string is the only one that has actually been
    # screened.
    safe_url = validate_clone_url(url, allowed_schemes)

    dest_parent.mkdir(parents=True, exist_ok=True)
    destination = dest_parent / f"repo-{uuid.uuid4().hex[:12]}"

    command = [
        "git",
        "clone",
        "--depth",
        str(max(1, depth)),
        "--single-branch",
        "--quiet",
        safe_url,
        str(destination),
    ]

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=_NON_INTERACTIVE_GIT_ENV,
        )
    except subprocess.TimeoutExpired as exc:
        # ignore_errors=True: a half-cloned directory left behind is worse
        # than failing to remove one, and it is not a silent swallow (rule
        # 20) because a RepoUnavailableError is raised immediately on every
        # path below -- nothing here is quietly absorbed.
        shutil.rmtree(destination, ignore_errors=True)
        raise RepoUnavailableError(
            "Cloning the repository timed out.",
            detail=f"url={safe_url} timeout={timeout}s",
        ) from exc

    if completed.returncode != 0:
        shutil.rmtree(destination, ignore_errors=True)
        stderr_tail = completed.stderr.strip()[-_STDERR_DETAIL_BUDGET:]
        raise RepoUnavailableError(
            "The repository could not be cloned. Check that the URL is correct "
            "and the repository is public.",
            detail=f"url={safe_url} exit={completed.returncode} stderr={stderr_tail}",
        )

    return Workspace(
        root=destination,
        commit_sha=read_commit_sha(destination),
        cleanup_dir=destination,
    )
