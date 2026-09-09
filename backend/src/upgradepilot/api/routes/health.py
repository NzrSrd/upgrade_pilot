import os
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Request

from upgradepilot import __version__
from upgradepilot.api.schemas import HealthChecks, HealthResponse
from upgradepilot.config import Settings, get_settings

router = APIRouter()

HealthStatus = Literal["ok", "degraded"]
"""The only two things this endpoint is entitled to say about itself.

`"ok"` means every check below came back true. `"degraded"` means at least
one did not -- the process is answering, but something it needs is not in
place. There is deliberately no third value for "one specific subsystem is
down": `checks` already carries that, and a status vocabulary that tries to
rank failures would be asserting a severity ordering nothing here measures.

`HealthChecks` and `HealthResponse` live in `api/schemas.py` with every other
response model, so the OpenAPI document -- and the frontend types generated
from it -- has one place errors and responses are defined.
"""


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


def _checkpoint_ready(settings: Settings) -> bool:
    """Whether the configured checkpoint destination looks usable.

    **The two backends are measured differently, and the difference is not
    hidden.** With SQLite this is the same filesystem check as `chroma_dir`:
    the directory that will hold the database is writable. With Postgres it
    reports only that a DSN is *configured*, exactly as `llm_configured`
    reports a key -- and deliberately **not** that the server is reachable.

    That asymmetry is a real weakness and is worth naming rather than
    papering over: on Postgres this check cannot go false, so a database that
    is down leaves `status` at `"ok"`. It is accepted for the reason the
    module docstring already gives -- a health probe must not open
    connections, cost money, or inherit third-party latency -- and the
    response publishes `checkpoint_backend` so a caller can tell which of the
    two claims it is reading. A reachability probe belongs behind its own
    endpoint, where its cost is opted into.
    """
    if settings.checkpoint_url is not None:
        return True
    return _store_ready(settings.checkpoint_db.parent)


def _derive_status(checks: HealthChecks) -> HealthStatus:
    """Compute the status from the checks, rather than asserting one.

    Iterates the model's own fields instead of naming them, so a check added
    to `HealthChecks` later cannot be reported to the caller while being
    silently left out of the status it is supposed to inform. That omission
    is the exact defect this function exists to fix.
    """
    return "ok" if all(checks.model_dump().values()) else "degraded"


@router.get("/health", response_model=HealthResponse)
def health(request: Request) -> HealthResponse:
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
    # The application's own settings, not `get_settings()`. They are usually
    # the same object, and when they are not -- a test app, a second app in
    # one process -- the cached global describes a configuration this
    # application is not running under, so the endpoint would report a key as
    # present while every run failed for the lack of it. Measured: it did.
    settings = getattr(request.app.state, "settings", None) or get_settings()
    checks = HealthChecks(
        chroma_dir=_store_ready(settings.chroma_dir),
        checkpoint_ready=_checkpoint_ready(settings),
        llm_configured=settings.llm_configured,
    )
    return HealthResponse(
        status=_derive_status(checks),
        version=__version__,
        auth_required=settings.auth_required,
        checkpoint_backend="postgres" if settings.checkpoint_url is not None else "sqlite",
        checks=checks,
    )
