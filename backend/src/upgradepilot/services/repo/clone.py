"""Shallow-clone resolver for public repositories.

Depth defaults above 1 because churn signals need history. Credential
prompting is disabled so a private repository fails fast rather than
hanging the run waiting on stdin.

Submodules are deliberately NOT fetched: plain `git clone --depth N` never
recurses into them, and this module adds no `--recurse-submodules` flag.
That is scope, not an oversight -- fetching them would change the clone's
cost profile, and this task does not own that decision. The practical
consequence: a repository whose real code lives in a submodule analyses as
nearly empty, with nothing in this module's return value signalling that
anything was skipped. A consumer that needs completeness must detect
`.gitmodules` in the cloned tree itself and surface that to the caller;
this module does not do so.
"""

import shutil
import subprocess
import uuid
from pathlib import Path

from upgradepilot.models.errors import InvalidRepoUrlError, RepoUnavailableError
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
"""Which of the entries above are proven by a test, and which are not:

- `GIT_CONFIG_GLOBAL=/dev/null`: proven. Deleting it turns
  `test_a_global_insteadof_rule_cannot_redirect_the_clone` red (a real
  `insteadOf` rewrite fires and the clone fails against the rewritten,
  nonexistent host).
- `GIT_CONFIG_SYSTEM=/dev/null`, `GIT_CONFIG_NOSYSTEM=1`: defence in depth
  with no hermetic test possible. Both guard against a *system-wide* git
  config file, and this sandboxed test environment has no such file to
  poison in the first place, so deleting either turns zero tests red. Kept
  anyway because a deployment target is not guaranteed to share that
  property.
- `GIT_TERMINAL_PROMPT=0`, `GIT_ASKPASS=/usr/bin/true`: defence in depth
  with no hermetic test possible. Both only matter for a repository that
  actually prompts for credentials, and every test here clones from a
  `file://` origin, which never prompts. Exercising this honestly would
  need a real private repository, which is out of reach for a hermetic
  suite. Kept anyway: the alternative is hanging on stdin against a private
  repo in production. This is a deliberate, reported gap, not an oversight.
"""


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

    # A parser differential between the validator and git, not between the
    # validator and itself: `guards.validate_clone_url` exempts `file:` from
    # its host check, because file:// legitimately has no host -- but that
    # exemption also lets a hostless, slash-less `file:./relative` or
    # `file:../x` through unchanged. git's own transport selection only
    # recognises `scheme://...`; anything shaped like `word:path` with no
    # `//` falls through to git's scp-style remote-alias parsing, so git
    # would treat `file:./relative` as ssh to a host literally named
    # "file", not as a local-filesystem clone. Fixed here, in this module
    # rather than in guards.py (which this task does not own), before the
    # string ever reaches git.
    if safe_url.startswith("file:") and not safe_url.startswith("file://"):
        raise InvalidRepoUrlError(
            "file: URLs must include '//' before the path (e.g. file:///path or file://host/path).",
            detail="scheme=file missing '//' authority separator",
        )

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
        # ignore_errors=True: same reasoning as the TimeoutExpired branch
        # above -- a RepoUnavailableError follows immediately on every path
        # below, so nothing here is silently swallowed.
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
