from pathlib import Path

import pytest
from pydantic import ValidationError
from pydantic_settings import SettingsConfigDict

from upgradepilot.config import Settings


def test_comma_separated_env_values_parse_into_collections(monkeypatch) -> None:
    """Complex-typed env values are JSON-decoded unless NoDecode is set."""
    monkeypatch.setenv("UP_ALLOWED_LOCAL_ROOTS", "/tmp/a,/tmp/b")
    monkeypatch.setenv("UP_ALLOWED_URL_SCHEMES", "https,git")
    monkeypatch.setenv("UP_CORS_ORIGINS", "http://localhost:5173")

    settings = Settings(_env_file=None)

    assert settings.allowed_local_roots == (Path("/tmp/a"), Path("/tmp/b"))
    assert settings.allowed_url_schemes == frozenset({"https", "git"})
    assert settings.cors_origins == ("http://localhost:5173",)


def test_api_key_is_read_without_the_up_prefix(monkeypatch) -> None:
    """An explicit alias bypasses env_prefix."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-unprefixed")
    monkeypatch.delenv("UP_OPENAI_API_KEY", raising=False)
    assert Settings(_env_file=None).openai_configured is True

    monkeypatch.delenv("OPENAI_API_KEY")
    monkeypatch.setenv("UP_OPENAI_API_KEY", "sk-prefixed")
    assert Settings(_env_file=None).openai_configured is False


def test_explicit_kwargs_override_the_environment(monkeypatch) -> None:
    """Relied on by every test that builds a Settings by hand."""
    monkeypatch.setenv("UP_MAX_REPO_FILES", "7")
    assert Settings(_env_file=None).max_repo_files == 7
    assert Settings(_env_file=None, max_repo_files=99).max_repo_files == 99


# --- Path-typed settings: an empty variable must not become the CWD --------


@pytest.mark.parametrize(
    "setting",
    ["UP_WORKSPACE_DIR", "UP_CHROMA_DIR", "UP_CHECKPOINT_DB"],
)
@pytest.mark.parametrize("value", ["", "   ", ".", "./", "..", "../elsewhere"], ids=repr)
def test_a_blank_or_cwd_shaped_path_setting_is_rejected(
    monkeypatch: pytest.MonkeyPatch, setting: str, value: str
) -> None:
    """`UP_WORKSPACE_DIR=` yielded `Path('.')`, so `sweep_stale` treated the
    process working directory as the workspace root. Demonstrated data loss: a
    directory `repo-users-important-work/` containing `thesis.txt` in a temp
    CWD was matched as stale and removed with `rmtree`.

    Applied to every Path-typed setting, not just the one whose failure mode
    is `rmtree` -- treating one instance as the whole class is how this defect
    reached its fifth appearance.
    """
    monkeypatch.setenv(setting, value)
    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=None)
    assert setting.removeprefix("UP_").lower() in str(excinfo.value).lower()


def test_a_blank_path_setting_is_reported_as_blank_not_as_dot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The value-shape validators would reject a blank setting anyway, so the
    blank check exists for the message. An operator who exported
    `UP_WORKSPACE_DIR=` must not be told the problem is `'.'` -- they never
    wrote that and cannot find it in their configuration."""
    monkeypatch.setenv("UP_WORKSPACE_DIR", "")

    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=None)

    message = str(excinfo.value)
    assert "must not be blank" in message
    assert "unset the variable" in message
    assert "got '.'" not in message


def test_the_shipped_relative_defaults_still_load(monkeypatch: pytest.MonkeyPatch) -> None:
    """The rule rejects the CWD itself, not every relative path: a relative
    subdirectory is what the shipped defaults have always meant, and it cannot
    collapse to the CWD. Requiring absolute paths would leave no setting with
    a working default."""
    for name in ("UP_WORKSPACE_DIR", "UP_CHROMA_DIR", "UP_CHECKPOINT_DB"):
        monkeypatch.delenv(name, raising=False)

    settings = Settings(_env_file=None)

    assert settings.workspace_dir == Path("./.workspaces")
    assert settings.chroma_dir == Path("./.chroma")
    assert settings.checkpoint_db == Path("./checkpoints.db")


def test_an_ordinary_relative_or_absolute_path_is_accepted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("UP_WORKSPACE_DIR", str(tmp_path / "My Documents" / "work"))
    monkeypatch.setenv("UP_CHROMA_DIR", "var/chroma")

    settings = Settings(_env_file=None)

    assert settings.workspace_dir == tmp_path / "My Documents" / "work"
    assert settings.chroma_dir == Path("var/chroma")


def test_a_path_with_a_blank_component_is_rejected() -> None:
    """Only-whitespace is a legal directory name and never an intended one.
    Reachable programmatically, where the before-validator sees a Path rather
    than the original string."""
    with pytest.raises(ValidationError):
        Settings(_env_file=None, workspace_dir=Path("   "))


# --- Collection-typed settings --------------------------------------------


def test_allowed_local_roots_rejects_a_relative_entry(tmp_path: Path) -> None:
    """Fails at startup rather than on the first request that exercises it.
    `guards.py` still refuses these at use time and must keep doing so -- it
    is the security boundary and is reachable without passing through
    Settings. This is an earlier failure, not a replacement."""
    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=None, allowed_local_roots=(tmp_path, Path("relative/root")))
    assert "absolute" in str(excinfo.value)


def test_allowed_local_roots_rejects_a_blank_entry() -> None:
    """`Path("")` is `Path(".")`: an allowlist entry that grants the whole
    working directory tree."""
    with pytest.raises(ValidationError):
        Settings(_env_file=None, allowed_local_roots=(Path(""),))


def test_settings_still_accept_real_tuples_and_frozensets(tmp_path: Path) -> None:
    """Every test that builds a Settings by hand relies on this: `_split_csv`
    guards on `isinstance(value, str)`, so tuples and frozensets pass through
    untouched."""
    settings = Settings(
        _env_file=None,
        allowed_local_roots=(tmp_path,),
        allowed_url_schemes=frozenset({"file"}),
        workspace_dir=tmp_path / "workspaces",
        cors_origins=("http://localhost:5173",),
    )

    assert settings.allowed_local_roots == (tmp_path,)
    assert settings.allowed_url_schemes == frozenset({"file"})
    assert settings.cors_origins == ("http://localhost:5173",)


def test_a_blank_element_of_an_allowlist_is_rejected() -> None:
    """An empty string in `allowed_url_schemes` would be compared against
    `urlsplit()`'s empty scheme for an input that has no scheme at all."""
    with pytest.raises(ValidationError):
        Settings(_env_file=None, allowed_url_schemes=frozenset({"https", "  "}))
    with pytest.raises(ValidationError):
        Settings(_env_file=None, cors_origins=("http://localhost:5173", ""))


def test_split_csv_drops_blank_parts(monkeypatch: pytest.MonkeyPatch) -> None:
    """A trailing comma, a doubled comma or a lone comma is what a hand-edited
    .env looks like. Without the filter each one becomes a blank element --
    `Path("")`, i.e. `Path(".")`, in the local-root allowlist."""
    monkeypatch.setenv("UP_ALLOWED_LOCAL_ROOTS", "/tmp/a,,/tmp/b,")
    monkeypatch.setenv("UP_ALLOWED_URL_SCHEMES", "https, ,git")
    monkeypatch.setenv("UP_CORS_ORIGINS", ",http://localhost:5173,")

    settings = Settings(_env_file=None)

    assert settings.allowed_local_roots == (Path("/tmp/a"), Path("/tmp/b"))
    assert settings.allowed_url_schemes == frozenset({"https", "git"})
    assert settings.cors_origins == ("http://localhost:5173",)


# --- The API key ----------------------------------------------------------


def test_the_api_key_is_not_in_the_settings_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    """`repr(settings)` is what reaches a log line, a traceback frame and a
    pytest failure report. As a plain `str` the key was in all three."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-do-not-leak-me")

    settings = Settings(_env_file=None)

    assert "sk-do-not-leak-me" not in repr(settings)
    assert "sk-do-not-leak-me" not in str(settings)
    assert settings.openai_api_key is not None
    assert settings.openai_api_key.get_secret_value() == "sk-do-not-leak-me"


def test_an_empty_api_key_is_not_reported_as_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """`OPENAI_API_KEY=` gives a SecretStr wrapping "", and a SecretStr is an
    object -- reading it as truthy would report a key that is not there."""
    monkeypatch.setenv("OPENAI_API_KEY", "")
    assert Settings(_env_file=None).openai_configured is False

    monkeypatch.setenv("OPENAI_API_KEY", "   ")
    assert Settings(_env_file=None).openai_configured is False


# --- clone_depth ----------------------------------------------------------


def test_clone_depth_cannot_be_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """`clone.py` clamps with `max(1, depth)`, so a configured 0 became a
    depth-1 clone and silently destroyed the churn signal that depth exists
    to provide. The clamp is a reasonable last-ditch defence; refusing the
    value belongs here."""
    monkeypatch.setenv("UP_CLONE_DEPTH", "0")
    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=None)
    errors = excinfo.value.errors()
    assert any(e["loc"] == ("clone_depth",) and e["type"] == "greater_than_equal" for e in errors)


def test_clone_depth_rejects_a_negative_value() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, clone_depth=-1)


def test_clone_depth_of_one_is_allowed() -> None:
    """The shallowest meaningful clone, and what `max(1, depth)` produced."""
    assert Settings(_env_file=None, clone_depth=1).clone_depth == 1


# --- extra="ignore", pinned with the measurement that justifies it --------


def test_a_typoed_env_var_is_not_caught_by_either_extra_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pins the measurement behind `extra="ignore"`.

    `extra="forbid"` was proposed so that `UP_ALLOWED_LOCAL_ROTS` would fail
    loudly. It does not: `EnvSettingsSource` only harvests variables matching
    a declared field, so an unknown `UP_*` variable never becomes an extra
    input and there is nothing for `forbid` to reject. If this test ever
    fails, pydantic-settings changed and the choice recorded in `config.py`
    should be revisited.
    """
    monkeypatch.setenv("UP_ALLOWED_LOCAL_ROTS", "/tmp/typo")

    settings = Settings(_env_file=None)

    assert settings.allowed_local_roots == ()

    class Forbidding(Settings):
        model_config = SettingsConfigDict(env_file=None, env_prefix="UP_", extra="forbid")

    assert Forbidding().allowed_local_roots == ()


def test_forbid_would_reject_an_unrelated_key_in_the_dotenv_file(tmp_path: Path) -> None:
    """The other half of the measurement: what `forbid` *does* reject is any
    key in the .env file that is not a field, prefix or no prefix. This
    repository's own .env holds an unrelated non-UpgradePilot key, so
    `forbid` would make `get_settings()` raise on it."""
    env_file = tmp_path / "dotenv"
    env_file.write_text("OPENROUTER_API_KEY=abc\nUP_MAX_REPO_FILES=11\n")

    class Forbidding(Settings):
        model_config = SettingsConfigDict(env_file=None, env_prefix="UP_", extra="forbid")

    assert Settings(_env_file=env_file).max_repo_files == 11
    with pytest.raises(ValidationError):
        Forbidding(_env_file=env_file)


# --- allowed_url_schemes: an entry that cannot match is a silent deny-all ---


@pytest.mark.parametrize("scheme", ["HTTPS", "Https", "hTTps"], ids=repr)
def test_an_uppercase_scheme_is_refused_at_startup(
    monkeypatch: pytest.MonkeyPatch, scheme: str
) -> None:
    """`urlsplit` always lowercases the scheme it parses, so an uppercase
    allowlist entry matches nothing and refuses every clone. The refusal an
    operator actually saw was "Repository URL scheme must be one of: HTTPS."
    against a URL whose detail said `scheme='https'` -- it named their own
    value as the thing they were missing.

    Refused at startup rather than lowercased: rewriting the entry would mean
    the effective policy is not the configured policy, and a security
    allowlist is the last place that should be true."""
    monkeypatch.setenv("UP_ALLOWED_URL_SCHEMES", f"{scheme},git")

    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=None)

    message = str(excinfo.value)
    assert "must be lowercase" in message
    assert repr(scheme) in message
    # The message must name the fix, not just the fault.
    assert repr(scheme.lower()) in message


@pytest.mark.parametrize(
    "scheme",
    ["https://", "http s", "ht tps", "9https", "+https", "ħttps"],
    ids=repr,
)
def test_a_scheme_shaped_nothing_like_a_scheme_is_refused(scheme: str) -> None:
    """The same defect class as the uppercase case: each of these was accepted
    by Settings and then silently matched no URL at all. RFC 3986 allows a
    letter followed by letters, digits, '+', '-' and '.', and nothing here
    fits that, so none of them could ever equal a parsed scheme."""
    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=None, allowed_url_schemes=frozenset({scheme}))

    assert "is not a URL scheme" in str(excinfo.value)


@pytest.mark.parametrize(
    "scheme", ["https", "git", "file", "git+ssh", "svn+ssh", "view-source", "h2c"], ids=repr
)
def test_every_legitimate_scheme_still_loads(scheme: str) -> None:
    """The shape rule must not cost a real scheme. '+', '-', '.' and digits
    are all legal under RFC 3986 and appear in schemes this product plausibly
    allows."""
    settings = Settings(_env_file=None, allowed_url_schemes=frozenset({scheme}))

    assert settings.allowed_url_schemes == frozenset({scheme})


def test_the_shipped_scheme_defaults_are_matchable() -> None:
    """The default and the committed .env.example must both survive the rule
    they are validated by -- a guard that rejects its own defaults is a
    startup failure for everyone."""
    assert Settings(_env_file=None).allowed_url_schemes == frozenset({"https", "git"})
    assert Settings(_env_file=".env.example").allowed_url_schemes == frozenset({"https", "git"})


def test_a_blank_scheme_keeps_its_own_message() -> None:
    """The scheme rules compose on top of the blank check rather than
    replacing it, so a blank entry is still reported as blank instead of being
    described as a malformed scheme."""
    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=None, allowed_url_schemes=frozenset({"  "}))

    message = str(excinfo.value)
    assert "must not be blank" in message
    assert "is not a URL scheme" not in message


def test_an_unmatchable_scheme_would_otherwise_deny_every_clone(tmp_path: Path) -> None:
    """Why this is a config-time error and not a cosmetic one: the whole point
    of the allowlist is reached through `validate_clone_url`, which compares
    against the lowercased scheme `urlsplit` returns. Asserted against the
    real guard rather than restated, so this stays true if the guard changes.
    """
    from upgradepilot.models.errors import UpgradePilotError
    from upgradepilot.services.repo.guards import validate_clone_url

    # The allowlist Settings now refuses, applied directly to the guard.
    with pytest.raises(UpgradePilotError):
        validate_clone_url("https://github.com/acme/payment-service", frozenset({"HTTPS"}))

    # The same allowlist, spelled the way Settings now insists on.
    assert (
        validate_clone_url("https://github.com/acme/payment-service", frozenset({"https"}))
        == "https://github.com/acme/payment-service"
    )


# --- StorePath and AllowedRoot must not drift apart -----------------------


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param("/tmp/a/../etc", id="dotdot-in-the-middle"),
        pytest.param("/tmp/a/..", id="dotdot-at-the-end"),
        pytest.param("/tmp/a/ /b", id="blank-component"),
    ],
)
def test_both_path_setting_classes_share_their_shape_rules(bad: str) -> None:
    """`allowed_local_roots` accepted `/tmp/a/../etc` while `workspace_dir`
    rejected it: one rule applied to the instance that prompted it and not to
    its sibling, which is the mistake this branch keeps repeating.

    Both are "a filesystem location an operator configured", so both get the
    same shape rules, and this test is what stops them drifting again. Not
    exploitable before the fix -- `guards.py` resolves each root before
    comparing -- but a configured policy that reads as one directory and means
    another is the same defect as an allowlist entry that silently matches
    nothing.
    """
    with pytest.raises(ValidationError):
        Settings(_env_file=None, workspace_dir=Path(bad))
    with pytest.raises(ValidationError):
        Settings(_env_file=None, allowed_local_roots=(Path(bad),))


def test_allowed_local_roots_keeps_its_own_absoluteness_rule(tmp_path: Path) -> None:
    """AllowedRoot enforces a strict superset of StorePath, not the same set:
    a relative path is fine for a store location and never fine for a security
    allowlist entry. The shared rules must not have flattened that away."""
    assert Settings(_env_file=None, chroma_dir=Path("var/chroma")).chroma_dir == Path("var/chroma")
    with pytest.raises(ValidationError, match="absolute"):
        Settings(_env_file=None, allowed_local_roots=(Path("var/roots"),))
    assert Settings(_env_file=None, allowed_local_roots=(tmp_path,)).allowed_local_roots == (
        tmp_path,
    )
