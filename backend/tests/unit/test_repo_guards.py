import contextlib
import unicodedata
from pathlib import Path

import pytest

from upgradepilot.models.errors import (
    ErrorCode,
    InvalidRepoUrlError,
    LocalPathForbiddenError,
    UpgradePilotError,
)
from upgradepilot.services.repo.guards import _redact, resolve_local_path, validate_clone_url

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
def test_every_hostile_path_input_raises_only_upgradepilot_error(
    hostile_path: str, tmp_path: Path
) -> None:
    """The contract for this security boundary is that no input produces an
    exception other than UpgradePilotError. This is the test that makes
    that contract real rather than assumed: parametrized over inputs no one
    had tried yet (NUL bytes, unresolvable home-directory lookups, lone
    surrogates, pathological lengths), asserting each one either resolves
    or raises UpgradePilotError — nothing else is an acceptable outcome."""
    with contextlib.suppress(UpgradePilotError):
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
def test_every_hostile_root_configuration_raises_only_upgradepilot_error(
    hostile_roots: list[Path], tmp_path: Path
) -> None:
    with contextlib.suppress(UpgradePilotError):
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
