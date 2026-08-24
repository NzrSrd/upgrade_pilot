"""Security boundary for repository access.

Accepting a URL or a filesystem path is an arbitrary-read surface, so both
are validated here and nowhere else. The scheme allowlist and root list are
parameters rather than globals so tests can permit file:// without
weakening production defaults.
"""

import re
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

_USERINFO = re.compile(r"(?<=//)[^/@]*@")
"""Matches a `user:pass@` or bare `token@` authority prefix after `//`."""


def _redact(raw: str) -> str:
    """Strip any userinfo before a URL reaches a log or an error detail.

    `AppError.detail` is logged, and a caller may paste a tokenised URL. The
    project never stores raw credentials in plaintext, so no code path may
    place an un-redacted URL into a detail string — this is defence in
    depth for the rare case a URL must appear in a detail at all; the
    primary defence in this module is to not echo the input in the first
    place (see the comments at each raise site below).

    Known limitation: an authority with no `//` (e.g. a bare `user@host`
    with no scheme) is not redacted by this pattern. That is acceptable
    here because every call site below has already rejected inputs without
    a recognised `scheme://` shape before reaching a point where a URL is
    echoed at all.
    """
    return _USERINFO.sub("***@", raw)


def validate_clone_url(raw: str, allowed_schemes: frozenset[str]) -> str:
    """Return the URL unchanged if safe to hand to `git clone`.

    Checks run in order: control characters, parse round-trip, scheme,
    credentials, host — a caller should learn the most fundamental problem
    first.
    """
    if any(ch in _FORBIDDEN_URL_CHARS for ch in raw):
        # Do not echo `raw`: a caller who pasted a tokenised URL containing
        # a stray control character would otherwise have that token written
        # to the log via `detail`. Name the defect class and the length —
        # enough to diagnose, nothing that can leak a credential.
        raise InvalidRepoUrlError(
            "Repository URL contains control characters, which are not allowed.",
            detail=f"control characters present; length={len(raw)}",
        )

    candidate = raw.strip()
    if not candidate:
        raise InvalidRepoUrlError("A repository URL is required.")

    scheme, separator, remainder = candidate.partition("://")
    if separator:
        # Schemes are case-insensitive (RFC 3986) and git accepts HTTPS://,
        # but urlsplit lowercases the scheme, which would fail the
        # round-trip check below on nothing more than letter case.
        # Normalise the scheme only — never the host or path, which stay
        # case-sensitive (github.com/Acme/Repo is a different repository
        # from github.com/acme/repo) — so the round-trip check tests what
        # we actually care about: that no character was silently added or
        # dropped, not letter case.
        candidate = f"{scheme.lower()}{separator}{remainder}"

    parts = urlsplit(candidate)

    # Deliberately redundant with the control-character check above: that
    # check rejects the specific characters urlsplit() is known to strip
    # today; this is the general invariant — the parsed URL must describe
    # exactly the string we are about to return — that catches whatever
    # normalisation quirk a future Python release introduces.
    if parts.geturl() != candidate:
        # Do not echo `candidate` or the reparsed URL: at this point neither
        # string has been screened for credentials yet, and either could
        # contain one. Length is enough to diagnose a round-trip mismatch.
        raise InvalidRepoUrlError(
            "Repository URL could not be parsed unambiguously.",
            detail=f"parsed URL did not round-trip; length={len(candidate)}",
        )

    if parts.scheme not in allowed_schemes:
        # `parts.hostname` is a parsed field, never the raw credential-
        # bearing string — safe to log even when userinfo is present,
        # because urlsplit() separates username/password from hostname.
        raise InvalidRepoUrlError(
            f"Repository URL scheme must be one of: {', '.join(sorted(allowed_schemes))}.",
            detail=f"scheme={parts.scheme!r} host={parts.hostname!r}",
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
            detail=f"scheme={parts.scheme!r}",
        )

    if parts.scheme == "file" and not parts.path:
        # Credentials have already been rejected above, so `candidate`
        # cannot contain userinfo here — but redact defensively rather than
        # rely on that ordering never changing.
        raise InvalidRepoUrlError(
            "file:// URL is missing a path.", detail=f"url={_redact(candidate)!r}"
        )

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
