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
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

from upgradepilot.models.errors import InvalidRepoUrlError, RepoUnavailableError
from upgradepilot.services.repo.guards import resolve_local_path, validate_clone_url
from upgradepilot.services.repo.local import read_commit_sha
from upgradepilot.services.repo.workspace import HARDENED_GIT_ENV, Workspace

_STDERR_DETAIL_BUDGET = 500
"""`stderr` is untrusted, unbounded remote output -- a hostile or merely
broken remote can emit megabytes of it, and this lands in `AppError.detail`,
which is logged. Keep only the tail: the most recent lines are the ones
most likely to name the actual failure."""

_LOCALHOST_AUTHORITIES = frozenset({"", "localhost"})
"""The only `file://` authorities this module will clone from.

git ignores a `file://` URL's host entirely -- verified against git 2.50.1:
`git clone file://otherhost/path/to/repo` clones the LOCAL directory
`/path/to/repo` and exits 0, never contacting `otherhost`. Accepting such a
URL would therefore let a request that reads as remote quietly read the
server's own filesystem, so a host that is not empty or `localhost` is
refused rather than silently dropped."""


def _resolve_file_url(safe_url: str, allowed_local_roots: Sequence[Path]) -> str:
    """Re-derive a `file://` clone URL from an allowlisted local path.

    This is the fix for the second door into the local filesystem. A
    `LocalRepoRef` went through `resolve_local_path` and obeyed
    `allowed_local_roots`; a `file://` `RemoteRepoRef` went through
    `validate_clone_url`, which knows nothing about roots, so with `file`
    added to `allowed_url_schemes` an operator got UNBOUNDED read access to
    the filesystem from a setting named `allowed_local_roots`. Reproduced
    with `allowed_local_roots=()`: door one was denied, door two cloned a
    directory outside every root and read its contents.

    The mechanism is deliberately not a second containment check. This
    calls the very function the other door calls, so the two cannot
    disagree about a target for any reason -- containment, symlink escape,
    nonexistence, not-a-directory, an empty or misconfigured root list --
    and the parity is a property of the code rather than of two
    implementations kept in step by hand.

    The returned URL is rebuilt from `resolve_local_path`'s *resolved*
    path rather than passed through, which closes a parser differential in
    the process. git percent-decodes a `file://` path (verified: a clone of
    `file://<dir>/a%20b` reads the directory `a b`), so validating the raw
    URL text and handing that same text to git would validate one path and
    read another whenever an escape is present. Decoding before the check
    and re-encoding after it makes the string git receives a faithful
    encoding of the exact directory that was allowlisted -- including a
    directory whose name really does contain a `%`, which round-trips as
    `%25` (verified against git 2.50.1).
    """
    # Cannot raise: `validate_clone_url` has already parsed this string
    # successfully, and it is unchanged since.
    parts = urlsplit(safe_url)
    if parts.netloc not in _LOCALHOST_AUTHORITIES:
        raise InvalidRepoUrlError(
            "A file:// repository URL must not name a host "
            "(use file:///path or file://localhost/path).",
            detail=f"scheme=file host={parts.hostname!r}",
        )
    try:
        decoded_path = unquote(parts.path, errors="strict")
    except UnicodeDecodeError as exc:
        # `unquote`'s default is errors="replace", which turns an invalid
        # percent-encoded byte into U+FFFD instead of raising: `unquote("%ff")`
        # is `"\ufffd"`. That fails closed in practice — the mangled name
        # matches no real directory, so `resolve_local_path` says "does not
        # exist" — but failing closed by accident is not the invariant this
        # module enforces everywhere else. Silently substituting a character
        # into caller input is the exact anti-pattern `guards.py` spends its
        # category rule and its ambiguity refusal preventing, and the claim
        # this docstring makes just above — that a directory whose name really
        # does contain a `%` round-trips faithfully — is false for a name
        # containing a byte that is not valid UTF-8 unless the decode is
        # strict. Reject what will not decode instead of guessing at it.
        #
        # `detail` carries parsed, known-shape fields only: never `exc` (whose
        # message quotes the offending byte sequence) and never the path (which
        # has been credential-screened by `validate_clone_url` but is still
        # caller input this function has not otherwise echoed anywhere).
        raise InvalidRepoUrlError(
            "The file:// URL contains a percent-encoded sequence that is not valid UTF-8.",
            detail=f"scheme=file percent-decode failed; encoded path length={len(parts.path)}",
        ) from exc
    resolved = resolve_local_path(decoded_path, allowed_local_roots)
    return f"file://{quote(str(resolved))}"


def clone_repository(
    url: str,
    dest_parent: Path,
    *,
    depth: int,
    allowed_schemes: frozenset[str],
    allowed_local_roots: Sequence[Path],
    timeout: int = 180,
) -> Workspace:
    """Shallow-clone `url` into a fresh directory under `dest_parent`.

    `allowed_local_roots` is required rather than defaulted so that a
    caller must state the local-filesystem policy it is cloning under. It
    is consulted only for a `file://` URL (see `_resolve_file_url`), but a
    default of `()` would let a new call site silently inherit "deny all"
    or, worse, invite a default of "allow all" later; mypy refusing the
    call is the enforcement.

    NOTE: this does not enforce the repository size caps. Only
    `WorkspaceManager.open` does. See that method's docstring.
    """
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

    # The second door into the local filesystem, held to the same lock as
    # the first. Runs before `dest_parent.mkdir` below so a denied request
    # leaves nothing on disk, exactly as the scheme rejection does.
    if safe_url.startswith("file://"):
        safe_url = _resolve_file_url(safe_url, allowed_local_roots)

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
            env=HARDENED_GIT_ENV,
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
