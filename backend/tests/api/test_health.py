from pathlib import Path

from fastapi.testclient import TestClient

from upgradepilot.api.app import create_app
from upgradepilot.api.routes import health
from upgradepilot.config import Settings


def test_health_reports_ok_and_checks() -> None:
    client = TestClient(create_app())
    response = client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert set(body["checks"]) == {"chroma_dir", "checkpoint_dir", "openai_configured"}
    assert isinstance(body["version"], str) and body["version"]


def test_health_does_not_require_an_api_key(monkeypatch) -> None:
    """A health probe must never depend on, or spend money at, OpenAI.

    Builds an explicitly unconfigured Settings (no .env, no dotenv fallback)
    and injects it into the route, so this test controls its own inputs
    instead of depending on the working tree having no `.env` file. A
    shell-exported OPENAI_API_KEY is also stripped so it can't leak in via
    the real environment either.
    """
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    unconfigured = Settings(_env_file=None)
    monkeypatch.setattr(health, "get_settings", lambda: unconfigured)

    client = TestClient(create_app())
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["checks"]["openai_configured"] is False


def test_health_reports_store_ready_for_a_writable_location(tmp_path, monkeypatch) -> None:
    """chroma_dir/checkpoint_dir must actually verify usability, not just existence."""
    settings = Settings(
        _env_file=None,
        chroma_dir=tmp_path / "chroma",
        checkpoint_db=tmp_path / "nested" / "checkpoints.db",
    )
    monkeypatch.setattr(health, "get_settings", lambda: settings)

    client = TestClient(create_app())
    checks = client.get("/api/health").json()["checks"]

    assert checks["chroma_dir"] is True
    assert checks["checkpoint_dir"] is True


def test_health_reports_store_not_ready_for_an_uncreatable_location(monkeypatch) -> None:
    """A location whose parent also doesn't exist can't be created, so must be False."""
    missing_root = Path("/nonexistent-upgradepilot-test-root/deeply/nested")
    settings = Settings(
        _env_file=None,
        chroma_dir=missing_root / "chroma",
        checkpoint_db=missing_root / "checkpoints.db",
    )
    monkeypatch.setattr(health, "get_settings", lambda: settings)

    client = TestClient(create_app())
    checks = client.get("/api/health").json()["checks"]

    assert checks["chroma_dir"] is False
    assert checks["checkpoint_dir"] is False
