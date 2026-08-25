"""The HTTP contract: every status code, the one response shape, and orphans.

Phase 9's exit criterion is "all backend functionality is reachable through
the documented contract". These tests drive the real application -- the real
lifespan, routes, error handlers and CORS -- over a graph whose chat model is
scripted and whose embeddings are offline, which is spec 11's split exactly.

`TestClient` runs the lifespan, so every test here opens a real SQLite
checkpointer and a real Chroma collection under `tmp_path` and drains the run
registry on the way out.
"""

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from tests.api.api_fixtures import (
    a_runtime_factory,
    a_settings,
    a_start_body,
)
from upgradepilot.api.app import create_app
from upgradepilot.models.enums import RunStatus


@pytest.fixture
def repo_root_holder() -> list[Path]:
    return []


@pytest.fixture
def client(tmp_path: Path, repo_root_holder: list[Path]) -> Any:
    app = create_app(
        a_settings(tmp_path),
        runtime_factory=a_runtime_factory(tmp_path, repo_root_holder=repo_root_holder),
    )
    with TestClient(app) as running:
        yield running


def start(client: Any, repo_root: Path) -> dict[str, Any]:
    response = client.post("/api/agent/start", json=a_start_body(repo_root))
    assert response.status_code == 202, response.text
    return cast(dict[str, Any], response.json())


def poll(client: Any, thread_id: str) -> dict[str, Any]:
    response = client.get(f"/api/agent/status/{thread_id}")
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json())


POLL_INTERVAL = 0.05
POLL_LIMIT = 200
"""How the tests wait for a run that is genuinely asynchronous.

`TestClient` runs the application on its own event loop in a background
thread, so a run started by `POST /start` progresses in real time whether or
not anyone is polling. A poll loop with no sleep therefore spins on the
calling thread and reads the same early snapshot two hundred times -- which
looks exactly like a stalled run and is a stalled *test*. Sleeping between
polls is what the real client does, for the same reason.
"""


def wait_until(
    client: Any,
    thread_id: str,
    predicate: Callable[[dict[str, Any]], bool],
    *,
    what: str,
    limit: int = POLL_LIMIT,
) -> dict[str, Any]:
    """Poll until `predicate` holds.

    A predicate rather than a status set, because "awaiting a human" is not
    always enough: a run that has just been sent an unusable answer is still
    awaiting a human *before* the graph has re-asked, so a wait on the status
    alone returns the pre-resume snapshot and the test asserts against a
    question that has not been updated yet.
    """
    snapshot: dict[str, Any] = {}
    for _ in range(limit):
        snapshot = poll(client, thread_id)
        if predicate(snapshot):
            return snapshot
        time.sleep(POLL_INTERVAL)
    raise AssertionError(f"the run never {what}; last status {snapshot.get('status')}")


def wait_for(
    client: Any, thread_id: str, wanted: set[str], limit: int = POLL_LIMIT
) -> dict[str, Any]:
    """Poll until the run reports one of `wanted`."""
    return wait_until(
        client,
        thread_id,
        lambda snapshot: snapshot["status"] in wanted,
        what=f"reached {sorted(wanted)}",
        limit=limit,
    )


def run_until_settled(client: Any, thread_id: str, limit: int = POLL_LIMIT) -> dict[str, Any]:
    """Poll, answering every question with its recommendation, until it ends."""
    status = "unknown"
    for _ in range(limit):
        snapshot = poll(client, thread_id)
        status = snapshot["status"]
        if status == RunStatus.AWAITING_HUMAN.value:
            question = snapshot["pending_decision"]
            client.post(
                "/api/agent/resume",
                json={
                    "thread_id": thread_id,
                    "decision": {
                        "question_id": question["question_id"],
                        "selected_option_id": question["recommendation_id"],
                    },
                },
            )
            continue
        if status in {
            RunStatus.COMPLETED.value,
            RunStatus.COMPLETED_WITH_WARNINGS.value,
            RunStatus.FAILED.value,
        }:
            return snapshot
        time.sleep(POLL_INTERVAL)
    raise AssertionError(f"the run never settled; last status {status}")


# -- start ------------------------------------------------------------------


def test_start_answers_202_with_somewhere_to_poll(
    client: Any, repo_root_holder: list[Path]
) -> None:
    """202, not 200: a full run takes minutes and an HTTP client that waits
    for one has already timed out."""
    body = start(client, repo_root_holder[0])

    assert body["thread_id"]
    assert body["poll_url"] == f"/api/agent/status/{body['thread_id']}"
    assert body["status"] in {RunStatus.QUEUED.value, RunStatus.RUNNING.value}


def test_a_request_naming_both_a_url_and_a_path_is_refused(
    client: Any, repo_root_holder: list[Path]
) -> None:
    """Refused rather than resolved by precedence: quietly preferring one
    would analyse a repository the caller did not mean to name, with every
    citation in the report pointing at the wrong tree."""
    payload = a_start_body(repo_root_holder[0])
    payload["repo"]["url"] = "https://example.invalid/repo.git"

    response = client.post("/api/agent/start", json=payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_repo_url"


def test_a_request_naming_neither_is_refused(client: Any, repo_root_holder: list[Path]) -> None:
    payload = a_start_body(repo_root_holder[0])
    payload["repo"] = {}

    response = client.post("/api/agent/start", json=payload)

    assert response.status_code == 422


def test_equal_versions_are_refused_at_the_boundary(
    client: Any, repo_root_holder: list[Path]
) -> None:
    """Validated at the boundary so no node re-checks it: a `DependencySpec`
    whose versions match is refused here with a 422 rather than three nodes
    deep as an internal error."""
    payload = a_start_body(repo_root_holder[0])
    payload["dependency"]["target_version"] = payload["dependency"]["current_version"]

    response = client.post("/api/agent/start", json=payload)

    assert response.status_code == 422


# -- status -----------------------------------------------------------------


def test_an_unknown_thread_is_404_not_an_empty_report(client: Any) -> None:
    """Measured against the pinned LangGraph: `aget_state` answers for an
    unknown id with a perfectly ordinary snapshot, so an endpoint that did not
    check would return 200 and a blank report for any string a client sent."""
    response = client.get("/api/agent/status/not-a-real-thread")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "thread_not_found"


def test_the_snapshot_is_one_shape_in_every_state(
    client: Any, repo_root_holder: list[Path]
) -> None:
    """Spec 9.1: the frontend renders a single shape and never branches on
    which endpoint replied. Asserted across three genuinely different states
    of the same run."""
    thread_id = start(client, repo_root_holder[0])["thread_id"]
    shapes = [set(poll(client, thread_id))]

    settled = run_until_settled(client, thread_id)
    shapes.append(set(settled))

    assert len({frozenset(shape) for shape in shapes}) == 1
    assert {"thread_id", "status", "usage", "trace", "pending_decision", "final_report"} <= (
        shapes[0]
    )


def test_a_run_in_flight_reports_evidence_as_it_arrives(
    client: Any, repo_root_holder: list[Path]
) -> None:
    """A client polling a running job should see what has been established
    rather than a spinner over nothing."""
    thread_id = start(client, repo_root_holder[0])["thread_id"]

    settled = run_until_settled(client, thread_id)

    assert settled["affected_files"], "no evidence reached the client"
    assert settled["breaking_changes"]
    assert settled["completed_steps"][0] == "analyze_repo"
    assert settled["usage"]["calls"] > 0


def test_the_error_detail_never_reaches_the_client(
    client: Any, repo_root_holder: list[Path]
) -> None:
    """CLAUDE.md rule 27: `detail` is technical and belongs in logs correlated
    by `thread_id`; it routinely contains provider responses and exception
    text."""
    response = client.get("/api/agent/status/not-a-real-thread")

    assert "detail" not in response.json()["error"]


def test_a_finished_run_carries_its_report_and_its_usage(
    client: Any, repo_root_holder: list[Path]
) -> None:
    thread_id = start(client, repo_root_holder[0])["thread_id"]

    settled = run_until_settled(client, thread_id)

    assert settled["status"] in {
        RunStatus.COMPLETED.value,
        RunStatus.COMPLETED_WITH_WARNINGS.value,
    }
    assert settled["final_report"] is not None
    assert settled["migration_plan"]["steps"]
    assert settled["usage"]["estimated"] is False
    assert settled["usage"]["pricing_complete"] is True
    assert settled["usage"]["by_node"]


# -- the human-in-the-loop pause -------------------------------------------


def test_a_paused_run_offers_the_question_over_http(
    client: Any, repo_root_holder: list[Path]
) -> None:
    """Everything the person answering needs, reachable without watching the
    run."""
    thread_id = start(client, repo_root_holder[0])["thread_id"]

    snapshot = wait_for(client, thread_id, {RunStatus.AWAITING_HUMAN.value})

    question = snapshot["pending_decision"]
    assert question is not None
    assert question["reason"] and question["question"]
    assert question["consequences_if_unanswered"]
    assert len(question["options"]) >= 2
    assert question["recommendation_id"] in {o["id"] for o in question["options"]}
    assert snapshot["final_report"] is None, "a paused run must not present a final report"


def test_resuming_a_completed_run_is_409(client: Any, repo_root_holder: list[Path]) -> None:
    """A client that has lost track of state. Quietly re-running the graph
    would bill a second time for a report that already exists."""
    thread_id = start(client, repo_root_holder[0])["thread_id"]
    run_until_settled(client, thread_id)

    response = client.post(
        "/api/agent/resume",
        json={
            "thread_id": thread_id,
            "decision": {"question_id": "strategy-choice", "selected_option_id": "x"},
        },
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "thread_not_awaiting_input"


def test_resuming_an_unknown_thread_is_404(client: Any) -> None:
    response = client.post("/api/agent/resume", json={"thread_id": "nope", "decision": None})

    assert response.status_code == 404


def test_an_unknown_option_leaves_the_run_awaiting_the_same_question(
    client: Any, repo_root_holder: list[Path]
) -> None:
    """The graph validates again against the question actually being asked,
    because only it knows which one that is. The API's shape check cannot."""
    thread_id = start(client, repo_root_holder[0])["thread_id"]
    snapshot = wait_for(client, thread_id, {RunStatus.AWAITING_HUMAN.value})

    question = snapshot["pending_decision"]
    accepted = client.post(
        "/api/agent/resume",
        json={
            "thread_id": thread_id,
            "decision": {
                "question_id": question["question_id"],
                "selected_option_id": "not-an-option",
            },
        },
    )

    assert accepted.status_code == 202
    after = wait_until(
        client,
        thread_id,
        lambda snapshot: (snapshot["pending_decision"] or {}).get("validation_error") is not None,
        what="re-asked the question with a complaint",
    )

    assert after["status"] == RunStatus.AWAITING_HUMAN.value
    assert "not one of the options offered" in after["pending_decision"]["validation_error"]
    assert after["pending_decision"]["question_id"] == question["question_id"]
    assert after["final_report"] is None


def test_a_malformed_decision_is_422_before_it_reaches_the_graph(
    client: Any, repo_root_holder: list[Path]
) -> None:
    response = client.post(
        "/api/agent/resume",
        json={"thread_id": "whatever", "decision": {"question_id": "q"}},
    )

    assert response.status_code == 422


# -- CORS -------------------------------------------------------------------


def test_cors_allows_the_configured_origin_and_nothing_else(tmp_path: Path) -> None:
    """Not `*`: a wildcard in a service that will later hold repository
    credentials is a decision nobody would make on purpose."""
    settings = a_settings(tmp_path)
    app = create_app(settings, runtime_factory=a_runtime_factory(tmp_path))

    with TestClient(app) as running:
        allowed = running.get("/api/health", headers={"Origin": settings.cors_origins[0]})
        refused = running.get("/api/health", headers={"Origin": "https://not-configured.invalid"})

    assert allowed.headers["access-control-allow-origin"] == settings.cors_origins[0]
    assert "access-control-allow-origin" not in refused.headers


# -- the contract the frontend generates types from ------------------------


def test_the_openapi_document_describes_every_endpoint(client: Any) -> None:
    """Phase 10 generates its TypeScript from this document. A route missing
    from it is a route the frontend cannot call in a type-safe way, and the
    gap surfaces as an `any` rather than as an error."""
    schema = client.get("/openapi.json").json()

    assert set(schema["paths"]) >= {
        "/api/health",
        "/api/agent/start",
        "/api/agent/status/{thread_id}",
        "/api/agent/resume",
    }
    start = schema["paths"]["/api/agent/start"]["post"]["responses"]
    assert set(start) >= {"202", "404", "409", "422"}


def test_derived_values_the_report_renders_are_in_the_response(
    client: Any, repo_root_holder: list[Path]
) -> None:
    """A derived value the frontend cannot see is one the frontend
    re-derives, which is a second implementation of the rule in a language
    that cannot check it against this one."""
    thread_id = start(client, repo_root_holder[0])["thread_id"]

    settled = run_until_settled(client, thread_id)

    assert "passed" in settled["validation"]
    assert "completed_with_warnings" in settled["final_report"]
    assert "evidence_available" in settled["rag_context"]
    assert settled["validation"]["passed"] is True
    assert settled["final_report"]["completed_with_warnings"] is False
