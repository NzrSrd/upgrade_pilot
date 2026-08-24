from pathlib import Path

from fastapi.testclient import TestClient

from upgradepilot.api.app import create_app
from upgradepilot.api.routes import health
from upgradepilot.config import Settings

MISSING_ROOT = Path("/nonexistent-upgradepilot-test-root/deeply/nested")
"""A location whose parent also does not exist, so it cannot be created."""


def _all_checks_pass(tmp_path: Path) -> Settings:
    """Settings under which every health check is true."""
    return Settings(
        _env_file=None,
        openai_api_key="sk-test-not-a-real-key",
        chroma_dir=tmp_path / "chroma",
        checkpoint_db=tmp_path / "nested" / "checkpoints.db",
    )


def test_health_responds_with_the_documented_shape() -> None:
    client = TestClient(create_app())
    response = client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] in {"ok", "degraded"}
    assert set(body["checks"]) == {"chroma_dir", "checkpoint_dir", "openai_configured"}
    assert isinstance(body["version"], str) and body["version"]


def test_health_reports_ok_when_every_check_passes(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(health, "get_settings", lambda: _all_checks_pass(tmp_path))

    body = TestClient(create_app()).get("/api/health").json()

    assert body["checks"] == {
        "chroma_dir": True,
        "checkpoint_dir": True,
        "openai_configured": True,
    }
    assert body["status"] == "ok"


def test_health_does_not_require_an_api_key(monkeypatch) -> None:
    """A health probe must never depend on, or spend money at, OpenAI.

    Builds an explicitly unconfigured Settings (no .env, no dotenv fallback)
    and injects it into the route, so this test controls its own inputs
    instead of depending on the working tree having no `.env` file. A
    shell-exported OPENAI_API_KEY is also stripped so it can't leak in via
    the real environment either.

    "Does not require" means the request still succeeds and still reports.
    It does not mean the missing key is reported as fine -- see
    `test_health_is_not_ok_when_the_api_key_is_missing`.
    """
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    unconfigured = Settings(_env_file=None)
    monkeypatch.setattr(health, "get_settings", lambda: unconfigured)

    response = TestClient(create_app()).get("/api/health")

    assert response.status_code == 200
    assert response.json()["checks"]["openai_configured"] is False


def test_health_reports_store_ready_for_a_writable_location(tmp_path, monkeypatch) -> None:
    """chroma_dir/checkpoint_dir must actually verify usability, not just existence."""
    monkeypatch.setattr(health, "get_settings", lambda: _all_checks_pass(tmp_path))

    checks = TestClient(create_app()).get("/api/health").json()["checks"]

    assert checks["chroma_dir"] is True
    assert checks["checkpoint_dir"] is True


def test_health_reports_store_not_ready_for_an_uncreatable_location(monkeypatch) -> None:
    """A location whose parent also doesn't exist can't be created, so must be False."""
    settings = Settings(
        _env_file=None,
        chroma_dir=MISSING_ROOT / "chroma",
        checkpoint_db=MISSING_ROOT / "checkpoints.db",
    )
    monkeypatch.setattr(health, "get_settings", lambda: settings)

    checks = TestClient(create_app()).get("/api/health").json()["checks"]

    assert checks["chroma_dir"] is False
    assert checks["checkpoint_dir"] is False


def test_health_is_not_ok_when_a_store_check_fails(tmp_path, monkeypatch) -> None:
    """The defect this asserts against: a 200 saying "ok" over failing checks.

    Reproduced before the fix -- the route returned a hardcoded `"ok"` while
    both store checks were false, and `App.tsx` rendered it as a green tick.
    Only `chroma_dir` is broken here, and the key and checkpoint location are
    both fine, so the status cannot come out non-ok by accident: it can only
    be non-ok if the one false check actually reached the derivation.
    """
    settings = Settings(
        _env_file=None,
        openai_api_key="sk-test-not-a-real-key",
        chroma_dir=MISSING_ROOT / "chroma",
        checkpoint_db=tmp_path / "nested" / "checkpoints.db",
    )
    monkeypatch.setattr(health, "get_settings", lambda: settings)

    body = TestClient(create_app()).get("/api/health").json()

    assert body["checks"] == {
        "chroma_dir": False,
        "checkpoint_dir": True,
        "openai_configured": True,
    }
    assert body["status"] != "ok"
    assert body["status"] == "degraded"


def test_health_is_not_ok_when_the_api_key_is_missing(tmp_path, monkeypatch) -> None:
    """The same rule for the configuration check, not just the store checks.

    A missing key means the agent cannot do its job. Both stores are ready
    here, so `openai_configured` is the only false check and is therefore
    the only thing that can make the status non-ok.
    """
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    settings = Settings(
        _env_file=None,
        chroma_dir=tmp_path / "chroma",
        checkpoint_db=tmp_path / "nested" / "checkpoints.db",
    )
    monkeypatch.setattr(health, "get_settings", lambda: settings)

    body = TestClient(create_app()).get("/api/health").json()

    assert body["checks"] == {
        "chroma_dir": True,
        "checkpoint_dir": True,
        "openai_configured": False,
    }
    assert body["status"] == "degraded"


def test_every_reported_check_can_change_the_status() -> None:
    """No check may be reported to the caller and left out of the derivation.

    The tests above pin the three checks one at a time, which is only a
    complete guarantee for as long as there are three. This binds the rule
    itself: for each field of `HealthChecks`, flipping just that field to
    false must move the status off "ok". A fourth check added to the model
    and forgotten in `_derive_status` fails here rather than shipping as a
    reassuring lie.
    """
    fields = list(health.HealthChecks.model_fields)
    assert fields, "an empty check set would assert nothing"

    all_true = dict.fromkeys(fields, True)
    assert health._derive_status(health.HealthChecks(**all_true)) == "ok"

    for field in fields:
        one_false = {**all_true, field: False}
        status = health._derive_status(health.HealthChecks(**one_false))
        assert status != "ok", f"{field!r} is reported but does not affect the status"
