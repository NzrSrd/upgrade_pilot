from fastapi.testclient import TestClient

from upgradepilot.api.app import create_app


def test_health_reports_ok_and_checks() -> None:
    client = TestClient(create_app())
    response = client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert set(body["checks"]) == {"chroma_dir", "checkpoint_dir", "openai_configured"}
    assert isinstance(body["version"], str) and body["version"]


def test_health_does_not_require_an_api_key(monkeypatch) -> None:
    """A health probe must never depend on, or spend money at, OpenAI."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    from upgradepilot.config import get_settings

    get_settings.cache_clear()
    client = TestClient(create_app())
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["checks"]["openai_configured"] is False
    get_settings.cache_clear()
