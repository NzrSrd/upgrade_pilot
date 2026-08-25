"""The FastAPI application, and the lifespan that owns everything long-lived.

Two things this module gets deliberately right.

**Nothing is built at import time.** `create_app()` is a function and the
resources are opened in the lifespan, so importing this module -- which a test
collector, a type checker and `--help` all do -- neither opens a SQLite
connection nor reads a Chroma directory. The Phase 2 carry-in was exactly the
opposite: `app = create_app()` at module scope, which made `import
upgradepilot.api.app` a side effect.

**CORS comes from settings, with an explicit allowlist.** Not `*`: the browser
sends cookies and headers to whatever is allowed, and a wildcard in a service
that will later hold repository credentials is a decision nobody would make on
purpose.
"""

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from upgradepilot import __version__
from upgradepilot.api.errors import install_error_handlers
from upgradepilot.api.routes import agent, health
from upgradepilot.api.runtime import Runtime, open_runtime, sweep_workspaces
from upgradepilot.config import Settings, get_settings

RuntimeFactory = Callable[[Settings], AbstractAsyncContextManager[Runtime]]


def create_app(
    settings: Settings | None = None,
    *,
    runtime_factory: RuntimeFactory = open_runtime,
) -> FastAPI:
    """Build the application.

    `runtime_factory` is the seam the tests use. The alternative -- monkey-
    patching `open_runtime` -- would leave the production path untested while
    appearing to test it, because a patched module-level name is not the code
    that runs in production. Injecting the factory means the test drives the
    real lifespan, the real routes and the real error handlers over a graph
    whose chat model is scripted and whose embeddings are offline, which is
    exactly spec 11's split.
    """
    resolved = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Before anything opens a workspace of its own, and exactly once.
        app.state.swept = sweep_workspaces(resolved)
        async with runtime_factory(resolved) as runtime:
            app.state.runtime = runtime
            yield

    app = FastAPI(title="UpgradePilot", version=__version__, lifespan=lifespan)
    app.state.settings = resolved
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(resolved.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    install_error_handlers(app)
    app.include_router(health.router, prefix="/api")
    app.include_router(agent.router, prefix="/api")
    return app
