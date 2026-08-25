"""The long-lived objects a running API owns, and the run operations over them.

Three things outlive any single request -- the checkpointer's SQLite
connection, the Chroma client, and the compiled graph -- and one thing is
per-process state: the run registry. They are gathered here so that
`api/routes/agent.py` contains routing and nothing else, and so that a test
can drive every operation without an HTTP client.

**The graph is compiled once.** Compiling per request would rebuild the
retrieval subgraph and re-open nothing useful; more to the point, the
checkpointer it is compiled against is the process's, and a graph compiled
against a connection that has since closed fails in a way that looks like a
database problem.

**Startup does not require a model provider.** A missing key makes
`build_tracked_llm` raise, and this module records that rather than refusing
to start: the health endpoint's whole job is to say what is missing, and an
API that will not boot cannot say anything at all. Starting a run then fails
with the same typed error, which the central handler turns into a 502 naming
the configuration.
"""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, StateSnapshot
from pydantic import ValidationError

from upgradepilot.api.registry import RunRegistry
from upgradepilot.api.schemas import (
    ApiError,
    DecisionInput,
    RunSnapshot,
    StartRunRequest,
    UsageView,
)
from upgradepilot.api.status import derive_status, snapshot_values
from upgradepilot.config import Settings
from upgradepilot.graph.build import NODE_SEQUENCE, compile_graph
from upgradepilot.graph.checkpointer import open_checkpointer
from upgradepilot.graph.deps import GraphDeps
from upgradepilot.graph.inspect import pending_payload
from upgradepilot.models.decision import HumanDecision
from upgradepilot.models.enums import RunStatus, TraceEventKind
from upgradepilot.models.errors import (
    ErrorCode,
    InvalidRepoUrlError,
    ThreadNotAwaitingInputError,
    ThreadNotFoundError,
    VersionInvalidError,
)
from upgradepilot.models.inputs import (
    DependencySpec,
    LocalRepoRef,
    RemoteRepoRef,
    RepoRef,
)
from upgradepilot.models.state import MigrationState, initial_state
from upgradepilot.models.usage import LLMCall, UsageSummary
from upgradepilot.services.knowledge.embeddings import openai_embedding_function
from upgradepilot.services.knowledge.store import KnowledgeStore
from upgradepilot.services.llm.tracked import build_tracked_llm
from upgradepilot.services.repo.manager import WorkspaceManager

STALE_WORKSPACE_SECONDS = 60 * 60
"""Age above which an abandoned clone is swept at startup.

An hour, not a minute: `sweep_stale` matches on directory name and mtime, so
too short a window would remove a workspace a slow analysis is still reading.
"""


def sweep_workspaces(settings: Settings) -> list[str]:
    """Reclaim clones a previous process abandoned. Startup only.

    Phase 2 left `sweep_stale` implemented and unwired, with a note that it
    belongs in the FastAPI lifespan. It belongs there and nowhere else: the
    sweep matches on directory name and mtime, so a timer or a request handler
    calling it could remove a workspace a slow analysis still has open --
    `WorkspaceManager.sweep_stale` spells out that contract, and this is the
    single call site that honours it.

    Lives here rather than inside `open_runtime` so that it runs however the
    application is built. A test that substitutes the runtime factory is still
    exercising a real application, and an action that only happens on one of
    two construction paths is an action nobody can test on the path they use.
    """
    return [str(path) for path in WorkspaceManager(settings).sweep_stale(STALE_WORKSPACE_SECONDS)]


@dataclass
class Runtime:
    """Everything a request handler needs, built once at startup."""

    settings: Settings
    registry: RunRegistry
    graph: CompiledStateGraph[MigrationState, Any, MigrationState, MigrationState] | None = None
    startup_error: Exception | None = None
    """Why the graph could not be built, if it could not be.

    Held rather than raised, so the process starts and `/api/health` can say
    what is wrong. An API that refuses to boot when its model provider is
    unconfigured is an API that cannot tell anyone the provider is
    unconfigured.
    """

    def require_graph(
        self,
    ) -> CompiledStateGraph[MigrationState, Any, MigrationState, MigrationState]:
        if self.graph is None:
            raise self.startup_error or RuntimeError("the graph was not built")
        return self.graph


def _repo_ref(request: StartRunRequest) -> RepoRef:
    """Turn the form's two optional fields into one reference.

    Both-or-neither is refused here rather than resolved by precedence. A
    request naming a URL *and* a path is a client bug, and quietly preferring
    one would analyse a repository the caller did not mean to name -- with
    every citation in the resulting report pointing at the wrong tree.
    """
    url = (request.repo.url or "").strip()
    path = (request.repo.path or "").strip()
    if bool(url) == bool(path):
        raise InvalidRepoUrlError(
            "Supply either a repository URL or a local path, not both and not neither.",
            detail=f"url={url!r} path={path!r}",
        )
    return RemoteRepoRef(url=url) if url else LocalRepoRef(path=path)


def build_state(request: StartRunRequest, thread_id: str) -> MigrationState:
    """Validate the request into the graph's own input models.

    Validation happens at the boundary so no node re-checks it (spec 6): a
    `DependencySpec` whose versions are equal, or a blank name, is refused
    here with a 422 rather than three nodes deep with an internal error.
    """
    try:
        dependency = DependencySpec(
            name=request.dependency.name,
            current_version=request.dependency.current_version,
            target_version=request.dependency.target_version,
        )
    except ValidationError as exc:
        # Typed rather than allowed to escape. A `ValidationError` reaching the
        # generic handler is a 500 with "something went wrong", which is a lie
        # about a request the caller can fix in one field -- and it is the one
        # class of error a form is guaranteed to produce. Spec 9.3's
        # `VERSION_INVALID` is the code that fits: the two versions and the
        # name are all this model constrains.
        raise VersionInvalidError(
            "; ".join(error["msg"] for error in exc.errors()) or "The dependency is invalid.",
            detail=f"DependencySpec: {exc}",
        ) from exc

    return initial_state(
        thread_id=thread_id,
        repo_ref=_repo_ref(request),
        dependency=dependency,
        constraints=request.constraints,
    )


def config_for(thread_id: str) -> RunnableConfig:
    return {"configurable": {"thread_id": thread_id}}


@asynccontextmanager
async def open_runtime(settings: Settings) -> AsyncIterator[Runtime]:
    """Open every long-lived resource, and close them in the right order.

    The checkpointer's connection is the outermost thing here, because the
    graph holds it and in-flight tasks hold the graph. `drain()` before the
    context exits is what stops a task writing a checkpoint through a
    connection that has already closed -- which surfaces as "Connection
    closed" from deep inside aiosqlite, several frames from anything that
    explains it.
    """
    registry = RunRegistry(settings.max_concurrent_runs)
    runtime = Runtime(settings=settings, registry=registry)

    workspaces = WorkspaceManager(settings)

    async with open_checkpointer(settings.checkpoint_db) as checkpointer:
        try:
            store = KnowledgeStore.open(
                settings.chroma_dir,
                embedding_function=openai_embedding_function(settings),
            )
            runtime.graph = compile_graph(
                deps=GraphDeps(
                    llm=build_tracked_llm(settings),
                    store=store,
                    workspaces=workspaces,
                    max_rag_iterations=settings.max_rag_iterations,
                ),
                checkpointer=checkpointer,
            )
        except Exception as exc:  # noqa: BLE001 - recorded, never swallowed
            # Rule 20 in its startup form: the failure is kept on the runtime
            # and reported by `/api/health` and by any attempt to start a run,
            # rather than being raised into uvicorn's boot sequence where the
            # only artefact is a traceback in a log nobody is watching yet.
            runtime.startup_error = exc

        try:
            yield runtime
        finally:
            await registry.drain()


def new_thread_id() -> str:
    return str(uuid.uuid4())


async def snapshot_of(runtime: Runtime, thread_id: str) -> StateSnapshot | None:
    graph = runtime.require_graph()
    return await graph.aget_state(config_for(thread_id))


def _completed_steps(trace: list[Any]) -> tuple[str, ...]:
    """Parent-graph nodes that have finished, in order, without repeats.

    Filtered to `NODE_SEQUENCE` and `human_review`, because the retrieval
    subgraph's nodes run several times each and a progress list that grew to
    fourteen entries for a three-round loop would read as a stalled run rather
    than a working one. The full detail is in `trace`, which is what the
    activity drawer renders.
    """
    known = {*NODE_SEQUENCE, "human_review"}
    seen: list[str] = []
    for event in trace:
        if (
            event.kind is TraceEventKind.NODE_COMPLETED
            and event.node in known
            and event.node not in seen
        ):
            seen.append(event.node)
    return tuple(seen)


def snapshot_response(
    runtime: Runtime, thread_id: str, snapshot: StateSnapshot | None
) -> RunSnapshot:
    """Build the one response shape from the checkpoint plus the registry."""
    handle = runtime.registry.get(thread_id)
    status = derive_status(snapshot, handle)
    values = snapshot_values(snapshot)
    trace = list(values.get("agent_trace", []))
    calls: list[LLMCall] = list(values.get("llm_calls", []))

    current: str | None = None
    if snapshot is not None and snapshot.next:
        current = snapshot.next[0]

    errors = tuple(ApiError.of(error) for error in values.get("errors", []))
    if handle is not None and handle.failed:
        # A task that raised produced no `AppError` in state -- it died before
        # any node could record one -- so the failure would otherwise be
        # visible only as a status with no explanation next to it.
        errors = (
            *errors,
            ApiError(
                code=ErrorCode.INTERNAL,
                message="The run stopped unexpectedly and did not complete.",
                retryable=True,
            ),
        )

    return RunSnapshot(
        thread_id=thread_id,
        status=status,
        current_step=current,
        completed_steps=_completed_steps(trace),
        trace=tuple(trace),
        usage=UsageView.of(UsageSummary.from_calls(calls)),
        affected_files=tuple(values.get("affected_files", [])),
        breaking_changes=tuple(values.get("breaking_changes", [])),
        retrieved_sources=tuple(values.get("retrieved_sources", [])),
        rag_context=values.get("rag_context"),
        risk_analysis=values.get("risk_analysis"),
        migration_plan=values.get("migration_plan"),
        validation=values.get("validation"),
        human_decisions=tuple(values.get("human_decisions", [])),
        pending_decision=pending_payload(snapshot) if snapshot is not None else None,
        final_report=values.get("final_report"),
        errors=errors,
    )


async def start_run(runtime: Runtime, request: StartRunRequest) -> str:
    """Validate, register and schedule a run. Returns its thread id.

    Returns rather than awaits: spec 9.1 has `start` answer **202**, because
    a full run takes minutes and an HTTP client that waits for it has already
    timed out. The work happens on a task and the client polls.
    """
    graph = runtime.require_graph()
    thread_id = new_thread_id()
    state = build_state(request, thread_id)

    async def work() -> None:
        await graph.ainvoke(state, config_for(thread_id))

    runtime.registry.start(thread_id, work)
    return thread_id


async def resume_run(runtime: Runtime, thread_id: str, decision: DecisionInput | None) -> RunStatus:
    """Continue a paused or abandoned run. Returns the status it resumed from.

    Two legitimate cases, and refusing everything else is the point:

    - `AWAITING_HUMAN` with a decision -- the ordinary resume. The decision is
      validated *again* inside `human_review` against the question actually
      being asked, because only the graph knows which one that is.
    - `ORPHANED` with no decision -- a run whose checkpoint outlived the
      process driving it. `ainvoke(None, ...)` picks up from the last
      checkpoint; asking the client to supply a decision for it would be
      asking for a lie.

    Anything else is `THREAD_NOT_AWAITING_INPUT` (409). A resume against a
    completed run is a client that has lost track of state, and quietly
    re-running the graph for it would bill a second time for a report that
    already exists.
    """
    graph = runtime.require_graph()
    snapshot = await snapshot_of(runtime, thread_id)
    status = derive_status(snapshot, runtime.registry.get(thread_id))

    if status is RunStatus.ORPHANED and snapshot is not None and snapshot.created_at is None:
        raise ThreadNotFoundError("No run with that id exists.", detail=f"thread_id={thread_id!r}")

    if status is RunStatus.AWAITING_HUMAN:
        if decision is None:
            raise ThreadNotAwaitingInputError(
                "This run is waiting for an answer, so a decision is required.",
                detail=f"thread_id={thread_id!r} status={status.value}",
            )
        answer = HumanDecision(
            question_id=decision.question_id,
            selected_option_id=decision.selected_option_id,
            rationale=decision.rationale,
            decided_at=datetime.now(UTC),
        )

        async def work() -> None:
            await graph.ainvoke(Command(resume=answer), config_for(thread_id))

    elif status is RunStatus.ORPHANED:
        if decision is not None:
            raise ThreadNotAwaitingInputError(
                "This run was abandoned rather than waiting for an answer; resume it "
                "without a decision.",
                detail=f"thread_id={thread_id!r} status={status.value}",
            )

        async def work() -> None:
            await graph.ainvoke(None, config_for(thread_id))

    else:
        raise ThreadNotAwaitingInputError(
            f"This run is {status.value.replace('_', ' ')}, so there is nothing to resume.",
            detail=f"thread_id={thread_id!r} status={status.value}",
        )

    runtime.registry.start(thread_id, work)
    return status
