"""Which Clerk user started which run, and nobody else's.

ADR-002 D6. Until Clerk arrived there was no second user, so every run was
implicitly the caller's and `GET /api/agent/status/{thread_id}` checking
nothing was correct. Real users make that a hole: without an owner, any
signed-in caller can read any run, and -- worse than reading -- can answer
someone else's pending decision, which the append-only `human_decisions`
channel would then record as that user's answer with nothing to say it was
not.

**Postgres rather than the graph state.** The owner could have travelled
inside the checkpoint, which would have needed no table and worked on both
backends. Two reasons it lives here instead. Ownership is an authorization
fact, and `CLAUDE.md` rule 16's layering keeps API concerns out of graph
state. And Sub-project 3's saved analyses need exactly this join, so a table
now is a down payment rather than a detour -- whereas a field in the
checkpoint would have to be migrated out again.

The cost is a stated coupling: `Settings` refuses to start with a Clerk key
and no `UP_CHECKPOINT_URL`, because a gate whose ownership cannot be recorded
is worse than no gate at all.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

TABLE = "run_owners"

_CREATE = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    thread_id TEXT PRIMARY KEY,
    user_id   TEXT NOT NULL,
    claimed_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

_INDEX = f"CREATE INDEX IF NOT EXISTS {TABLE}_user_id_idx ON {TABLE} (user_id)"


class RunOwnership:
    """The `thread_id` -> Clerk user mapping, and the only reader of it."""

    def __init__(self, pool: AsyncConnectionPool[AsyncConnection[tuple[str, ...]]]) -> None:
        self._pool = pool

    async def setup(self) -> None:
        """Create the table if it is absent. Idempotent, like the checkpointer's.

        Needs DDL rights once, the same requirement `AsyncPostgresSaver.setup()`
        already imposes, so this adds no new privilege to the deployment.
        """
        async with self._pool.connection() as connection:
            await connection.execute(_CREATE)
            await connection.execute(_INDEX)

    async def claim(self, thread_id: str, user_id: str) -> None:
        """Record who started this run.

        `ON CONFLICT DO NOTHING` rather than an upsert, deliberately: a
        `thread_id` is generated per run and never reused, so a conflict means
        something unexpected happened, and the safe response is to leave the
        first claim standing. An upsert would let a second caller take
        ownership of a run already claimed -- which is the exact transfer this
        table exists to prevent.
        """
        async with self._pool.connection() as connection:
            await connection.execute(
                f"INSERT INTO {TABLE} (thread_id, user_id) VALUES (%s, %s) "
                "ON CONFLICT (thread_id) DO NOTHING",
                (thread_id, user_id),
            )

    async def owner_of(self, thread_id: str) -> str | None:
        """The claiming user, or `None` if this run was never claimed.

        `None` is not "anyone may have it". Callers treat an unclaimed run the
        same as one owned by somebody else -- see `api/auth.py`, where the
        decision about what absence means is made once.
        """
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                f"SELECT user_id FROM {TABLE} WHERE thread_id = %s", (thread_id,)
            )
            row = await cursor.fetchone()
        return None if row is None else str(row[0])


@asynccontextmanager
async def open_ownership(url: str) -> AsyncIterator[RunOwnership]:
    """Open the ownership store over `url`, creating its table.

    A pool rather than a single connection, because psycopg's
    `AsyncConnection` is not safe for concurrent use and this is read on every
    status poll -- one connection would serialise a 1-per-second poll per
    active run behind whatever else was in flight.

    **`check=` is a correctness argument, not tuning.** A pool alone does not
    survive the server hanging up: it notices a dead connection when the
    connection is *returned*, having already handed it to the caller, so the
    caller gets the exception. The chosen provider suspends its compute after
    five minutes idle while Cloud Run keeps an idle instance alive, which makes
    a dead pooled connection the deployment's ordinary morning state. Without
    this, `require_owner` raised `psycopg.errors.AdminShutdown` from
    `owner_of` -- a 500 on the authorisation check, so unavailable rather than
    unsafe, but still the owner locked out of their own paused run. Measured in
    `test_ownership_still_answers_after_the_database_hangs_up`; the checkpointer
    had the same defect and is fixed the same way.
    """
    async with AsyncConnectionPool(
        url, check=AsyncConnectionPool.check_connection, open=False
    ) as pool:
        await pool.open(wait=True)
        store = RunOwnership(pool)
        await store.setup()
        yield store
