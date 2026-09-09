"""The three endpoints of spec 9.1. Routing, and nothing else.

Every operation lives in `api/runtime.py`, so this module has no branching to
get wrong and a test can drive the same code paths without an HTTP client.

`start` and `resume` both answer **202**: a full run takes minutes, and an
HTTP client that waits for one has already timed out. The client polls
`status`, which returns `RunSnapshot` in every state -- so the frontend
renders one shape and never branches on which endpoint replied.
"""

from fastapi import APIRouter, status

from upgradepilot.api.auth import CallerDep, claim_run, require_owner
from upgradepilot.api.deps import RuntimeDep
from upgradepilot.api.runtime import (
    resume_run,
    snapshot_of,
    snapshot_response,
    start_run,
)
from upgradepilot.api.schemas import (
    ErrorResponse,
    ResumeRequest,
    RunSnapshot,
    StartResponse,
    StartRunRequest,
)
from upgradepilot.api.status import checkpoint_exists, derive_status
from upgradepilot.models.errors import THREAD_NOT_FOUND_MESSAGE, ThreadNotFoundError

router = APIRouter(prefix="/agent", tags=["agent"])

RESPONSES: dict[int | str, dict[str, object]] = {
    404: {"model": ErrorResponse, "description": "No run with that id"},
    409: {"model": ErrorResponse, "description": "The run is not waiting for input"},
    422: {"model": ErrorResponse, "description": "The request could not be accepted"},
}
"""Declared so the generated OpenAPI -- and therefore the frontend's types --
carry the error shape. An error body that only exists at runtime is one the
client renders as `[object Object]` the first time it is hit."""


@router.post(
    "/start",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=StartResponse,
    responses=RESPONSES,
)
async def start(request: StartRunRequest, runtime: RuntimeDep, caller: CallerDep) -> StartResponse:
    thread_id = await start_run(runtime, request)
    # After the run exists, so a thread id is never claimed for a run that
    # failed to start; before the response, so the caller cannot poll a run
    # they do not yet own and be told it does not exist.
    await claim_run(runtime, thread_id, caller)
    snapshot = await snapshot_of(runtime, thread_id)
    return StartResponse(
        thread_id=thread_id,
        status=derive_status(snapshot, runtime.registry.get(thread_id)),
        poll_url=f"/api/agent/status/{thread_id}",
    )


@router.get(
    "/status/{thread_id}",
    response_model=RunSnapshot,
    responses=RESPONSES,
)
async def get_status(thread_id: str, runtime: RuntimeDep, caller: CallerDep) -> RunSnapshot:
    """The one response shape, in every state.

    A thread nobody started is a **404** rather than an empty snapshot:
    measured against the pinned LangGraph, `aget_state` answers for an unknown
    id with a perfectly ordinary snapshot, so an endpoint that did not check
    would return 200 and a blank report for any string a client sent.
    """
    # Ownership before the snapshot read, for two reasons. A run belonging to
    # someone else is never loaded at all, so nothing about it can leak
    # through a response this handler builds. And an unclaimed id short-
    # circuits without touching the checkpointer, which is the cheaper path
    # for the case a prober generates most of.
    await require_owner(runtime, thread_id, caller)
    snapshot = await snapshot_of(runtime, thread_id)
    if not checkpoint_exists(snapshot) and runtime.registry.get(thread_id) is None:
        raise ThreadNotFoundError(THREAD_NOT_FOUND_MESSAGE, detail=f"thread_id={thread_id!r}")
    return snapshot_response(runtime, thread_id, snapshot)


@router.post(
    "/resume",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=StartResponse,
    responses=RESPONSES,
)
async def resume(request: ResumeRequest, runtime: RuntimeDep, caller: CallerDep) -> StartResponse:
    # Ownership first, before `resume_run` touches the graph. Answering
    # someone else's pending decision is the worst thing an unowned resume
    # could do -- the `human_decisions` channel is append-only, so it would be
    # recorded as that user's answer with nothing to say it was not.
    await require_owner(runtime, request.thread_id, caller)
    await resume_run(runtime, request.thread_id, request.decision)
    snapshot = await snapshot_of(runtime, request.thread_id)
    return StartResponse(
        thread_id=request.thread_id,
        status=derive_status(snapshot, runtime.registry.get(request.thread_id)),
        poll_url=f"/api/agent/status/{request.thread_id}",
    )
