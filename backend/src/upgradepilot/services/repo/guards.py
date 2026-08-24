"""Security boundary for repository access.

Accepting a URL or a filesystem path is an arbitrary-read surface, so both
are validated here and nowhere else. The scheme allowlist and root list are
parameters rather than globals so tests can permit file:// without
weakening production defaults.

Contract: no input to either public function may raise anything other than
an `UpgradePilotError` subclass. Every filesystem/parsing operation that can
raise outside that hierarchy is caught and converted at its call site.
"""

import re
import unicodedata
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import urlsplit

from upgradepilot.models.errors import InvalidRepoUrlError, LocalPathForbiddenError

_MAX_URL_LENGTH = 2048
"""Conventional practical URL length bound (the de facto limit several major
browsers and servers have historically enforced). Not a spec requirement,
just cheap defence against log bloat and unbounded work: `detail` values and
this string both potentially reach the log, and later checks in this
function scan the whole string more than once."""

_FORBIDDEN_URL_CHARS = frozenset(map(chr, range(0x20))) | {"\x7f"}
"""C0 control characters and DEL.

`urlsplit()` silently strips `\\t`, `\\r` and `\\n` before parsing. A guard
that validates the *parsed* result but returns the *original* string can be
tricked into validating one string while handing a different one to the
caller (and, downstream, to `git clone`) — a classic parser-differential.
Reject these characters outright rather than normalise: the caller supplied
something we will not honour, and they should be told so plainly.

Kept as a fast, explicit path even though `_DISALLOWED_CATEGORIES` below is
the general rule that subsumes it (Cc covers this whole set plus the C1
control range that this frozenset does not).
"""

_DISALLOWED_CATEGORIES = frozenset({"Cc", "Cf", "Zl", "Zp", "Zs"})
"""Unicode general categories rejected after ASCII-space stripping: C0/C1
controls (Cc — includes NEL U+0085), format characters (Cf — includes
ZERO WIDTH SPACE and bidi override characters), line/paragraph separators
(Zl/Zp), and every space separator (Zs — includes NBSP, OGHAM SPACE MARK,
and the U+2000 block). An ordinary ASCII space is *also* category Zs, which
is exactly why it must be stripped from the edges before this check runs
(see `validate_clone_url`), not caught by it.

This exists because `str.strip()` with no argument uses Python's much
broader `str.isspace()` definition and would silently remove these
codepoints from the edges of the URL — the same "validate one string,
return a different one" anti-pattern `_FORBIDDEN_URL_CHARS` exists to kill,
just reached through `.strip()` instead of through `urlsplit()`.
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

    Checks run in order: length, control characters, disallowed unicode
    categories, parse round-trip, scheme, credentials, host — a caller
    should learn the most fundamental problem first.
    """
    if len(raw) > _MAX_URL_LENGTH:
        raise InvalidRepoUrlError(
            f"Repository URL is too long (max {_MAX_URL_LENGTH} characters).",
            detail=f"length={len(raw)}",
        )

    if any(ch in _FORBIDDEN_URL_CHARS for ch in raw):
        # Do not echo `raw`: a caller who pasted a tokenised URL containing
        # a stray control character would otherwise have that token written
        # to the log via `detail`. Name the defect class and the length —
        # enough to diagnose, nothing that can leak a credential.
        raise InvalidRepoUrlError(
            "Repository URL contains control characters, which are not allowed.",
            detail=f"control characters present; length={len(raw)}",
        )

    # Strip only ASCII horizontal/vertical whitespace from the edges — never
    # bare `.strip()`, which uses `str.isspace()` and would silently drop
    # NBSP, NEL, line/paragraph separators, and other non-ASCII "whitespace"
    # that `_FORBIDDEN_URL_CHARS` does not cover. See `_DISALLOWED_CATEGORIES`.
    candidate = raw.strip(" \t\r\n\f\v")
    if not candidate:
        raise InvalidRepoUrlError("A repository URL is required.")

    for ch in candidate:
        if unicodedata.category(ch) in _DISALLOWED_CATEGORIES:
            # Do not echo `candidate`: same reasoning as the control-
            # character branch above — this has not been screened for
            # credentials yet.
            raise InvalidRepoUrlError(
                "Repository URL contains disallowed control, formatting, or separator characters.",
                detail=f"category={unicodedata.category(ch)!r}; length={len(candidate)}",
            )

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
    except (OSError, RuntimeError, ValueError):
        # An allowed root that cannot itself be resolved — missing, a
        # symlink loop (RuntimeError), permission denied (OSError), or a
        # root string containing a NUL byte (ValueError, from the
        # underlying realpath syscall wrapper) — is treated as absent
        # rather than raising: the caller falls through to try the next
        # configured root, and if none match, resolve_local_path still
        # raises LocalPathForbiddenError. Fail closed, never open.
        return False

    for candidate in (resolved, *resolved.parents):
        try:
            if candidate.samefile(real_root):
                return True
        except (OSError, ValueError):
            # An ancestor that vanished or became unstattable between the
            # resolve() above and this samefile() call (a race, or a
            # removed/permission-changed directory) is treated as a
            # non-match, not an error: the loop just tries the next
            # ancestor, and the enclosing function still raises
            # LocalPathForbiddenError if nothing ever matches. Nothing is
            # silently permitted by this continue.
            continue
    return False


def resolve_local_path(raw: str, allowed_roots: Sequence[Path]) -> Path:
    """Resolve a local repository path, confined to the configured roots.

    `Path.resolve()` follows symlinks, so containment is checked against the
    real path — a symlink pointing outside an allowed root is rejected.
    An empty root list denies everything: this is the single most important
    line in the file, since an unconfigured allowlist must never mean
    "allow all". A near-empty list (a relative or blank root entry, e.g.
    `Path("")` or `Path(".")`) is rejected just as loudly, rather than
    resolved against the server's current working directory.
    """
    if not allowed_roots:
        raise LocalPathForbiddenError(
            "Local repository analysis is not enabled on this server.",
            detail="UP_ALLOWED_LOCAL_ROOTS is empty",
        )

    for root in allowed_roots:
        try:
            expanded_root = root.expanduser()
        except (OSError, RuntimeError, ValueError) as exc:
            raise LocalPathForbiddenError(
                "The server's local-repository allowlist is misconfigured.",
                detail=f"root={root!r} could not be expanded: {exc!r}",
            ) from exc
        if not expanded_root.is_absolute():
            # `Path("")` normalises to `Path(".")`, and a relative entry
            # like `Path(".")` would resolve against the server process's
            # current working directory in `_is_within` below — silently
            # granting the whole CWD tree instead of denying by default.
            # This is exactly the shape a naive `"".split(",")`-style
            # env-var loader produces for an unset/blank setting: `['']`,
            # not `[]`. Fail loudly on the one bad entry rather than
            # filtering it out and continuing with a narrower allowlist —
            # the operator's intent is unknown, and a silently narrowed
            # allowlist is the kind of misconfiguration nobody notices.
            raise LocalPathForbiddenError(
                f"The server's local-repository allowlist is misconfigured: "
                f"{root!r} is not an absolute path.",
                detail=f"non-absolute root={root!r}",
            )

    candidate = Path(raw.strip())

    try:
        candidate = candidate.expanduser()
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        # RuntimeError: `expanduser()` cannot determine a home directory
        # (e.g. `~nosuchuser/...`). ValueError: a NUL byte in the path
        # reaches the underlying `realpath` syscall wrapper, which raises
        # ValueError rather than OSError. Both are caller input, not a
        # config or filesystem-identity problem, so they get the same
        # "does not exist" treatment as an ordinary missing path.
        raise LocalPathForbiddenError(
            "That repository path does not exist.",
            detail=f"path={raw!r} error={exc!r}",
        ) from exc

    if not resolved.is_dir():
        raise LocalPathForbiddenError(
            "The repository path must be a directory.",
            detail=f"path={resolved}",
        )

    for root in allowed_roots:
        if _is_within(resolved, root):
            return resolved

    raise LocalPathForbiddenError(
        "That repository path is outside the allowed directories.",
        detail=f"path={resolved} roots={[str(r) for r in allowed_roots]}",
    )
