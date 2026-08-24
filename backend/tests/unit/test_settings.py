from pathlib import Path

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
