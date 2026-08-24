from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from upgradepilot import __version__
from upgradepilot.api.routes import health
from upgradepilot.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="UpgradePilot", version=__version__)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    app.include_router(health.router, prefix="/api")
    return app


app = create_app()
