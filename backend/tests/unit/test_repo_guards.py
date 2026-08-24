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
