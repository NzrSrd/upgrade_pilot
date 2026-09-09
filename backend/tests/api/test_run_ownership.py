"""Two users, and neither can reach the other's run.

ADR-002 D6. Until Clerk arrived there was no second user, so
`GET /api/agent/status/{thread_id}` checking nothing about the caller was
correct. Real users turn that into the worst kind of hole: not only can any
signed-in caller read any run, they can **answer someone else's pending
decision**, and the `human_decisions` channel is append-only, so it would be
recorded as that user's answer with nothing to say it was not.

**Run against the real store, not a fake**, which is why these are marked
`postgres` and skip without `UP_TEST_POSTGRES_URL`. An in-memory dict standing
in for the ownership table would pass every assertion below while telling us
nothing about the SQL -- and "a suite of fakes can pass while the real path is
broken" is the reason rule 24 keeps a live test at all. Ownership is exactly
that shape of guarantee.

Clerk itself *is* faked, and only Clerk: verifying a real token would need a
live Clerk instance and a signed JWT, and what is under test here is this
project's authorization, not Clerk's cryptography.
"""

import os
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import psycopg
import pytest
from clerk_backend_api import Clerk
from clerk_backend_api.security.types import (
    AuthenticateRequestOptions,
    AuthErrorReason,
    AuthStatus,
    Requestish,
    RequestState,
)
from fastapi.testclient import TestClient
from pydantic import SecretStr

from tests.api.api_fixtures import a_runtime_factory, a_settings, a_start_body
from upgradepilot.api.app import create_app
from upgradepilot.models.errors import THREAD_NOT_FOUND_MESSAGE
from upgradepilot.services.ownership import TABLE, open_ownership

pytestmark = pytest.mark.postgres

ALICE = "user_alice"
BOB = "user_bob"


class _ClerkStub(Clerk):
    """Answers as whichever user the test is currently pretending to be.

    Mutable rather than one instance per user, because the interesting
    assertions are about two callers reaching the *same* deployment -- a
    second app would give each its own ownership store and prove nothing.
    """

    def __init__(self) -> None:
        super().__init__(bearer_auth="sk_test_stub")
        self.user_id: str | None = ALICE

    async def authenticate_request_async(
        self, request: Requestish, options: AuthenticateRequestOptions
    ) -> RequestState:
        if self.user_id is None:
            return RequestState(
                status=AuthStatus.SIGNED_OUT,
                reason=AuthErrorReason.SESSION_TOKEN_MISSING,
            )
        return RequestState(status=AuthStatus.SIGNED_IN, payload={"sub": self.user_id})


@pytest.fixture
def postgres_url() -> str:
    return os.environ[  # the `postgres` marker guarantees this is set
        "UP_TEST_POSTGRES_URL"
    ]


@pytest.fixture(autouse=True)
def a_clean_owner_table(postgres_url: str) -> Iterator[None]:
    """Each test starts with no claims.

    The table is shared across tests in one database, and a claim left behind
    would make a later test pass because of an earlier one's row.
    """
    yield
    with psycopg.connect(postgres_url, autocommit=True) as connection:
        connection.execute(f"DROP TABLE IF EXISTS {TABLE}")


@pytest.fixture
def clerk() -> _ClerkStub:
    return _ClerkStub()


@pytest.fixture
def repo_root() -> list[Path]:
    return []


@pytest.fixture
def client(
    tmp_path: Path, postgres_url: str, clerk: _ClerkStub, repo_root: list[Path]
) -> Iterator[TestClient]:
    """The real app, with the real ownership store and a stubbed Clerk."""
    settings = a_settings(tmp_path).model_copy(
        update={"clerk_secret_key": SecretStr("sk_test_not_a_real_key")}
    )
    inner = a_runtime_factory(tmp_path, repo_root_holder=repo_root)

    @asynccontextmanager
    async def factory(resolved: Any) -> AsyncIterator[Any]:
        async with inner(resolved) as runtime, open_ownership(postgres_url) as ownership:
            runtime.clerk = clerk
            runtime.ownership = ownership
            yield runtime

    with TestClient(create_app(settings, runtime_factory=factory)) as test_client:
        yield test_client


def _start_a_run(client: TestClient, repo_root: list[Path]) -> str:
    response = client.post("/api/agent/start", json=a_start_body(repo_root[0]))
    assert response.status_code == 202, response.text
    thread_id: str = response.json()["thread_id"]
    return thread_id


def test_the_user_who_started_a_run_can_read_it(client: TestClient, repo_root: list[Path]) -> None:
    """The positive direction, without which every refusal below is worthless.

    A gate that refused everyone would satisfy the other tests in this file.
    """
    thread_id = _start_a_run(client, repo_root)

    response = client.get(f"/api/agent/status/{thread_id}")

    assert response.status_code == 200, response.text
    assert response.json()["thread_id"] == thread_id


def test_another_signed_in_user_cannot_read_someone_elses_run(
    client: TestClient, clerk: _ClerkStub, repo_root: list[Path]
) -> None:
    thread_id = _start_a_run(client, repo_root)

    clerk.user_id = BOB
    response = client.get(f"/api/agent/status/{thread_id}")

    assert response.status_code == 404
    assert response.json()["error"]["message"] == THREAD_NOT_FOUND_MESSAGE


def test_another_signed_in_user_cannot_resume_someone_elses_run(
    client: TestClient, clerk: _ClerkStub, repo_root: list[Path]
) -> None:
    """The assertion this whole mechanism exists for.

    Reading another user's run leaks. *Resuming* it writes: the decision would
    land on the append-only `human_decisions` channel as that user's answer,
    and no later reader could tell it was not theirs.
    """
    thread_id = _start_a_run(client, repo_root)

    clerk.user_id = BOB
    response = client.post(
        "/api/agent/resume",
        json={
            "thread_id": thread_id,
            "decision": {"question_id": "q", "selected_option_id": "o"},
        },
    )

    assert response.status_code == 404
    assert response.json()["error"]["message"] == THREAD_NOT_FOUND_MESSAGE


def test_a_foreign_run_is_byte_identical_to_a_missing_one(
    client: TestClient, clerk: _ClerkStub, repo_root: list[Path]
) -> None:
    """The indistinguishability requirement, asserted rather than assumed.

    ADR-002 D6 says a run owned by someone else must be indistinguishable from
    one that does not exist, and "same status code" is not the same claim as
    "same response". The first version of this mechanism raised the two from
    different string literals -- `"No run with that id."` beside
    `"No run with that id exists."` -- which was a working oracle for whether
    a thread id was real, with nothing failing to reveal it. That is why the
    message is now a single constant, and why this compares whole bodies.
    """
    thread_id = _start_a_run(client, repo_root)

    clerk.user_id = BOB
    foreign = client.get(f"/api/agent/status/{thread_id}")
    missing = client.get("/api/agent/status/a-thread-that-was-never-started")

    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json()


def test_an_unauthenticated_request_is_refused_with_401(
    client: TestClient, clerk: _ClerkStub, repo_root: list[Path]
) -> None:
    """401, not 404: the remedy is to sign in, and the status code says so.

    Distinct from the ownership case on purpose. Hiding *authentication*
    failures behind 404 would tell a legitimate user with an expired session
    that their own run had vanished.
    """
    thread_id = _start_a_run(client, repo_root)

    clerk.user_id = None
    response = client.get(f"/api/agent/status/{thread_id}")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"


def test_an_unclaimed_run_is_refused_rather_than_shared(
    client: TestClient, postgres_url: str, repo_root: list[Path]
) -> None:
    """A run with no owner row belongs to nobody, not to everybody.

    Reachable in practice: any thread predating this table, or one whose claim
    failed to write. The permissive reading -- "no owner, so anyone may read
    it" -- would quietly expose exactly those runs, so the row is deleted here
    to prove the refusal rather than trusting the comment in `require_owner`.
    """
    thread_id = _start_a_run(client, repo_root)
    with psycopg.connect(postgres_url, autocommit=True) as connection:
        connection.execute(f"DELETE FROM {TABLE} WHERE thread_id = %s", (thread_id,))

    response = client.get(f"/api/agent/status/{thread_id}")

    assert response.status_code == 404


def test_a_second_claim_does_not_transfer_ownership(
    client: TestClient, clerk: _ClerkStub, postgres_url: str, repo_root: list[Path]
) -> None:
    """`ON CONFLICT DO NOTHING`, asserted at the SQL rather than in the fake.

    Thread ids are generated per run and never reused, so a conflict means
    something unexpected happened and the first claim must stand. An upsert
    here would let a second caller take a run already claimed, which is the
    exact transfer this table exists to prevent.
    """
    thread_id = _start_a_run(client, repo_root)

    with psycopg.connect(postgres_url, autocommit=True) as connection:
        connection.execute(
            f"INSERT INTO {TABLE} (thread_id, user_id) VALUES (%s, %s) "
            "ON CONFLICT (thread_id) DO NOTHING",
            (thread_id, BOB),
        )
        cursor = connection.execute(
            f"SELECT user_id FROM {TABLE} WHERE thread_id = %s", (thread_id,)
        )
        row = cursor.fetchone()

    assert row is not None
    assert row[0] == ALICE

    clerk.user_id = BOB
    assert client.get(f"/api/agent/status/{thread_id}").status_code == 404
