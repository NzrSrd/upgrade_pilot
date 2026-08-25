"""The three endpoints of spec 9.1. Routing, and nothing else.

Every operation lives in `api/runtime.py`, so this module has no branching to
get wrong and a test can drive the same code paths without an HTTP client.

`start` and `resume` both answer **202**: a full run takes minutes, and an
HTTP client that waits for one has already timed out. The client polls
`status`, which returns `RunSnapshot` in every state -- so the frontend
renders one shape and never branches on which endpoint replied.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, status

from upgradepilot.api.runtime import (
    Runtime,
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
from upgradepilot.models.errors import ThreadNotFoundError

router = APIRouter(prefix="/agent", tags=["agent"])


def get_runtime(request: Request) -> Runtime:
    runtime = getattr(request.app.state, "runtime", None)
    if runtime is None:  # pragma: no cover - the lifespan always sets it
        raise RuntimeError("the application runtime was not initialised")
    return runtime  # type: ignore[no-any-return]


RuntimeDep = Annotated[Runtime, Depends(get_runtime)]

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
async def start(request: StartRunRequest, runtime: RuntimeDep) -> StartResponse:
    thread_id = await start_run(runtime, request)
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
async def get_status(thread_id: str, runtime: RuntimeDep) -> RunSnapshot:
    """The one response shape, in every state.

    A thread nobody started is a **404** rather than an empty snapshot:
    measured against the pinned LangGraph, `aget_state` answers for an unknown
    id with a perfectly ordinary snapshot, so an endpoint that did not check
    would return 200 and a blank report for any string a client sent.
    """
    snapshot = await snapshot_of(runtime, thread_id)
    if not checkpoint_exists(snapshot) and runtime.registry.get(thread_id) is None:
        raise ThreadNotFoundError("No run with that id exists.", detail=f"thread_id={thread_id!r}")
    return snapshot_response(runtime, thread_id, snapshot)


@router.post(
    "/resume",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=StartResponse,
    responses=RESPONSES,
)
async def resume(request: ResumeRequest, runtime: RuntimeDep) -> StartResponse:
    await resume_run(runtime, request.thread_id, request.decision)
    snapshot = await snapshot_of(runtime, request.thread_id)
    return StartResponse(
        thread_id=request.thread_id,
        status=derive_status(snapshot, runtime.registry.get(request.thread_id)),
        poll_url=f"/api/agent/status/{request.thread_id}",
    )
