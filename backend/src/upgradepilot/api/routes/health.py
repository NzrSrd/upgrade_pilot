import os
from pathlib import Path
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from upgradepilot import __version__
from upgradepilot.config import get_settings

router = APIRouter()

HealthStatus = Literal["ok", "degraded"]
"""The only two things this endpoint is entitled to say about itself.

`"ok"` means every check below came back true. `"degraded"` means at least
one did not -- the process is answering, but something it needs is not in
place. There is deliberately no third value for "one specific subsystem is
down": `checks` already carries that, and a status vocabulary that tries to
rank failures would be asserting a severity ordering nothing here measures.
"""


class HealthChecks(BaseModel):
    chroma_dir: bool
    checkpoint_dir: bool
    llm_configured: bool


class HealthResponse(BaseModel):
    status: HealthStatus
    version: str
    checks: HealthChecks


def _store_ready(directory: Path) -> bool:
    """Whether `directory` is a usable store location.

    True if the directory already exists and is writable, or if it doesn't
    exist yet but its parent is writable (so it could be created on first
    use). Filesystem-only and read-only: never touches the network, never
    creates anything itself.
    """
    if directory.exists():
        return directory.is_dir() and os.access(directory, os.W_OK)
    return directory.parent.exists() and os.access(directory.parent, os.W_OK)


def _derive_status(checks: HealthChecks) -> HealthStatus:
    """Compute the status from the checks, rather than asserting one.

    Iterates the model's own fields instead of naming them, so a check added
    to `HealthChecks` later cannot be reported to the caller while being
    silently left out of the status it is supposed to inform. That omission
    is the exact defect this function exists to fix.
    """
    return "ok" if all(checks.model_dump().values()) else "degraded"


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Liveness, local-store readiness, and model-provider configuration.

    `status` is derived from `checks` by `_derive_status` and is never
    asserted independently of them. It previously was: the endpoint returned
    a hardcoded `"ok"` alongside whatever the checks happened to say, so a
    200 with `"ok"` was demonstrated while both store checks were false --
    and the frontend rendered that as a green tick. A health endpoint that
    cannot be wrong about its own checks is the whole point of this route.

    Every check is a cheap local read: two filesystem stats and one look at
    already-loaded settings. Nothing here is reported as unknown because
    nothing here is expensive enough to need to be. In particular this
    deliberately does **not** open the Chroma store, connect to the
    checkpointer database, or call the model provider -- a health probe must not cost
    money or inherit third-party latency, so what it reports is the
    readiness of the store *locations* and the presence of a key, which is
    exactly what the field names say and no more.

    `llm_configured` counts toward `status` like any other check. A
    missing key means the agent cannot do its job, so reporting `"ok"`
    without one would be the same class of false claim in a smaller font.
    """
    settings = get_settings()
    checks = HealthChecks(
        chroma_dir=_store_ready(settings.chroma_dir),
        checkpoint_dir=_store_ready(settings.checkpoint_db.parent),
        llm_configured=settings.llm_configured,
    )
    return HealthResponse(status=_derive_status(checks), version=__version__, checks=checks)
