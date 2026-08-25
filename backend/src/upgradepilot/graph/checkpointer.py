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
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from pydantic import BaseModel

import upgradepilot.models

MODELS_PACKAGE = upgradepilot.models.__name__


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
async def open_checkpointer(path: Path | str) -> AsyncIterator[AsyncSqliteSaver]:
    """Open an `AsyncSqliteSaver` over `path` with our serializer.

    Written out rather than using `AsyncSqliteSaver.from_conn_string`, which
    takes no `serde` argument -- the constructor does, but only if the
    connection is owned by the caller.

    The connection's lifetime belongs to whoever outlives the graph: the API's
    lifespan in Phase 9, a `with` block in a test. A graph that opened its own
    would either close it too early or leak it, which is why `compile_graph`
    takes a checkpointer rather than a path.
    """
    async with aiosqlite.connect(str(path)) as connection:
        saver = AsyncSqliteSaver(connection, serde=checkpoint_serializer())
        await saver.setup()
        yield saver
