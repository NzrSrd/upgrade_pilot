"""Opening the checkpointer, with a serializer that knows our own types.

**Why this module exists at all.** LangGraph 1.2.11 warns on deserializing a
type it has not been told about: "Deserializing unregistered type ... This
will be blocked in a future version." Measured against the pinned version, the
word "blocked" undersells the behaviour: with strict msgpack enabled, an
unregistered type does not raise -- it comes back as a plain `dict`.

A resumed run would therefore carry dictionaries everywhere it expects
Pydantic models. `BreakingChange.source` would no longer be required,
`RiskFactor.evidence`'s `min_length=1` would no longer hold, `LLMCall`'s
agreement between cost and basis would no longer be checked. Every honesty
invariant this project encodes in its types would be absent from a resumed
run, with nothing raised at the point of loss and the first symptom appearing
somewhere else entirely.

So registering the allowlist is a correctness requirement rather than warning
suppression, and it is registered *by walking the package* rather than by a
hand-written list -- a list is exactly what a model added in a later phase
gets forgotten from, and forgetting has no visible symptom until a resume.
"""

import importlib
import inspect
import pkgutil
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from enum import Enum
from pathlib import Path

import aiosqlite
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from psycopg import AsyncConnection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel

import upgradepilot.models

MODELS_PACKAGE = upgradepilot.models.__name__

POOL_CONNECTION_KWARGS: dict[str, object] = {
    "autocommit": True,
    "prepare_threshold": 0,
    "row_factory": dict_row,
}
"""What `AsyncPostgresSaver.from_conn_string` sets, restated because we no
longer call it.

Each one is load-bearing rather than copied. `autocommit=True` because the
saver's non-pipeline path opens a bare cursor with no surrounding
`transaction()`, so without it psycopg starts an implicit transaction that
nothing commits and every checkpoint write is discarded at close.
`row_factory=dict_row` because `BasePostgresSaver` is typed against `DictRow`
and reads its own rows by column name. `prepare_threshold=0` because the
saver issues the same handful of statements for the life of the process, which
is exactly the case server-side preparation exists for -- it is also the
setting that makes the **direct** Neon endpoint mandatory rather than the
`-pooler` one, since transaction-pooled connections and named prepared
statements do not coexist.
"""

MAX_POOL_SIZE = 2
"""Deliberately tiny, and not a throughput number.

`AsyncPostgresSaver._cursor` holds `self.lock` around every operation, so the
saver serialises itself and a second connection can never be *busy*. The pool
is here to replace a dead connection, not to run two at once; the spare slot
is headroom for the moment a checked-out connection is discarded and its
replacement opened. Sizing it by expected concurrency would hold idle
connections against a free-tier connection budget for no gain.
"""


def serializable_state_types() -> tuple[type, ...]:
    """Every Pydantic model and enum defined under `upgradepilot.models`.

    Enums are included alongside models deliberately. `CostBasis` and
    `TraceEventKind` appeared in LangGraph's own warning output next to the
    models, and a walk that collected only `BaseModel` subclasses would leave
    them to degrade into bare strings -- so `call.cost_basis is
    CostBasis.UNKNOWN` would quietly stop being true after a resume while
    `call.cost_basis == "unknown"` kept working.

    `obj.__module__ == info.name` restricts this to classes *defined* in the
    package rather than merely imported into it, so re-exports do not register
    third-party types on our behalf.
    """
    found: dict[str, type] = {}
    package = importlib.import_module(MODELS_PACKAGE)
    for info in pkgutil.walk_packages(package.__path__, prefix=f"{MODELS_PACKAGE}."):
        module = importlib.import_module(info.name)
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if obj.__module__ != info.name:
                continue
            if issubclass(obj, BaseModel | Enum):
                found[f"{obj.__module__}.{obj.__qualname__}"] = obj
    return tuple(found[key] for key in sorted(found))


def checkpoint_serializer() -> JsonPlusSerializer:
    """LangGraph's serializer, told about this project's types.

    The allowlist is passed to the **constructor**, and that detail is the
    whole of it. The obvious spelling --
    `JsonPlusSerializer().with_msgpack_allowlist(types)` -- is a silent no-op:
    the default serializer's allowlist is the sentinel `True` (permissive),
    and `with_msgpack_allowlist` returns `self` unchanged when the base is
    `True` rather than narrowing it. Everything still worked, because
    permissive mode allows everything anyway, and the "unregistered type"
    warnings kept being logged with nothing to explain why. Passing the list
    at construction is what actually registers it.

    Setting an explicit allowlist also switches this serializer out of
    permissive mode, which is deliberate: it is the mode LangGraph says will
    become the default, so running in it now means a future upgrade changes
    nothing here. Types LangGraph handles itself -- dates, UUIDs, LangChain
    messages -- are unaffected, because they never go through this check.
    """
    return JsonPlusSerializer(allowed_msgpack_modules=serializable_state_types())


@asynccontextmanager
async def open_checkpointer(
    path: Path | str, *, url: str | None = None
) -> AsyncIterator[BaseCheckpointSaver[str]]:
    """Open the checkpointer: Postgres when `url` is given, SQLite otherwise.

    ADR-002 D2. `url` is set in a hosted deployment and unset everywhere
    else, so SQLite remains the path this project develops and tests under
    and the hermetic suite needs no database (rule 22). ADR-001 claims that
    "swapping model provider, checkpointer backend, or repository source each
    touch one module"; this function is that claim being cashed.

    **`url` wins over `path` rather than conflicting with it.** Passing both
    is not an error, because `checkpoint_db` has a default and a deployment
    should be able to select Postgres by adding one variable rather than by
    remembering to unset another. What makes that safe is that `url` cannot
    be *nearly* a URL: `Settings.checkpoint_url` refuses anything that is not
    a `postgres(ql)://` DSN, so there is no value that quietly means SQLite.

    **Both backends get `checkpoint_serializer()`, and that is not
    incidental.** Without the allowlist a resumed run comes back holding
    plain dicts where it expects Pydantic models -- `BreakingChange.source`
    no longer required, `RiskFactor.evidence`'s `min_length=1` no longer
    held. A Postgres backend wired without the serializer would lose every
    honesty invariant this project encodes in its types, on the exact path
    Postgres exists to make durable, and nothing would raise at the point of
    loss.

    **A pool rather than one connection, because the connection dies first.**
    `AsyncPostgresSaver.from_conn_string` is the obvious spelling and it opens
    a single `AsyncConnection` held for the whole application lifespan. The
    chosen provider suspends its compute after five minutes with no queries,
    and Cloud Run keeps an idle instance alive after its last request, so the
    ordinary overnight state of this deployment is a live process holding a
    connection to a database that hung up. The next resume was the first thing
    to find out, and it found out as an `OperationalError` on the resume of a
    run the user had been told was safely paused -- the one promise ADR-002 D2
    exists to keep. `check=` is what fixes it: a connection is verified on
    checkout, and a dead one is discarded and replaced instead of handed over.
    Measured in `tests/graph/test_checkpointer_survives_an_idle_disconnect.py`,
    which reproduces the hang-up with `pg_terminate_backend` rather than
    waiting five minutes.

    `open(wait=True)` so an unreachable database or a bad DSN fails here, at
    startup, rather than on the first checkpoint write.

    `setup()` runs on both branches. It is idempotent, and running it here
    matches what SQLite already did rather than inventing a second lifecycle
    for the new backend. The cost is that the connecting role needs DDL
    rights: a least-privilege deployment that refuses them has to run
    `setup()` once as a migration step instead, and will find out by way of
    a permission error at startup rather than a silent one.

    The connection's lifetime belongs to whoever outlives the graph: the
    API's lifespan in Phase 9, a `with` block in a test. A graph that opened
    its own would either close it too early or leak it, which is why
    `compile_graph` takes a checkpointer rather than a path.
    """
    if url is not None:
        async with AsyncConnectionPool(
            url,
            # Spelled out so the pool's row type is `DictRow`, which is what
            # `BasePostgresSaver` is typed against and reads its rows by.
            # Without it the pool infers tuple rows, `AsyncPostgresSaver`
            # rejects it, and the only alternative was a cast asserting a
            # contract nothing had established.
            connection_class=AsyncConnection[DictRow],
            kwargs=POOL_CONNECTION_KWARGS,
            min_size=1,
            max_size=MAX_POOL_SIZE,
            check=AsyncConnectionPool.check_connection,
            open=False,
        ) as pool:
            await pool.open(wait=True)
            postgres_saver = AsyncPostgresSaver(pool, serde=checkpoint_serializer())
            await postgres_saver.setup()
            yield postgres_saver
    else:
        # Written out rather than using `AsyncSqliteSaver.from_conn_string`,
        # which takes no `serde` argument -- the constructor does, but only if
        # the connection is owned by the caller.
        async with aiosqlite.connect(str(path)) as connection:
            sqlite_saver = AsyncSqliteSaver(connection, serde=checkpoint_serializer())
            await sqlite_saver.setup()
            yield sqlite_saver
