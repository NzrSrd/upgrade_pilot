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
