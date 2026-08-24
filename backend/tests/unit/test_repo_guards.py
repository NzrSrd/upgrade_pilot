import json
import os
import unicodedata
from pathlib import Path

import pytest

from upgradepilot.models.errors import (
    ErrorCode,
    InvalidRepoUrlError,
    LocalPathForbiddenError,
    UpgradePilotError,
)
from upgradepilot.services.repo.guards import (
    _DISALLOWED_CATEGORIES,
    _redact,
    resolve_local_path,
    validate_clone_url,
)

DEFAULT_SCHEMES = frozenset({"https", "git"})


# --- URL validation -------------------------------------------------------


def test_accepts_an_https_github_url() -> None:
    url = validate_clone_url("https://github.com/acme/payment-service", DEFAULT_SCHEMES)
    assert url == "https://github.com/acme/payment-service"


def test_accepts_a_git_scheme_url() -> None:
    assert validate_clone_url("git://example.com/repo.git", DEFAULT_SCHEMES)


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ssh://git@github.com/acme/repo.git",
        "ftp://example.com/repo",
        "http://github.com/acme/repo",
        "/Users/nzrsrd/Code/demo",
        "github.com/acme/repo",
    ],
)
def test_rejects_disallowed_schemes(url: str) -> None:
    with pytest.raises(InvalidRepoUrlError) as excinfo:
        validate_clone_url(url, DEFAULT_SCHEMES)
    assert excinfo.value.code is ErrorCode.INVALID_REPO_URL


def test_rejects_credentials_embedded_in_the_url() -> None:
    """Never accept a token pasted into a URL — Sub-project 2 handles auth properly."""
    with pytest.raises(InvalidRepoUrlError) as excinfo:
        validate_clone_url("https://user:token@github.com/acme/repo", DEFAULT_SCHEMES)
    assert "credential" in str(excinfo.value).lower()


def test_rejects_a_url_with_no_host() -> None:
    with pytest.raises(InvalidRepoUrlError):
        validate_clone_url("https:///acme/repo", DEFAULT_SCHEMES)


def test_rejects_blank_input() -> None:
    with pytest.raises(InvalidRepoUrlError):
        validate_clone_url("   ", DEFAULT_SCHEMES)


def test_allowlist_is_injectable_so_tests_can_permit_file_urls() -> None:
    """Clone-resolver tests need file:// without weakening production defaults."""
    assert validate_clone_url("file:///tmp/repo", frozenset({"file"}))


# --- URL validation: control characters and parse-integrity (addendum Finding 1) --


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/acme/repo\n--upload-pack=/bin/sh",
        "https://github.com/acme/repo\t--config=core.sshCommand=evil",
        "ht\ntps://github.com/x",
        "https://github.com/acme/repo\r",
    ],
)
def test_rejects_control_characters_in_a_url(url: str) -> None:
    """urlsplit() strips \\t\\r\\n before parsing, so a validated parse can
    describe a different string than the one returned. Reject rather than
    normalise: the caller asked for something we will not honour."""
    with pytest.raises(InvalidRepoUrlError) as excinfo:
        validate_clone_url(url, DEFAULT_SCHEMES)
    assert excinfo.value.code is ErrorCode.INVALID_REPO_URL


def test_rejects_a_url_whose_reparse_does_not_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    """General invariant, deliberately redundant with the control-character
    check: whatever the parsed representation is, it must describe exactly
    the string we return — otherwise the validated value and the returned
    value are two different things."""
    import upgradepilot.services.repo.guards as guards_module

    class _FakeSplitResult:
        scheme = "https"
        hostname = "github.com"
        username = None
        password = None
        path = "/acme/repo"

        def geturl(self) -> str:
            return "https://github.com/acme/repo-DIFFERENT"

    monkeypatch.setattr(guards_module, "urlsplit", lambda _candidate: _FakeSplitResult())

    with pytest.raises(InvalidRepoUrlError):
        validate_clone_url("https://github.com/acme/repo", DEFAULT_SCHEMES)


def test_accepted_url_is_byte_identical_to_the_input() -> None:
    """This is the property Finding 1 found broken: the validated value must
    be the same string that gets handed to `git clone`, not a look-alike."""
    raw = "https://github.com/acme/payment-service.git"
    assert validate_clone_url(raw, DEFAULT_SCHEMES) == raw


# --- URL validation: scheme case is normalised, host/path case is not (fix round 2) --


@pytest.mark.parametrize("scheme_variant", ["HTTPS", "Https", "hTTps"])
def test_accepts_a_case_varied_scheme_and_normalises_it_to_lowercase(scheme_variant: str) -> None:
    """Schemes are case-insensitive per RFC 3986, and git accepts HTTPS://.
    urlsplit() lowercases the scheme when parsing, so the round-trip
    invariant must normalise scheme case before comparing — otherwise a
    user pasting an uppercase-scheme URL from documentation is wrongly told
    their URL 'did not round-trip'. The normalisation is observable in the
    return value, so pin it: the returned scheme is always lowercase."""
    url = f"{scheme_variant}://github.com/acme/repo"
    assert validate_clone_url(url, DEFAULT_SCHEMES) == "https://github.com/acme/repo"


def test_host_and_path_case_are_preserved_not_lowered() -> None:
    """The fix must normalise only the scheme. Lowercasing the whole URL
    would be wrong: github.com/Acme/Repo is a different repository from
    github.com/acme/repo, and this is the test that stops someone
    'simplifying' the scheme fix to candidate.lower()."""
    url = "https://GitHub.com/Acme/Repo"
    assert validate_clone_url(url, DEFAULT_SCHEMES) == "https://GitHub.com/Acme/Repo"


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/acme/repo\n--upload-pack=/bin/sh",
        "https://github.com/acme/repo\t--config=core.sshCommand=evil",
        "ht\ntps://github.com/x",
        "https://github.com/acme/repo\r",
        "https://github.com\r\n@evil.com/x",
    ],
)
def test_scheme_case_normalisation_does_not_widen_the_control_character_hole(url: str) -> None:
    """The scheme-case fix touches the same round-trip check that Finding 1
    depends on. Re-assert, in this file rather than a one-off probe, that
    normalising scheme case did not also let any of these back in."""
    with pytest.raises(InvalidRepoUrlError):
        validate_clone_url(url, DEFAULT_SCHEMES)


# --- URL validation: no rejection path may leak a credential (fix round 1) --

_TOKEN = "ghp_TOKEN_SECRET_VALUE"  # noqa: S105 - not a real credential, a test fixture


@pytest.mark.parametrize(
    ("url", "patch_urlsplit"),
    [
        pytest.param(f"ftp://user:{_TOKEN}@github.com/a/b", False, id="scheme_rejection"),
        pytest.param(
            f"https://user:{_TOKEN}@github.com/a/b\n--x", False, id="control_character_rejection"
        ),
        pytest.param(f"https:///acme/{_TOKEN}", False, id="missing_host_rejection"),
        pytest.param(f"https://github.com/acme/{_TOKEN}", True, id="round_trip_rejection"),
    ],
)
def test_no_rejection_path_leaks_a_credential(
    url: str, patch_urlsplit: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AppError.detail is logged, and raw credentials must never be stored
    in plaintext. Every rejection path must be screened for this, not only
    the dedicated credentials-rejection path — parametrized over the four
    paths that fire *before* that dedicated check, so a future rejection
    path added without redaction fails this suite."""
    if patch_urlsplit:
        import upgradepilot.services.repo.guards as guards_module

        class _FakeSplitResult:
            scheme = "https"
            hostname = "github.com"
            username = None
            password = None
            path = "/acme/" + _TOKEN

            def geturl(self) -> str:
                return "https://github.com/acme/DIFFERENT"

        monkeypatch.setattr(guards_module, "urlsplit", lambda _candidate: _FakeSplitResult())

    with pytest.raises(InvalidRepoUrlError) as excinfo:
        validate_clone_url(url, DEFAULT_SCHEMES)

    assert _TOKEN not in str(excinfo.value)
    assert _TOKEN not in excinfo.value.message
    assert _TOKEN not in (excinfo.value.detail or "")


def test_control_character_detail_does_not_contain_the_raw_input() -> None:
    """Not just the token: the full raw input (including a command-injection
    payload) must not appear in the logged detail."""
    raw = "https://github.com/acme/repo\n--upload-pack=/bin/sh"
    with pytest.raises(InvalidRepoUrlError) as excinfo:
        validate_clone_url(raw, DEFAULT_SCHEMES)
    assert raw not in (excinfo.value.detail or "")
    assert "--upload-pack" not in (excinfo.value.detail or "")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://user:tok@github.com/a/b", "https://***@github.com/a/b"),
        ("https://tok@github.com/a/b", "https://***@github.com/a/b"),
        ("https://github.com/a/b", "https://github.com/a/b"),
        ("https://github.com/a/b?x=1@2", "https://github.com/a/b?x=1@2"),
    ],
)
def test_redact_strips_userinfo_but_leaves_the_rest_untouched(raw: str, expected: str) -> None:
    assert _redact(raw) == expected


# --- URL validation: unicode whitespace is rejected, not silently stripped (fix round 3) --


@pytest.mark.parametrize(
    ("name", "url"),
    [
        ("NEL_U+0085_trailing", "https://github.com/acme/repo\x85"),
        ("NBSP_U+00A0_both_ends", "\xa0https://github.com/acme/repo\xa0"),
        ("LINE_SEPARATOR_U+2028", "https://github.com/acme/repo\u2028"),
        ("PARAGRAPH_SEPARATOR_U+2029", "https://github.com/acme/repo\u2029"),
        ("OGHAM_SPACE_MARK_U+1680_leading", "\u1680https://github.com/acme/repo"),
        ("EN_QUAD_U+2000_leading", "\u2000https://github.com/acme/repo"),
        ("ZERO_WIDTH_SPACE_U+200B_leading", "\u200bhttps://github.com/acme/repo"),
    ],
)
def test_rejects_non_ascii_whitespace_instead_of_silently_stripping_it(name: str, url: str) -> None:
    """`str.strip()` with no argument uses `str.isspace()`, which silently
    removes these 7 codepoints even though none of them is in
    `_FORBIDDEN_URL_CHARS` — the exact "validate one string, return a
    different one" anti-pattern Finding 1 exists to eliminate, reached via
    `.strip()` instead of via `urlsplit()`. Reject rather than normalise."""
    with pytest.raises(InvalidRepoUrlError) as excinfo:
        validate_clone_url(url, DEFAULT_SCHEMES)
    assert excinfo.value.code is ErrorCode.INVALID_REPO_URL


def test_disallowed_category_detail_does_not_contain_the_raw_input() -> None:
    url = "\xa0https://github.com/acme/repo\xa0"
    with pytest.raises(InvalidRepoUrlError) as excinfo:
        validate_clone_url(url, DEFAULT_SCHEMES)
    assert url not in (excinfo.value.detail or "")


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/acme/repo",
        "  https://github.com/acme/repo  ",
        "https://github.com/acme/repo/path%20with%20encoded%20space",
        "https://github.com/acme/\u0440\u0435\u043f\u043e",  # Cyrillic "repo"
    ],
)
def test_unicode_category_check_accepts_ordinary_and_legitimate_urls(url: str) -> None:
    """The category check must not reject: an ordinary ASCII-space-padded
    URL (space is category Zs too, so it must be stripped from the edges
    *before* the check runs), a percent-encoded space (no raw whitespace
    character at all), or a legitimate non-ASCII path segment (Cyrillic
    letters are category Ll/Lu, not in the disallowed set)."""
    assert validate_clone_url(url, DEFAULT_SCHEMES)


# --- Local path resolution ------------------------------------------------


def test_accepts_a_path_inside_an_allowed_root(tmp_path: Path) -> None:
    project = tmp_path / "demo"
    project.mkdir()
    assert resolve_local_path(str(project), [tmp_path]) == project.resolve()


def test_accepts_the_allowed_root_itself(tmp_path: Path) -> None:
    assert resolve_local_path(str(tmp_path), [tmp_path]) == tmp_path.resolve()


def test_rejects_a_path_outside_every_allowed_root(tmp_path: Path) -> None:
    outside = tmp_path.parent / "elsewhere"
    outside.mkdir(exist_ok=True)
    with pytest.raises(LocalPathForbiddenError) as excinfo:
        resolve_local_path(str(outside), [tmp_path / "allowed"])
    assert excinfo.value.code is ErrorCode.LOCAL_PATH_FORBIDDEN


def test_rejects_traversal_escaping_an_allowed_root(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    with pytest.raises(LocalPathForbiddenError):
        resolve_local_path(str(allowed / ".." / ".."), [allowed])


def test_rejects_a_symlink_pointing_outside_an_allowed_root(tmp_path: Path) -> None:
    """resolve() follows symlinks, so containment is checked on the real path."""
    allowed = tmp_path / "allowed"
    secret = tmp_path / "secret"
    allowed.mkdir()
    secret.mkdir()
    link = allowed / "escape"
    link.symlink_to(secret, target_is_directory=True)

    with pytest.raises(LocalPathForbiddenError):
        resolve_local_path(str(link), [allowed])


def test_rejects_a_missing_directory(tmp_path: Path) -> None:
    with pytest.raises(LocalPathForbiddenError) as excinfo:
        resolve_local_path(str(tmp_path / "nope"), [tmp_path])
    assert "does not exist" in str(excinfo.value).lower()


def test_rejects_a_file_where_a_directory_is_required(tmp_path: Path) -> None:
    target = tmp_path / "a.py"
    target.write_text("x = 1\n")
    with pytest.raises(LocalPathForbiddenError):
        resolve_local_path(str(target), [tmp_path])


def test_rejects_when_no_roots_are_configured(tmp_path: Path) -> None:
    """An empty allowlist denies everything rather than allowing everything."""
    with pytest.raises(LocalPathForbiddenError):
        resolve_local_path(str(tmp_path), [])


# --- Local path resolution: containment by filesystem identity (addendum Finding 2) --


def test_accepts_a_lowercased_variant_of_the_allowed_root(tmp_path: Path) -> None:
    """On a case-insensitive volume, `/users/x` and `/Users/x` name the same
    directory but are different strings. Containment must be checked by
    filesystem identity (samefile), not string comparison.

    `Path.resolve()` only follows symlinks; it does not rewrite the string
    to the on-disk canonical casing. So the return value legitimately keeps
    the caller's casing — what matters is that it resolves to the same
    directory as the allowed root's child, checked with `samefile()`."""
    project = tmp_path / "Demo"
    project.mkdir()
    lowered = Path(str(project).lower())
    if not lowered.exists():
        pytest.skip("filesystem is case-sensitive; lowercased path does not exist")
    result = resolve_local_path(str(lowered), [tmp_path])
    assert result.samefile(project)


def test_accepts_an_uppercased_variant_of_the_allowed_root(tmp_path: Path) -> None:
    project = tmp_path / "Demo"
    project.mkdir()
    uppered = Path(str(project).upper())
    if not uppered.exists():
        pytest.skip("filesystem is case-sensitive; uppercased path does not exist")
    result = resolve_local_path(str(uppered), [tmp_path])
    assert result.samefile(project)


def test_accepts_an_nfd_unicode_variant_of_the_allowed_root(tmp_path: Path) -> None:
    """APFS does not normalise on write, so an NFD-encoded path and its NFC
    counterpart resolve to string-different Path objects that are the same
    inode. String comparison denies this; samefile() does not."""
    project = tmp_path / "café"
    project.mkdir()
    nfd_variant = Path(unicodedata.normalize("NFD", str(project)))
    result = resolve_local_path(str(nfd_variant), [tmp_path])
    assert result.samefile(project)


def test_rejects_a_path_outside_every_allowed_root_still_denied_by_identity_check(
    tmp_path: Path,
) -> None:
    """The inode-based containment check must not be weaker than the string
    check it replaces: a plain-outside path stays denied."""
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(LocalPathForbiddenError):
        resolve_local_path(str(outside), [allowed])


# --- Local path resolution: only UpgradePilotError may escape (fix round 3, finding 1) --


def test_a_nul_byte_in_the_path_raises_local_path_forbidden_not_a_raw_valueerror(
    tmp_path: Path,
) -> None:
    """Path.resolve(strict=True) calls os.path.realpath, which raises a bare
    ValueError ('lstat: embedded null character in path') for an embedded
    NUL — not an OSError or RuntimeError, so the previous except clause let
    it escape as an unhandled exception instead of LocalPathForbiddenError."""
    with pytest.raises(LocalPathForbiddenError):
        resolve_local_path("a\x00b", [tmp_path])


def test_home_directory_lookup_failure_raises_local_path_forbidden(tmp_path: Path) -> None:
    """Path.expanduser() raises a bare RuntimeError for '~nosuchuser/...'.
    That call happened before the try/except in the original code, so it
    could also escape uncaught; it must now be covered too."""
    with pytest.raises(LocalPathForbiddenError):
        resolve_local_path("~nonexistentuser99999xyz/x", [tmp_path])


@pytest.mark.parametrize(
    "hostile_path",
    [
        "a\x00b",
        "\x00",
        "~nonexistentuser99999xyz/x",
        "~nonexistentuser99999xyz",
        "",
        "   ",
        "\ud800",  # lone unpaired surrogate
        "😀" * 50,
        "/" * 5000,
        "a" * 100_000,
        "relative/sub/path",
        ".",
        "..",
    ],
)
def test_every_hostile_path_input_is_refused_with_an_upgradepilot_error(
    hostile_path: str, tmp_path: Path
) -> None:
    """The contract for this security boundary is that no input produces an
    exception other than UpgradePilotError. This is the test that makes
    that contract real rather than assumed: parametrized over inputs no one
    had tried yet (NUL bytes, unresolvable home-directory lookups, lone
    surrogates, pathological lengths).

    It used to assert that with `contextlib.suppress(UpgradePilotError)`,
    which made it a test that could not fail: suppression passes
    identically whether the input is refused or resolves and is returned as
    an allowlisted path. Every input below is unresolvable, malformed, or
    outside `tmp_path`, so every one of them must be refused —
    `pytest.raises` says so, and now goes red if one starts being granted.
    The URL half of this file had the identical hole and it was hiding a
    live blocking defect; see `_REJECTED` below.
    """
    with pytest.raises(UpgradePilotError):
        resolve_local_path(hostile_path, [tmp_path])


@pytest.mark.parametrize(
    "hostile_roots",
    [
        [],
        [Path("")],
        [Path(".")],
        [Path("relative/sub")],
        [Path("~nonexistentuser99999xyz")],
    ],
)
def test_every_hostile_root_configuration_is_refused_with_an_upgradepilot_error(
    hostile_roots: list[Path], tmp_path: Path
) -> None:
    """The same repair as the sweep above, for the same reason: every entry
    here is an empty or non-absolute allowlist, all of which must deny, so
    suppressing the exception hid the only outcome worth testing for — a
    misconfigured allowlist that grants."""
    with pytest.raises(UpgradePilotError):
        resolve_local_path(str(tmp_path), hostile_roots)


# --- Local path resolution: a relative/empty allowlist root is rejected (fix round 3) --


def test_rejects_an_empty_path_as_an_allowed_root(tmp_path: Path) -> None:
    """Path("") normalises to Path("."), which would otherwise resolve
    against the server process's current working directory in _is_within
    — silently granting the whole CWD tree. This is exactly what a naive
    `os.environ.get(...).split(",")` produces for an unset/blank setting:
    [''], not []."""
    with pytest.raises(LocalPathForbiddenError):
        resolve_local_path(str(tmp_path), [Path("")])


def test_rejects_a_dot_path_as_an_allowed_root(tmp_path: Path) -> None:
    with pytest.raises(LocalPathForbiddenError):
        resolve_local_path(str(tmp_path), [Path(".")])


def test_rejects_a_relative_path_as_an_allowed_root(tmp_path: Path) -> None:
    with pytest.raises(LocalPathForbiddenError):
        resolve_local_path(str(tmp_path), [Path("relative/sub")])


def test_accepts_a_valid_absolute_root_alongside_no_other_bad_entries(tmp_path: Path) -> None:
    """The fix must not reject a well-formed allowlist — only bad entries."""
    project = tmp_path / "demo"
    project.mkdir()
    assert resolve_local_path(str(project), [tmp_path]) == project.resolve()


def test_rejects_when_no_roots_are_configured_still_denies_everything(tmp_path: Path) -> None:
    """Regression guard: the literal empty list is the single most
    important line in the file, and this fix must not weaken it while
    fixing the near-empty (Path("")/Path(".")) cases above."""
    with pytest.raises(LocalPathForbiddenError):
        resolve_local_path(str(tmp_path), [])


def test_a_tilde_prefixed_root_is_still_accepted_not_flagged_as_misconfigured() -> None:
    """A relative-looking root must be rejected, but ~-prefixed roots are a
    legitimate, intentionally-supported configuration shape (_is_within
    already calls root.expanduser()) and must not be caught by the same
    guard as a bare relative path."""
    home = Path.home()
    project = home / "upgradepilot_test_tilde_root_probe"
    project.mkdir(exist_ok=True)
    try:
        result = resolve_local_path(str(project), [Path("~/upgradepilot_test_tilde_root_probe")])
        assert result.samefile(project)
    finally:
        project.rmdir()


# --- Local path resolution: the root guard's denial *reason* is asserted (fix round 4) --

_SERVER_LOOKING_ROOT = "some/relative/server/looking/path"


def test_a_misconfigured_root_is_never_echoed_into_the_user_facing_message(tmp_path: Path) -> None:
    """`message` is user-facing; `detail` is logged. A server-configured
    filesystem path must never appear in `message`, and the sibling
    `expanduser()` failure branch already gets this right.

    Every earlier test for this branch asserted on the exception type only,
    which is exactly how a server path in the `message` shipped. So assert
    both halves of the split: absent from `message`, present in `detail` —
    `detail` carrying the root is what makes the misconfiguration
    diagnosable and must not be "fixed" away along with the leak."""
    with pytest.raises(LocalPathForbiddenError) as excinfo:
        resolve_local_path(str(tmp_path), [Path(_SERVER_LOOKING_ROOT)])

    assert _SERVER_LOOKING_ROOT not in excinfo.value.message
    assert _SERVER_LOOKING_ROOT not in str(excinfo.value)
    assert _SERVER_LOOKING_ROOT in (excinfo.value.detail or "")


@pytest.mark.parametrize("root", [Path(""), Path(".")], ids=["empty", "dot"])
def test_a_relative_root_is_denied_as_misconfigured_even_when_the_cwd_contains_the_path(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The per-root absoluteness check was dead code under its own suite:
    deleting the whole loop turned 0 of 89 tests red, because every root
    test used `tmp_path` while pytest's CWD (`backend/`) is never an
    ancestor of `tmp_path`. `_is_within` therefore still denied a `Path(".")`
    root — for the coincidental reason "the CWD tree does not contain this
    path" rather than the intended reason "a relative root is
    misconfiguration". The two denials are indistinguishable from the
    exception type alone.

    This test removes the coincidence: with the CWD chdir'd to an ancestor
    of the candidate, a deleted loop silently ACCEPTS the path. And it
    asserts the denial *reason*, not merely the type, so a containment
    denial can never stand in for the misconfiguration denial.

    `Path("")` normalises to `Path(".")` so both reach the same branch; the
    empty case is the one that matters operationally, because that is what
    a naive `os.environ.get(...).split(",")` env-var loader produces for an
    unset or blank setting: `['']`, not `[]`.

    `monkeypatch.chdir` rather than bare `os.chdir` so the CWD is restored
    even when the assertion below fails."""
    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.chdir(tmp_path)

    with pytest.raises(LocalPathForbiddenError) as excinfo:
        resolve_local_path(str(project), [root])

    detail = excinfo.value.detail or ""
    assert "non-absolute root" in detail, (
        "denied for the wrong reason: expected the misconfigured-root branch, "
        f"got detail={detail!r}"
    )


# --- Error contract -------------------------------------------------------


def test_errors_expose_a_code_and_an_http_status() -> None:
    error = InvalidRepoUrlError("bad url", detail="scheme was ftp")
    assert error.http_status == 422
    assert LocalPathForbiddenError("no").http_status == 403


def test_error_converts_to_an_app_error_preserving_technical_detail() -> None:
    error = InvalidRepoUrlError("Repository URL is not valid.", detail="scheme=ftp host=x")
    app_error = error.to_app_error(node="analyze_repo")

    assert app_error.code is ErrorCode.INVALID_REPO_URL
    assert app_error.message == "Repository URL is not valid."
    assert app_error.detail == "scheme=ftp host=x"
    assert app_error.node == "analyze_repo"
    assert app_error.retryable is False


def test_base_error_is_catchable_as_one_type() -> None:
    with pytest.raises(UpgradePilotError):
        raise LocalPathForbiddenError("denied")


# --- URL validation: the hostile-input contract sweep (fix wave B, item 3) --

_REJECTED = True
"""This input must raise `UpgradePilotError`.

The table below carries an expected outcome per input for one reason:
without it the sweep was a test that could not fail. It asserted only
"nothing outside `UpgradePilotError` escapes", spelled
`contextlib.suppress(UpgradePilotError)`, which passes identically whether
the input is rejected or sails through and is returned as a validated,
clone-ready URL. Two of its own entries — the lone-surrogate pair, added
deliberately by the fix wave that had already identified the class — were
in fact being ACCEPTED, and the URL handed back then killed
`clone_repository` at `subprocess.run` with a bare `UnicodeEncodeError`.
The sweep watched that happen for a whole review cycle and stayed green.
"""

_ACCEPTED = False
"""This input looks hostile but is validly formed, and returning it is
correct: nothing in this module's contract promises `git` will like it.

Recording those as expectations rather than dropping them from the table
keeps the sweep honest in both directions — it goes red if one of them
starts being refused, so a fix for a hostile input cannot quietly widen
into over-rejection. They are still held to the encodability half of the
contract (`_assert_the_os_can_be_handed_this`), which is the half the
surrogates broke.
"""

_HOSTILE_URLS = [
    pytest.param("http://[::1", _REJECTED, id="unclosed_ipv6_bracket"),
    pytest.param("https://[::1", _REJECTED, id="unclosed_ipv6_bracket_https"),
    pytest.param("https://[::1]junk/x", _REJECTED, id="trailing_junk_after_ipv6_literal"),
    pytest.param("https://[]/x", _REJECTED, id="empty_ipv6_literal"),
    pytest.param("https://[/x", _REJECTED, id="lone_open_bracket"),
    pytest.param("https://]/x", _REJECTED, id="lone_close_bracket"),
    pytest.param("https://\u2100.com/x", _REJECTED, id="nfkc_decomposing_So_in_netloc"),
    pytest.param("https://\uff03/x", _REJECTED, id="nfkc_decomposing_Po_in_netloc"),
    pytest.param("https://a\u2100b/x", _REJECTED, id="nfkc_decomposing_interior"),
    pytest.param("file://\u2100/x", _REJECTED, id="nfkc_decomposing_netloc_file_scheme"),
    pytest.param("https://github.com:notaport/x", _ACCEPTED, id="non_numeric_port"),
    pytest.param("https://[v1.fe80::a]/x", _ACCEPTED, id="ipvfuture_literal"),
    pytest.param("https://\ud800/x", _REJECTED, id="lone_surrogate_in_netloc"),
    pytest.param("\ud800", _REJECTED, id="lone_surrogate_alone"),
    pytest.param("https://github.com/a\ud800b", _REJECTED, id="lone_surrogate_in_path"),
    pytest.param("https://github.com/a\udc80b", _REJECTED, id="fsencodable_surrogate_in_path"),
    pytest.param("https://github.com/a\udfffb", _REJECTED, id="top_of_surrogate_range_in_path"),
    pytest.param("https://user@@github.com/x", _REJECTED, id="double_at_in_authority"),
    pytest.param("https://%00/x", _ACCEPTED, id="percent_encoded_nul_in_netloc"),
    pytest.param("https://xn--/x", _ACCEPTED, id="malformed_punycode"),
    pytest.param("https://github.com/x#\u2100", _ACCEPTED, id="nfkc_decomposing_in_fragment"),
    pytest.param("", _REJECTED, id="empty"),
    pytest.param("   ", _REJECTED, id="only_ascii_space"),
    pytest.param(":", _REJECTED, id="bare_colon"),
    pytest.param("://x", _REJECTED, id="scheme_separator_only"),
    pytest.param("//github.com/x", _REJECTED, id="protocol_relative"),
    pytest.param("https:", _REJECTED, id="scheme_only"),
    pytest.param("https://", _REJECTED, id="scheme_and_separator_only"),
    pytest.param("\U0001f600" * 50, _REJECTED, id="emoji_run"),
    pytest.param("/" * 5000, _REJECTED, id="slash_run_over_the_length_cap"),
    pytest.param("a" * 100_000, _REJECTED, id="hundred_thousand_characters"),
    pytest.param(
        "https://github.com/" + "a" * 3000, _REJECTED, id="valid_shape_over_the_length_cap"
    ),
]


def _assert_the_os_can_be_handed_this(url: str) -> None:
    """Fail unless `url` is a string `subprocess` could pass to `execve`
    unchanged.

    The second half of `validate_clone_url`'s contract, and the half that
    was never asserted: an accepted URL becomes an argv entry, so being
    well-formed is not enough — it has to survive encoding, and encode to
    bytes that read back as the same string.

    Two distinct failures live here, both from the surrogate class, and
    both invisible to a test that only checks the exception type. They need
    two separate detectors because they fail at different steps:

    - most surrogates cannot be encoded at all, so `os.fsencode` raises —
      which is precisely the exception that escaped `clone_repository`
      uncaught, so it is re-raised as an assertion rather than allowed to
      look like a broken test;
    - U+DC80–U+DCFF encode *successfully*. `os.fsencode`'s
      `surrogateescape` handler turns them back into the raw bytes
      0x80–0xFF, so `git` is handed bytes that are not the string that was
      validated — no exception, no crash, nothing in the log. Only the
      round-trip comparison catches that one.

    The decode below uses `errors="replace"` on purpose: a detector needs a
    value to compare, not an exception of its own. Both branches are
    demonstrated to fire — see the two surrogate tests below, either of
    which reaches this helper if its expectation is flipped to `_ACCEPTED`.
    """
    try:
        argv_bytes = os.fsencode(url)
    except UnicodeEncodeError as exc:
        raise AssertionError(
            f"{url!r} cannot be encoded for execve: subprocess.run raises "
            f"{type(exc).__name__} on this URL instead of cloning it"
        ) from exc

    assert argv_bytes.decode("utf-8", errors="replace") == url, (
        f"the bytes git would receive are not {url!r}: os.fsencode produced "
        f"{argv_bytes!r}, which is a different string"
    )


@pytest.mark.parametrize(("hostile_url", "expectation"), _HOSTILE_URLS)
def test_every_hostile_url_input_is_refused_or_returns_a_url_the_os_can_be_handed(
    hostile_url: str, expectation: bool
) -> None:
    """The counterpart the path side had and the URL side did not.

    `resolve_local_path` has had a hostile-input sweep since fix round 3;
    `validate_clone_url` had none, and the module docstring's contract --
    that no `str` input raises anything outside the `UpgradePilotError`
    hierarchy -- was therefore false and nothing noticed. `urlsplit` raises
    a bare `ValueError` for a malformed IPv6 literal and for a netloc
    containing a character that decomposes under NFKC, and neither is
    caught by the category rule: brackets are Ps/Pe and the NFKC
    troublemakers are So/Po, none of which are in `_DISALLOWED_CATEGORIES`.

    The consequence was not cosmetic: a user typo became an unhandled
    exception, i.e. an HTTP 500 whose traceback carried the raw URL -- at a
    point in the function where the URL has NOT yet been credential-
    screened.

    The missing sweep is the actual defect. The inputs below are its
    symptoms, so this is written as the contract, over every shape anyone
    has thought to try, and it must be extended rather than replaced when
    the next one turns up.

    Repaired: the assertion used to be `contextlib.suppress`, which is not
    an assertion. Each input now declares `_REJECTED` or `_ACCEPTED` and
    this test holds it to that, so a hostile input that sails through is a
    failure rather than a pass — see `_REJECTED` for what that hid — and
    whatever is accepted is additionally held to being encodable, which is
    what "safe to hand to `git clone`" actually requires.
    """
    try:
        returned = validate_clone_url(hostile_url, DEFAULT_SCHEMES | frozenset({"file"}))
    except UpgradePilotError:
        assert expectation is _REJECTED, (
            f"{hostile_url!r} is validly formed and was refused; over-rejection is "
            "a regression too, see _ACCEPTED"
        )
        return

    assert expectation is _ACCEPTED, (
        f"validate_clone_url ACCEPTED a hostile input and returned {returned!r}; "
        "it must raise an UpgradePilotError instead"
    )
    _assert_the_os_can_be_handed_this(returned)


@pytest.mark.parametrize(("hostile_url", "expectation"), _HOSTILE_URLS)
def test_no_hostile_url_rejection_echoes_the_input_into_a_logged_detail(
    hostile_url: str,
    expectation: bool,
) -> None:
    """Every rejection above must also stay credential-safe.

    A URL is unscreened until the credentials check, which most of these
    inputs never reach, so no rejection along the way may echo the input.
    `urlsplit`'s own NFKC error message quotes the entire netloc -- which is
    exactly where userinfo lives -- so the tempting `detail=f"{exc}"` would
    have reopened the leak that fix round 1 closed.

    `expectation` is unused here: this test shares `_HOSTILE_URLS` with the
    sweep above, which needs the per-input outcome, and one table over one
    set of inputs is worth an unused parameter.
    """
    try:
        validate_clone_url(hostile_url, DEFAULT_SCHEMES | frozenset({"file"}))
    except UpgradePilotError as error:
        detail = error.detail or ""
        if hostile_url:
            assert hostile_url not in detail
        if len(hostile_url) > 12:
            assert hostile_url[:12] not in detail
        assert len(detail) < 200, f"detail is not bounded: {len(detail)} characters"


# --- URL validation: the length cap has teeth (fix wave B, item 5.1) --


def test_a_url_over_the_length_cap_is_rejected_even_when_otherwise_valid() -> None:
    """The cap turned zero tests red when deleted.

    Every other over-long input in this file is malformed for some second
    reason, so the cap was never the thing doing the rejecting. This URL is
    well-formed in every other respect -- valid scheme, real host, no
    credentials, no disallowed characters, parses and round-trips -- so the
    cap is the only check that can refuse it. It matters because this is
    the only bound on the URL that `clone.py` writes into a logged
    `detail`.
    """
    url = "https://github.com/acme/" + "a" * 3000
    with pytest.raises(InvalidRepoUrlError) as excinfo:
        validate_clone_url(url, DEFAULT_SCHEMES)

    assert excinfo.value.detail == f"length={len(url)}"
    assert "aaaa" not in (excinfo.value.detail or ""), "the over-long URL must not be echoed"


def test_a_url_at_the_length_cap_is_accepted() -> None:
    """The cap is an inclusive maximum, so the boundary is pinned on both
    sides: the test above proves it rejects, this one proves it does not
    reject one character early."""
    prefix = "https://github.com/acme/"
    url = prefix + "a" * (2048 - len(prefix))
    assert len(url) == 2048
    assert validate_clone_url(url, DEFAULT_SCHEMES) == url


# --- URL validation: the file:// missing-path rejection has teeth (item 5.2) --


@pytest.mark.parametrize("url", ["file://", "file://localhost"], ids=["no_host", "localhost"])
def test_a_file_url_with_no_path_is_rejected(url: str) -> None:
    """Deleting this guard turned zero tests red, and it is the only call
    site of `_redact` -- so that function's "no code path may place an
    un-redacted URL into a detail" claim had no test exercising it in
    place either.

    `file://` and `file://localhost` are the two shapes that satisfy every
    earlier check (scheme allowed, host legitimately absent for file, no
    credentials) and still name nothing at all. Without this guard they are
    returned as valid clone URLs.
    """
    with pytest.raises(InvalidRepoUrlError) as excinfo:
        validate_clone_url(url, frozenset({"file"}))
    assert "path" in excinfo.value.message.lower()
    assert (excinfo.value.detail or "").startswith("url=")


def test_the_file_missing_path_detail_is_redacted() -> None:
    """`_redact` is called at that raise site defensively -- credentials are
    rejected earlier, so it cannot fire through the public function today.
    Assert the property the docstring claims by driving `_redact` with the
    string that site would hand it, so the claim is tested rather than
    asserted."""
    assert _redact("file://user:tok@") == "file://***@"


# --- URL validation: normalisation is the contract, byte-identity is not (item 7) --


def test_an_already_normal_url_is_returned_byte_identical() -> None:
    """Byte-identity holds for a URL that needs no normalisation, and this
    pins that narrow case only.

    It used to be named as though byte-identity were the general property
    of `validate_clone_url`, while exercising nothing but an already-normal
    URL. That framing invites the parser differential this module has now
    fixed three times: the two accepted normalisations (ASCII edge
    stripping, scheme lower-casing) are each *deliberately* not
    byte-identical, and a future contributor reading a byte-identity
    property test would be led to remove them -- or worse, to "fix" a
    failure by returning `raw` instead of the validated candidate, which is
    the original defect exactly.

    The general property is idempotence, tested below.
    """
    raw = "https://github.com/acme/payment-service.git"
    assert validate_clone_url(raw, DEFAULT_SCHEMES) == raw


@pytest.mark.parametrize(
    "raw",
    [
        "https://github.com/acme/repo",
        "  https://github.com/acme/repo  ",
        "HTTPS://GitHub.com/Acme/Repo",
        "  HTTPS://GitHub.com/Acme/Repo  ",
    ],
)
def test_validation_is_idempotent_so_the_returned_url_is_final(raw: str) -> None:
    """The real general property, and the one a caller depends on:
    re-validating the returned value returns it unchanged. If it did not,
    the returned string would not be fully normalised and `clone.py` would
    be handing git something this function has not settled on."""
    once = validate_clone_url(raw, DEFAULT_SCHEMES)
    assert validate_clone_url(once, DEFAULT_SCHEMES) == once


# --- Local path resolution: a path is never silently substituted (item 1) --


def test_an_interior_nbsp_is_rejected_not_stripped_into_a_different_directory(
    tmp_path: Path,
) -> None:
    """The blocking defect, as a test: two real directories whose names
    differ only by a trailing NBSP.

    `Path(raw.strip())` -- a bare strip, the exact anti-pattern the URL
    half of this module spends fifteen lines condemning -- removed the NBSP
    and resolved the *other* directory. Reproduced before the fix: asking
    for `repo\\xa0` returned `repo` and listed `repo`'s contents, with no
    error. Every file path, line number and sha the product cited
    afterwards would then belong to a repository the caller never named,
    which is this product's central failure mode occurring silently.

    What actually gives this test teeth, corrected. In the shipped code the
    CATEGORY check is what rejects this input, and it needs neither the
    sibling nor the ambiguity refusal — verified by creating the NBSP
    directory alone, with no sibling, and getting `category='Zs'` back.
    That is why the assertion below is on the category and not merely on
    the exception type: the category is the rule, and the rule is the thing
    under test.

    Against a bare-`.strip()` regression the test goes red either way, but
    by two different routes, and the earlier docstring named neither:

    - with the sibling present, the stripped name resolves to a real
      directory inside the root, so the AMBIGUITY REFUSAL fires ("remove
      the whitespace") — the detail says `edge whitespace stripped`;
    - with the sibling absent, the stripped name resolves to nothing, so
      the refusal is the plain "does not exist".

    Both go red on the `category='Zs'` assertion, so the sibling is not
    what gives this test teeth. The earlier claim — that without the
    sibling a bare strip "would fail with 'does not exist' and this test
    would pass while the defect stood" — got the mechanism right and the
    conclusion wrong: that refusal fails this test too.

    The sibling is kept for the thing it does do: it makes the original
    defect's consequence concrete rather than hypothetical. A bare strip
    did not merely fail, it opened this directory and offered
    `NOT_THE_REQUESTED_REPO.py` as the caller's repository. Deleting the
    sibling would not weaken the assertion; deleting the category check or
    the ambiguity refusal would, and each has its own test saying so.
    """
    named = tmp_path / "repo\xa0"
    sibling = tmp_path / "repo"
    named.mkdir()
    sibling.mkdir()
    (sibling / "NOT_THE_REQUESTED_REPO.py").write_text("x = 1\n")

    with pytest.raises(LocalPathForbiddenError) as excinfo:
        resolve_local_path(str(named), [tmp_path])

    assert "category='Zs'" in (excinfo.value.detail or "")


@pytest.mark.parametrize(
    ("name", "invisible"),
    [
        ("nbsp_U+00A0", "\xa0"),
        ("nel_U+0085", "\x85"),
        ("zero_width_space_U+200B", "\u200b"),
        ("ogham_space_mark_U+1680", "\u1680"),
        ("en_quad_U+2000", "\u2000"),
        ("line_separator_U+2028", "\u2028"),
        ("paragraph_separator_U+2029", "\u2029"),
        ("tab_U+0009", "\t"),
    ],
)
def test_no_invisible_codepoint_can_substitute_one_directory_for_another(
    name: str, invisible: str, tmp_path: Path
) -> None:
    """The whole class, not just the NBSP that was reported.

    `str.strip()` removes seventeen codepoints. Each one below sits in the
    *interior* of the name -- where no reading of "tolerate a paste slip"
    justifies removing it -- and each has a real sibling directory that a
    strip would resolve to instead. The invariant asserted is the strong
    one: either the call raises, or it returns the directory that was
    asked for. Silently returning the sibling is the only forbidden
    outcome.
    """
    named = tmp_path / f"a{invisible}b"
    sibling = tmp_path / "ab"
    named.mkdir()
    sibling.mkdir()

    try:
        resolved = resolve_local_path(str(named), [tmp_path])
    except LocalPathForbiddenError:
        return
    assert resolved.samefile(named), (
        f"{name} was stripped and the wrong directory was returned: {resolved}"
    )


@pytest.mark.parametrize("name", ["My Documents", "Café Projects", "a-b_c.d", "a b c"])
def test_ordinary_real_paths_are_still_accepted(name: str, tmp_path: Path) -> None:
    """Spaces and accents are normal in real paths, and the fix must not
    make them collateral damage. An interior ASCII space is category Zs,
    the same category as the NBSP rejected above -- which is why the shared
    category rule exempts U+0020 specifically, and only for paths: in a URL
    a raw space must be percent-encoded, while in a filename it is an
    ordinary legal character with no encoded alternative."""
    project = tmp_path / name
    project.mkdir()
    assert resolve_local_path(str(project), [tmp_path]) == project.resolve()


def test_edge_ascii_whitespace_is_stripped_so_a_pasted_path_still_works(tmp_path: Path) -> None:
    """The one tolerated normalisation, matching `validate_clone_url`: a
    leading or trailing ASCII space around a pasted path is a plausible
    caller slip, unlike an interior NBSP."""
    project = tmp_path / "demo"
    project.mkdir()
    assert resolve_local_path(f"  {project}\t", [tmp_path]) == project.resolve()


def test_a_path_whose_literal_form_also_exists_is_refused_not_guessed(tmp_path: Path) -> None:
    """The residual substitution that edge-stripping would otherwise leave.

    A trailing space is legal in a POSIX filename, so `demo ` and `demo`
    can both be real, different directories. Stripping the space then picks
    one silently -- the same defect class as the NBSP above, just reached
    through the tolerated normalisation instead of the forbidden one. When
    both readings exist the request is genuinely ambiguous, so it is
    refused and the caller is told which whitespace to remove.
    """
    spaced = tmp_path / "demo "
    plain = tmp_path / "demo"
    spaced.mkdir()
    plain.mkdir()

    with pytest.raises(LocalPathForbiddenError) as excinfo:
        resolve_local_path(str(spaced), [tmp_path])

    assert "whitespace" in excinfo.value.message.lower()
    assert "edge whitespace stripped" in (excinfo.value.detail or "")


# --- Local path resolution: no unbounded input in a logged detail (item 6) --


def test_a_path_over_the_length_cap_is_rejected_reporting_only_its_length(
    tmp_path: Path,
) -> None:
    """A 100k-character path produced a 100,047-character `detail`, which is
    logged. The existing hostile-input sweep parametrises exactly this
    input but asserts only the exception type, so it never caught it."""
    with pytest.raises(LocalPathForbiddenError) as excinfo:
        resolve_local_path("a" * 100_000, [tmp_path])

    assert excinfo.value.detail == "length=100000"
    assert "aaaa" not in (excinfo.value.detail or "")


def test_a_long_but_legal_path_is_echoed_bounded_rather_than_dropped(tmp_path: Path) -> None:
    """For "that path does not exist" the path is the operator's most
    useful datum -- it is server-side, thread_id-correlated, and unlike a
    URL a local path has no standardised credential slot -- so it is
    echoed. The defect was the unboundedness, not the echo, so this
    asserts both halves: the leaf name survives (the detail is still
    diagnostic) and the whole thing stays short.
    """
    missing = tmp_path / ("b" * 3000) / "distinctive-leaf"
    with pytest.raises(LocalPathForbiddenError) as excinfo:
        resolve_local_path(str(missing), [tmp_path])

    detail = excinfo.value.detail or ""
    assert "distinctive-leaf" in detail
    assert "tail of" in detail, "truncation must be announced, not silent"
    assert len(detail) < 400, f"detail is not bounded: {len(detail)} characters"


def test_a_short_missing_path_is_echoed_in_full_with_the_error_type(tmp_path: Path) -> None:
    """Under the budget nothing is truncated, and the exception's *type* is
    what accompanies the path. `repr(exc)` on an OSError re-embeds the
    filename, which would put the unbounded path back into the very string
    the budget just bounded."""
    missing = tmp_path / "nope"
    with pytest.raises(LocalPathForbiddenError) as excinfo:
        resolve_local_path(str(missing), [tmp_path])

    detail = excinfo.value.detail or ""
    assert str(missing) in detail
    assert "FileNotFoundError" in detail


# --- The surrogate class: valid Python, invalid UTF-8 (wave B residual, item 1) --


def test_a_lone_surrogate_in_a_url_is_refused_naming_its_category() -> None:
    """The blocking residual, as a test.

    `unicodedata.category("\\ud800")` is `"Cs"`, which was not in
    `_DISALLOWED_CATEGORIES`, so this URL passed every check, was returned
    as a validated clone URL, and then killed `clone_repository` at
    `subprocess.run` with an uncaught `UnicodeEncodeError` -- a non-
    `UpgradePilotError` escaping the boundary, i.e. an HTTP 500 on a URL
    this module had just called safe.

    Reachability is asserted rather than asserted about: `json` decodes the
    escape `"\\ud800"` in a request body into exactly this string, so the
    input costs an HTTP client nothing to produce.

    The assertion is on the category, not merely on the exception type,
    because the rule is what matters: rejected *because* it is a surrogate,
    at the boundary, with the reason in the `detail` an operator will read.
    """
    assert json.loads('"\\ud800"') == "\ud800", (
        "the reachability premise is wrong if this fails: a JSON body cannot "
        "produce a lone surrogate"
    )

    with pytest.raises(InvalidRepoUrlError) as excinfo:
        validate_clone_url("https://github.com/acme/\ud800repo", DEFAULT_SCHEMES)

    assert "category='Cs'" in (excinfo.value.detail or "")


def test_a_surrogate_the_os_would_smuggle_as_a_raw_byte_is_refused() -> None:
    """U+DC80-U+DCFF are the half that does NOT crash, which is what makes
    them the dangerous half.

    `os.fsencode`'s `surrogateescape` handler exists to round-trip
    undecodable filesystem bytes, so it turns these 128 codepoints straight
    back into the raw bytes 0x80-0xFF. `subprocess` would therefore have
    handed `git` an argument whose bytes are not the string that was
    validated -- the fifth validate-one-string-use-another differential in
    this module, and the first one located in an encoder rather than a
    parser. No exception, no crash, no log line: `git` simply clones
    something else.

    The presence is demonstrated before the absence is asserted, so the
    guard below is known to be guarding something real.
    """
    assert os.fsencode("\udc80") == b"\x80", (
        "the smuggling premise is wrong if this fails: surrogateescape did not "
        "turn the surrogate back into a raw byte"
    )

    with pytest.raises(InvalidRepoUrlError) as excinfo:
        validate_clone_url("https://github.com/acme/\udc80repo", DEFAULT_SCHEMES)

    assert "category='Cs'" in (excinfo.value.detail or "")


def test_a_surrogate_in_a_local_path_is_refused_by_the_same_rule_as_in_a_url(
    tmp_path: Path,
) -> None:
    """Both doors, one rule -- the asymmetry that keeps recurring here.

    The path side already refused `"\\ud800"`, but only incidentally:
    `Path.resolve(strict=True)` failed with a `UnicodeEncodeError` raised
    deep inside `realpath` and caught by the broad `except` clause, which is
    an accident of one codepoint's behaviour rather than a rule -- and an
    accident that says nothing whatever about U+DC80, whose bytes
    `os.fsencode` produces happily.

    Asserting the category rather than the exception type is what makes this
    a parity test: it now refuses for the stated reason, at the same check,
    with the same `detail` shape as the URL door.
    """
    for surrogate in ("\ud800", "\udc80", "\udfff"):
        with pytest.raises(LocalPathForbiddenError) as excinfo:
            resolve_local_path(f"{tmp_path}/a{surrogate}b", [tmp_path])
        assert "category='Cs'" in (excinfo.value.detail or ""), (
            f"{surrogate!r} was not refused by the category rule"
        )


def test_every_codepoint_that_cannot_encode_as_utf8_is_in_a_disallowed_category() -> None:
    """The class, asserted exhaustively, rather than the codepoint that was
    reported.

    The question worth answering was not "is `Cs` handled" but "which
    Unicode categories can hold a codepoint that is legal in a Python `str`
    and has no UTF-8 encoding". Scanning all 0x110000 codepoints answers it
    once and for all: exactly one category, `Cs`, and all 2048 of it. This
    runs in about a tenth of a second, which is a cheap price for never
    having to reason about the class again -- and it goes red rather than
    silently stale if a future Unicode or CPython revision widens it.
    """
    offending_categories = set()
    offending_count = 0
    for codepoint in range(0x110000):
        character = chr(codepoint)
        try:
            character.encode("utf-8")
        except UnicodeEncodeError:
            offending_categories.add(unicodedata.category(character))
            offending_count += 1

    assert offending_categories == {"Cs"}, (
        "the non-encodable class is no longer exactly the surrogates; "
        "_DISALLOWED_CATEGORIES needs widening"
    )
    assert offending_count == 0xE000 - 0xD800
    assert offending_categories <= _DISALLOWED_CATEGORIES


# --- Local path resolution: the allowlist is consulted first (wave B item 2) --


def test_the_ambiguity_refusal_never_probes_a_path_the_allowlist_has_not_approved(
    tmp_path: Path,
) -> None:
    """The ambiguity refusal ran before the containment check, which made it
    a filesystem-existence oracle for paths outside every allowed root.

    Three distinguishable answers came back for one out-of-bounds target,
    selected purely by what exists next to it on disk: "does not exist",
    "outside the allowed directories", and -- once a whitespace-suffixed
    entry existed beside it -- "remove the whitespace". The third answer is
    information about a path this function would refuse to open, obtained
    from a `stat` the allowlist never authorised.

    So the ordering is the thing under test: the refusal for an
    out-of-bounds target must be the allowlist's own, and must not vary
    with the existence of the probed sibling.

    The last three assertions exist because the "whitespace" check is a
    negative one, and a negative assertion is worthless until it is shown to
    detect a presence: the same detector, on the same wording, fires for an
    identically-shaped request whose target is INSIDE the allowlist. If the
    ordering regresses, the middle assertion goes red; if the detector ever
    stops matching the refusal it is looking for, the last-but-one does.
    """
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "secret"
    target.mkdir()
    requested = f"{target} "  # trailing ASCII space: stripping changes the string

    with pytest.raises(LocalPathForbiddenError) as without_decoy:
        resolve_local_path(requested, [allowed])

    (outside / "secret ").mkdir()  # the whitespace-suffixed sibling, out of bounds

    with pytest.raises(LocalPathForbiddenError) as with_decoy:
        resolve_local_path(requested, [allowed])

    assert without_decoy.value.message == with_decoy.value.message, (
        "the refusal for an out-of-bounds target changed when a file appeared "
        "outside the allowlist: that is the oracle"
    )
    assert without_decoy.value.detail == with_decoy.value.detail
    assert "outside the allowed directories" in with_decoy.value.message

    in_bounds = allowed / "secret"
    in_bounds.mkdir()
    (allowed / "secret ").mkdir()
    with pytest.raises(LocalPathForbiddenError) as ambiguous:
        resolve_local_path(f"{in_bounds} ", [allowed])

    assert "whitespace" in ambiguous.value.message.lower(), (
        "detector check: the ambiguity refusal must still be recognisable by "
        "this word, or the assertion below proves nothing"
    )
    assert "whitespace" not in with_decoy.value.message.lower()
