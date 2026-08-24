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


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Liveness plus local-store readiness.

    Deliberately does not call OpenAI: a health probe must not cost money
    or inherit third-party latency.
    """
    settings = get_settings()
    checks = HealthChecks(
        chroma_dir=settings.chroma_dir.parent.exists(),
        checkpoint_dir=settings.checkpoint_db.parent.exists(),
        openai_configured=settings.openai_configured,
    )
    return HealthResponse(status="ok", version=__version__, checks=checks)
