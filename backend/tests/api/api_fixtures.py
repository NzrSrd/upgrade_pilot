"""A real application over a scripted graph.

The runtime factory seam in `create_app` is what makes this possible without
patching: the test drives the real lifespan, the real routes and the real
error handlers, over a graph whose chat model is scripted and whose embeddings
are offline. Spec 11's split exactly -- the substitutions are the two the spec
names, and everything else is production code.
"""

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from tests.graph.graph_fixtures import (
    COMPLETE_CORPUS,
    a_grade_response,
    a_graph_environment,
    a_narrative_response,
    a_plan_response,
    a_plan_response_draft,
)
from upgradepilot.api.registry import RunRegistry
from upgradepilot.api.runtime import Runtime
from upgradepilot.config import Settings
from upgradepilot.graph.build import compile_graph
from upgradepilot.graph.checkpointer import open_checkpointer

FIXTURE_HIGH_CONFIDENCE = ("BaseModel", "Config", "Optional", "validator")

REAL_PLAN = a_plan_response_draft(
    ("Replace @validator with @field_validator", ("validator",)),
    ("Replace class Config with model_config", ("Config",)),
    ("Update renamed BaseModel methods", ("BaseModel", "dict", "copy")),
    ("Give Optional fields explicit defaults", ("Optional",)),
)


def a_script() -> list[Any]:
    """One retrieval round, a narrative, and a plan. Enough for one run."""
    return [
        a_plan_response(("everything about this upgrade", FIXTURE_HIGH_CONFIDENCE)),
        a_grade_response(sufficient=True),
        a_narrative_response("Validators and model config both change."),
        REAL_PLAN,
    ]


def a_settings(tmp_path: Path, *, max_concurrent: int = 4) -> Settings:
    """Settings pointing every store inside `tmp_path`.

    `_env_file=None` keeps the developer's own `.env` out of the test process
    -- without it a machine with `UP_MAX_CONCURRENT_RUNS` exported would run a
    different test than CI does.
    """
    return Settings(
        _env_file=None,
        chroma_dir=tmp_path / "chroma",
        checkpoint_db=tmp_path / "checkpoints.db",
        workspace_dir=tmp_path / "workspaces",
        max_concurrent_runs=max_concurrent,
        allowed_local_roots=(tmp_path,),
    )


def a_runtime_factory(
    tmp_path: Path,
    *,
    responses: Sequence[Any] | None = None,
    max_concurrent: int = 4,
    repo_root_holder: list[Path] | None = None,
) -> Any:
    """A factory the app's lifespan can call, yielding a scripted `Runtime`.

    The checkpointer is opened here for the same reason production opens it in
    the lifespan: its connection has to outlive every request and be closed
    after the last in-flight task, and `registry.drain()` on the way out is
    what stops a task writing through a connection that has already closed.
    """
    script = list(responses) if responses is not None else a_script()

    @asynccontextmanager
    async def factory(settings: Settings) -> AsyncIterator[Runtime]:
        deps, repo_root, _ = a_graph_environment(
            tmp_path, responses=script, documents=COMPLETE_CORPUS
        )
        if repo_root_holder is not None:
            repo_root_holder.append(repo_root)
        registry = RunRegistry(max_concurrent)
        async with open_checkpointer(settings.checkpoint_db) as checkpointer:
            runtime = Runtime(
                settings=settings,
                registry=registry,
                graph=compile_graph(deps=deps, checkpointer=checkpointer),
            )
            try:
                yield runtime
            finally:
                await registry.drain()

    return factory


def a_start_body(repo_root: Path) -> dict[str, Any]:
    return {
        "repo": {"path": str(repo_root)},
        "dependency": {
            "name": "pydantic",
            "current_version": "1.10.13",
            "target_version": "2.9.0",
        },
        "constraints": {},
    }
