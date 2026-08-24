"""Security boundary for repository access.

Accepting a URL or a filesystem path is an arbitrary-read surface, so both
are validated here and nowhere else. The scheme allowlist and root list are
parameters rather than globals so tests can permit file:// without
weakening production defaults.
"""

from collections.abc import Sequence
from pathlib import Path
from urllib.parse import urlsplit

from upgradepilot.models.errors import InvalidRepoUrlError, LocalPathForbiddenError

_FORBIDDEN_URL_CHARS = frozenset(map(chr, range(0x20))) | {"\x7f"}
"""C0 control characters and DEL.

`urlsplit()` silently strips `\\t`, `\\r` and `\\n` before parsing. A guard
that validates the *parsed* result but returns the *original* string can be
tricked into validating one string while handing a different one to the
caller (and, downstream, to `git clone`) — a classic parser-differential.
Reject these characters outright rather than normalise: the caller supplied
something we will not honour, and they should be told so plainly.
"""


def validate_clone_url(raw: str, allowed_schemes: frozenset[str]) -> str:
    """Return the URL unchanged if safe to hand to `git clone`.

    Checks run in order: control characters, parse round-trip, scheme,
    credentials, host — a caller should learn the most fundamental problem
    first.
    """
    if any(ch in _FORBIDDEN_URL_CHARS for ch in raw):
        raise InvalidRepoUrlError(
            "Repository URL contains control characters, which are not allowed.",
            detail=f"url={raw!r}",
        )

    candidate = raw.strip()
    if not candidate:
        raise InvalidRepoUrlError("A repository URL is required.")

    parts = urlsplit(candidate)

    # Deliberately redundant with the control-character check above: that
    # check rejects the specific characters urlsplit() is known to strip
    # today; this is the general invariant — the parsed URL must describe
    # exactly the string we are about to return — that catches whatever
    # normalisation quirk a future Python release introduces.
    if parts.geturl() != candidate:
        raise InvalidRepoUrlError(
            "Repository URL could not be parsed unambiguously.",
            detail=f"candidate={candidate!r} reparsed={parts.geturl()!r}",
        )

    if parts.scheme not in allowed_schemes:
        raise InvalidRepoUrlError(
            f"Repository URL scheme must be one of: {', '.join(sorted(allowed_schemes))}.",
            detail=f"scheme={parts.scheme!r} url={candidate!r}",
        )

    if parts.username or parts.password:
        raise InvalidRepoUrlError(
            "Remove the credentials from the repository URL. "
            "Private repositories are not supported yet.",
            detail="credentials present in netloc",
        )

    # file:// legitimately has an empty host; every network scheme needs one.
    if parts.scheme != "file" and not parts.hostname:
        raise InvalidRepoUrlError(
            "Repository URL is missing a host.",
            detail=f"url={candidate!r}",
        )

    if parts.scheme == "file" and not parts.path:
        raise InvalidRepoUrlError("file:// URL is missing a path.", detail=candidate)

    return candidate


def _is_within(resolved: Path, root: Path) -> bool:
    """Containment by filesystem identity, not string comparison.

    A string comparison (`resolved == root or root in resolved.parents`) is
    wrong on a case-insensitive or unicode-normalising volume: `/users/x` and
    `/Users/x` can name the same directory but be different strings, and
    APFS does not normalise Unicode on write, so an NFD-encoded path and its
    NFC counterpart resolve to string-different `Path` objects for the same
    inode. `samefile()` compares `st_dev`/`st_ino`, so case-folding and
    Unicode normalisation both stop mattering.

    Do NOT fix this by case-folding the strings instead — that would make
    the check case-insensitive on case-*sensitive* volumes too, where
    `/data` and `/Data` are genuinely different directories, turning a
    fail-closed annoyance into a fail-open hole. Inode identity is correct
    on every volume; case-folding is correct on none.
    """
    try:
        real_root = root.expanduser().resolve(strict=True)
    except (OSError, RuntimeError):
        # An allowed root that cannot itself be resolved (missing, a
        # symlink loop, permission denied) is treated as absent rather than
        # raising: the caller falls through to try the next configured
        # root, and if none match, resolve_local_path still raises
        # LocalPathForbiddenError. Fail closed, never open.
        return False

    for candidate in (resolved, *resolved.parents):
        try:
            if candidate.samefile(real_root):
                return True
        except OSError:
            continue
    return False


def resolve_local_path(raw: str, allowed_roots: Sequence[Path]) -> Path:
    """Resolve a local repository path, confined to the configured roots.

    `Path.resolve()` follows symlinks, so containment is checked against the
    real path — a symlink pointing outside an allowed root is rejected.
    An empty root list denies everything: this is the single most important
    line in the file, since an unconfigured allowlist must never mean
    "allow all".
    """
    candidate = Path(raw.strip()).expanduser()

    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise LocalPathForbiddenError(
            "That repository path does not exist.",
            detail=f"path={raw!r} error={exc!r}",
        ) from exc

    if not resolved.is_dir():
        raise LocalPathForbiddenError(
            "The repository path must be a directory.",
            detail=f"path={resolved}",
        )

    if not allowed_roots:
        raise LocalPathForbiddenError(
            "Local repository analysis is not enabled on this server.",
            detail="UP_ALLOWED_LOCAL_ROOTS is empty",
        )

    for root in allowed_roots:
        if _is_within(resolved, root):
            return resolved

    raise LocalPathForbiddenError(
        "That repository path is outside the allowed directories.",
        detail=f"path={resolved} roots={[str(r) for r in allowed_roots]}",
    )
