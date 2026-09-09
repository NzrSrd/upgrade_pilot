"""The Postgres checkpointer must survive the database closing its side.

ADR-002 D2 chose Postgres so a paused human-in-the-loop run outlives the
process holding it. That guarantee has a second half nobody wrote down: the
run also has to outlive the *connection*, because the connection is the thing
most likely to go away first.

**The forcing fact is the provider.** The chosen database is Neon's free tier,
which suspends the compute after five minutes with no queries. Cloud Run,
meanwhile, keeps an idle instance around after its last request. So the
ordinary state of this deployment overnight is: an instance alive, holding a
connection, to a database that hung up. The next resume is the first thing to
find out.

`probes/probe_postgres_checkpointer.py` cannot catch this and is not deficient
for it -- it proved state survives a **process** restart, against a local
server that never suspends. A held connection dying underneath a process that
keeps running is the opposite arrangement, and nothing before this asserted it.

**Reproduced rather than reasoned about.** `pg_terminate_backend` from a second
connection does exactly what an idle timeout does: the server closes the
socket, the client learns about it on its next use. That makes the failure
deterministic and local, so this needs no Neon account and no five-minute wait.
Marked `postgres` and skipped without `UP_TEST_POSTGRES_URL`, per rule 22.

**What this test discriminates, measured by breaking it on purpose.** Swapping
the single connection for a pool is *not* the fix and this test says so: with
the pool in place but `check=` removed, it still fails, and the log shows
`discarding closed connection` -- the pool notices, but only when the
connection comes *back*, having already handed the dead one out. So the
assertion below is specifically about connection checking on checkout, not
about pooling.
"""

import os
from typing import Any

import psycopg
import pytest
from langgraph.checkpoint.base import Checkpoint, CheckpointMetadata

from upgradepilot.graph.checkpointer import open_checkpointer

pytestmark = pytest.mark.postgres

THREAD = "idle-disconnect-thread"


@pytest.fixture
def postgres_url() -> str:
    return os.environ[  # the `postgres` marker guarantees this is set
        "UP_TEST_POSTGRES_URL"
    ]


def _config(thread_id: str = THREAD) -> Any:
    return {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}


def _a_checkpoint(checkpoint_id: str, note: str) -> Checkpoint:
    """A minimal valid checkpoint.

    `updated_channels` is required by the TypedDict and `None` is its
    documented "not tracked" value; omitting it type-checked as `Any` and
    would have let a future required key slip in unnoticed.
    """
    checkpoint: Checkpoint = {
        "v": 4,
        "id": checkpoint_id,
        "ts": "2026-09-09T00:00:00+00:00",
        "channel_values": {"note": note},
        "channel_versions": {"note": "1"},
        "versions_seen": {},
        "updated_channels": None,
    }
    return checkpoint


def _hang_up_on_every_other_connection(url: str) -> int:
    """Close every *other* backend on this database, server-side.

    Returns how many were terminated, so a test cannot pass because there was
    nothing to terminate -- which is the way this reproduction would rot into
    a test of nothing if the checkpointer later stopped connecting eagerly.

    `pg_terminate_backend` rather than `pg_cancel_backend`: cancelling ends a
    query and leaves the session up, which is not what an idle timeout does.
    """
    with psycopg.connect(url, autocommit=True) as watcher:
        terminated = watcher.execute(
            """
            SELECT pg_terminate_backend(pid)
            FROM pg_stat_activity
            WHERE datname = current_database() AND pid <> pg_backend_pid()
            """
        ).fetchall()
    return len(terminated)


async def test_a_resume_still_works_after_the_database_hangs_up(postgres_url: str) -> None:
    """Write, lose the connection the way Neon loses it, write again.

    The second write is the assertion. Under a single long-lived
    `AsyncConnection` it raises `OperationalError: consuming input failed:
    server closed the connection unexpectedly`, and in production that
    surfaces as a 500 on the resume of a run the user was told was safely
    paused -- the precise promise D2 exists to keep.

    The read afterwards is not redundant: a pool that reconnected but lost the
    prior checkpoint would satisfy the write and still have dropped the run.
    """
    async with open_checkpointer("unused.db", url=postgres_url) as saver:
        await saver.aput(_config(), _a_checkpoint("cp-1", "before"), CheckpointMetadata(), {})

        terminated = _hang_up_on_every_other_connection(postgres_url)
        assert terminated, "nothing was terminated, so this test proved nothing"

        await saver.aput(_config(), _a_checkpoint("cp-2", "after"), CheckpointMetadata(), {})

        restored = await saver.aget_tuple(_config())
        assert restored is not None
        assert restored.checkpoint["channel_values"]["note"] == "after"
