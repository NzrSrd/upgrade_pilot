"""Security boundary for repository access.

Accepting a URL or a filesystem path is an arbitrary-read surface, so both
are validated here and nowhere else. The scheme allowlist and root list are
parameters rather than globals so tests can permit file:// without
weakening production defaults.

Contract: no `str` input to either public function may raise anything other
than an `UpgradePilotError` subclass. Every filesystem/parsing operation that
can raise outside that hierarchy is caught and converted at its call site.
The contract is deliberately limited to the declared parameter type. Passing
a non-`str` — a `Path` or `None` raises `AttributeError` from the first
string operation, `bytes` a `TypeError` slightly later — is out of scope:
these leaf functions do not spend `isinstance` checks re-litigating what the
type checker already enforces (mypy runs in strict mode over `services/`,
which includes this file). The cost if that judgement is wrong is an
unhelpful `AttributeError` for a future non-`str` caller, not a hole in the
security boundary.
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

Known cost of keeping `Cf`, accepted deliberately: a raw ZERO WIDTH JOINER
(U+200D, category Cf) is rejected, so a ZWJ-joined emoji sequence in a path
is refused in its raw form. Percent-encoding is the mitigation and it works
— `https://github.com/acme/a%E2%80%8Db-repo` is accepted where the raw form
is not — and the rejection `detail` names the category, so the fix is
discoverable from the error. Everything else non-ASCII that was checked
passes unaffected: plain emoji, combining accents, Han, Thai combining
marks, and variation selectors (U+FE0F is Mn, not Cf). The alternative,
dropping `Cf` from this set, would readmit BOM, SOFT HYPHEN and the bidi
override characters — the entire attack class this rule exists to close —
and GitHub and GitLab sanitise repository paths to ASCII regardless. Do not
widen this set to "fix" the ZWJ over-rejection.
"""

_MAX_PATH_LENGTH = 4096
"""Longest local path this boundary will look at, checked before anything
else touches the string.

4096 is Linux's `PATH_MAX`; macOS's is 1024. A longer path cannot name a
file on either platform, so rejecting it loses nothing and it bounds two
things that are otherwise unbounded: the work done below (the category scan
walks the whole string) and, with `_PATH_DETAIL_BUDGET`, the length of the
`detail` this function can write to the log. The rejection carries the
length and nothing else -- a path that has not been looked at yet is not
echoed."""

_PATH_DETAIL_BUDGET = 200
"""How much of a rejected path may reach a logged `AppError.detail`.

The same shape as `clone.py`'s `_STDERR_DETAIL_BUDGET`, for the same reason
and with the same tail-keeping bias: the tail of a path is its leaf name,
which is the part that explains a "does not exist" rejection. Unlike a URL,
a local path has no standardised credential slot and is the operator's most
useful datum for that rejection, so it is echoed -- bounded, not dropped."""

_ASCII_EDGE_WHITESPACE = " \t\r\n\f\v"
"""The only whitespace either public function will silently remove, and only
from the edges of its input.

Never bare `.strip()`, which uses `str.isspace()` and removes seventeen
codepoints -- NBSP, NEL, the U+2000 block, the line/paragraph separators.
Removing those silently is the "validate one string, use a different one"
anti-pattern this module exists to prevent, and it is exactly as harmful for
a path as for a URL: see `resolve_local_path`, where it caused the boundary
to open a *different directory* than the caller named. Everything outside
this set is rejected by `_first_disallowed_category` instead."""

_USERINFO = re.compile(r"(?<=//)[^/@]*@")
"""Matches a `user:pass@` or bare `token@` authority prefix after `//`."""


def _first_disallowed_category(text: str, *, allow_ascii_space: bool) -> str | None:
    """Return the Unicode general category of the first disallowed
    character in `text`, or None if every character is allowed.

    One implementation, one category set, shared by both public functions.
    A second, hand-rolled rule on the path side is precisely how the two
    sides drifted apart before: the URL side rejected invisible whitespace
    while the path side silently stripped it.

    `allow_ascii_space` is the single, deliberate difference between the two
    callers, and it is a difference in the *input domain*, not in the rule:

    - In a URL, a raw space is forbidden by RFC 3986 and percent-encoding is
      the correct, lossless spelling -- so `Zs` is rejected wholesale and
      `%20` is accepted (`validate_clone_url` passes False).
    - In a filesystem path, U+0020 is an ordinary, legal filename character
      with no encoded alternative. `My Documents` and `Café Projects` are
      real paths that must keep working, so the ASCII space is exempted --
      and only the ASCII space (`resolve_local_path` passes True). An
      interior NBSP, ZWSP or NEL stays rejected, because a caller cannot see
      one and neither can the operator reading the citation afterwards.
    """
    for character in text:
        if allow_ascii_space and character == " ":
            continue
        category = unicodedata.category(character)
        if category in _DISALLOWED_CATEGORIES:
            return category
    return None


def _bounded_for_detail(text: str) -> str:
    """Render caller-supplied text for a logged `detail`, length-bounded.

    Truncation is announced with the original length rather than left to
    look like the whole value, so an operator reading the log can tell a
    200-character path from a truncated 4000-character one.
    """
    if len(text) <= _PATH_DETAIL_BUDGET:
        return repr(text)
    return f"{text[-_PATH_DETAIL_BUDGET:]!r} (tail of {len(text)} characters)"


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
    """Return a NORMALISED URL that is safe to hand to `git clone`.

    The return value is the *only* string a caller may use. It is not
    always byte-identical to `raw`: two normalisations are applied, and
    both are deliberate and observable in the return value.

    - ASCII whitespace (`_ASCII_EDGE_WHITESPACE`) is stripped from the
      edges, so a pasted URL with a stray leading space is accepted.
    - The scheme is lower-cased, because schemes are case-insensitive
      (RFC 3986) and git accepts `HTTPS://`, while `urlsplit` lower-cases
      it and would otherwise fail the round-trip check on letter case
      alone. Nothing else is touched: host and path case are preserved,
      because `github.com/Acme/Repo` is a different repository from
      `github.com/acme/repo`.

    Everything else is rejected rather than normalised. A caller that uses
    `raw` instead of the return value reintroduces the parser differential
    this function exists to close -- validating one string and cloning
    another -- which is why `clone.py` uses the return value and says so.

    Checks run in order: length, control characters, disallowed unicode
    categories, parse, parse round-trip, scheme, credentials, host — a
    caller should learn the most fundamental problem first.

    Raises only `InvalidRepoUrlError` for any `str` input, per this
    module's contract; see the `urlsplit` branch below for the case that
    used to escape as a bare `ValueError`.
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
    candidate = raw.strip(_ASCII_EDGE_WHITESPACE)
    if not candidate:
        raise InvalidRepoUrlError("A repository URL is required.")

    # allow_ascii_space=False: in a URL a raw space is forbidden by RFC 3986
    # and `%20` is the correct spelling, so category Zs is rejected outright
    # once the edges have been stripped.
    category = _first_disallowed_category(candidate, allow_ascii_space=False)
    if category is not None:
        # Do not echo `candidate`: same reasoning as the control-character
        # branch above — this has not been screened for credentials yet.
        raise InvalidRepoUrlError(
            "Repository URL contains disallowed control, formatting, or separator characters.",
            detail=f"category={category!r}; length={len(candidate)}",
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

    try:
        parts = urlsplit(candidate)
    except ValueError as exc:
        # `urlsplit` raises a bare ValueError for several shapes the checks
        # above do not cover, because the offending characters are in
        # categories this module deliberately permits: an unterminated or
        # malformed IPv6 literal (`http://[::1`, `https://[::1]junk/x`,
        # `https://[]/x` — brackets are Ps/Pe) and a netloc containing a
        # character that changes under NFKC normalisation (`℀` is So, `＃`
        # is Po). Without this branch a user typo escaped the
        # `UpgradePilotError` hierarchy this module's contract promises, and
        # surfaced as an unhandled exception whose traceback carried the raw
        # URL — which at this point has NOT yet been credential-screened.
        #
        # `exc` itself is never echoed: `urlsplit`'s NFKC message quotes the
        # whole netloc, and the netloc is where userinfo lives, so the
        # exception's own text is a credential-leak channel. Report the
        # length only, exactly as the round-trip branch below does.
        raise InvalidRepoUrlError(
            "Repository URL could not be parsed.",
            detail=f"urlsplit rejected the URL; length={len(candidate)}",
        ) from exc

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

    The returned path is the directory that gets read, and it always
    corresponds to `raw`. That invariant is the whole point of this
    function and it was broken: a bare `Path(raw.strip())` removed all
    seventeen codepoints `str.isspace()` covers, so asking for a real
    directory named `repo\\xa0` silently resolved and returned the
    *different* real directory `repo` — after which every file path, line
    number and sha the product cited belonged to a repository the caller
    never named. The fix is the URL side's rule, shared rather than
    re-derived (`_first_disallowed_category`): reject a disallowed Unicode
    category instead of silently stripping it.

    Exactly one normalisation survives, matching `validate_clone_url`:
    ASCII whitespace is stripped from the *edges*, because a leading or
    trailing space around a pasted path is a plausible caller slip. That
    is a substitution, so it is not left silent either — if the literal,
    unstripped input also names something on the filesystem, the request
    is ambiguous and is refused rather than guessed at. Interior spaces
    are untouched: `My Documents` and `Café Projects` are ordinary paths.

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
                "The server's local-repository allowlist is misconfigured.",
                detail=f"non-absolute root={root!r}",
            )

    # Before `raw` is used for anything, including being echoed into a
    # `detail`. Length only in this rejection: an unlooked-at path is not
    # written to the log. See `_MAX_PATH_LENGTH`.
    if len(raw) > _MAX_PATH_LENGTH:
        raise LocalPathForbiddenError(
            f"That repository path is too long (max {_MAX_PATH_LENGTH} characters).",
            detail=f"length={len(raw)}",
        )

    candidate_text = raw.strip(_ASCII_EDGE_WHITESPACE)
    if not candidate_text:
        raise LocalPathForbiddenError(
            "A repository path is required.",
            detail=f"blank after stripping ASCII edge whitespace; length={len(raw)}",
        )

    # allow_ascii_space=True: U+0020 is a legal, ordinary filename
    # character with no encoded alternative, unlike in a URL. Everything
    # else in `_DISALLOWED_CATEGORIES` — NBSP, ZWSP, NEL, the bidi
    # overrides, the U+2000 block — stays rejected, because a caller cannot
    # see one and the citation that follows would name the wrong directory.
    category = _first_disallowed_category(candidate_text, allow_ascii_space=True)
    if category is not None:
        raise LocalPathForbiddenError(
            "That repository path contains disallowed control, formatting, "
            "or separator characters.",
            detail=f"category={category!r}; length={len(candidate_text)}",
        )

    if candidate_text != raw:
        # Edge ASCII whitespace was removed, so the path about to be opened
        # is not byte-identical to the one asked for. That is tolerated as a
        # paste slip only while it cannot mean anything else: a trailing
        # space is legal in a POSIX filename, so if the literal input also
        # names something on disk, both readings are real and choosing one
        # silently is the very defect this function was fixed for. Refuse
        # instead, and say which whitespace to remove.
        try:
            literal_is_real = Path(raw).exists(follow_symlinks=False)
            probe = "the literal input also names an existing filesystem entry"
        except (OSError, RuntimeError, ValueError) as exc:
            # Not a swallow (rule 20): the probe's failure is converted into
            # the rejection below, with its type recorded in `detail`. Fail
            # closed — an unprobeable literal is not evidence that
            # substituting the stripped form is safe.
            literal_is_real = True
            probe = f"the literal input could not be probed: {type(exc).__name__}"
        if literal_is_real:
            raise LocalPathForbiddenError(
                "Remove the whitespace from the start or end of that repository "
                "path — with it present the path is ambiguous.",
                detail=f"edge whitespace stripped; {probe}; length={len(raw)}",
            )

    candidate = Path(candidate_text)

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
        #
        # The path IS echoed here — for "that path does not exist" it is
        # the operator's most useful datum, it is server-side and
        # thread_id-correlated, and a local path has no standardised
        # credential slot — but bounded (`_bounded_for_detail`), never
        # verbatim. Only the exception's *type* accompanies it: `repr(exc)`
        # on an OSError re-embeds the filename, which would put the
        # unbounded path back into the same string the budget just bounded.
        raise LocalPathForbiddenError(
            "That repository path does not exist.",
            detail=f"path={_bounded_for_detail(candidate_text)} error={type(exc).__name__}",
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
