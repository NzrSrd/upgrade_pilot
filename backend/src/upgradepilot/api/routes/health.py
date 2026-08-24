import os
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

from upgradepilot import __version__
from upgradepilot.config import get_settings

router = APIRouter()


class HealthChecks(BaseModel):
    chroma_dir: bool
    checkpoint_dir: bool
    openai_configured: bool


class HealthResponse(BaseModel):
    status: str
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


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Liveness plus local-store readiness.

    Deliberately does not call OpenAI: a health probe must not cost money
    or inherit third-party latency.
    """
    settings = get_settings()
    checks = HealthChecks(
        chroma_dir=_store_ready(settings.chroma_dir),
        checkpoint_dir=_store_ready(settings.checkpoint_db.parent),
        openai_configured=settings.openai_configured,
    )
    return HealthResponse(status="ok", version=__version__, checks=checks)
