"""The status ladder and the registry, tested where HTTP cannot reach.

`QUEUED` needs a saturated semaphore, `ORPHANED` needs a checkpoint whose
process is gone, and `FAILED` needs a task that raised. All three are real
states a client will see, and none of them is reachable by driving the API
normally -- so they are driven directly here, against the same functions the
routes call.
"""

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.api.api_fixtures import (
    a_runtime_factory,
    a_script,
    a_settings,
    a_start_body,
)
from tests.graph.graph_fixtures import COMPLETE_CORPUS, a_graph_environment, a_state
from upgradepilot.api.app import create_app
from upgradepilot.api.registry import RunRegistry
from upgradepilot.api.runtime import Runtime, config_for, resume_run, snapshot_of
from upgradepilot.api.status import derive_status, is_terminal
from upgradepilot.graph.build import compile_graph
from upgradepilot.graph.checkpointer import open_checkpointer
from upgradepilot.models.enums import RunStatus
from upgradepilot.models.errors import ThreadNotAwaitingInputError

# -- the registry -----------------------------------------------------------


async def test_a_run_beyond_the_cap_reports_queued_not_running() -> None:
    """A real state, not politeness: a client polling a `RUNNING` run expects
    the trace to grow, and a queued run's trace does not move at all."""
    registry = RunRegistry(max_concurrent=1)
    release = asyncio.Event()

    async def blocking() -> None:
        await release.wait()

    first = registry.start("t-1", blocking)
    second = registry.start("t-2", blocking)
    await asyncio.sleep(0)  # let the first task acquire the semaphore

    assert first.waiting is False
    assert second.waiting is True
    assert derive_status(None, second) is RunStatus.QUEUED
    assert derive_status(None, first) is RunStatus.RUNNING

    release.set()
    await registry.drain()


async def test_a_handle_is_registered_before_its_task_is_scheduled() -> None:
    """Without that ordering there is a window in which a just-started run
    reports as `ORPHANED` -- rare, entirely real, and exactly the kind of race
    that only shows up under load."""
    registry = RunRegistry(max_concurrent=4)

    async def work() -> None:
        return None

    handle = registry.start("t-1", work)

    assert registry.get("t-1") is handle
    assert derive_status(None, handle) is RunStatus.QUEUED
    await registry.drain()


async def test_a_task_that_raised_reports_failed() -> None:
    registry = RunRegistry(max_concurrent=4)

    async def explode() -> None:
        raise RuntimeError("the run died")

    handle = registry.start("t-1", explode)
    await registry.drain()

    assert handle.failed is True
    assert derive_status(None, handle) is RunStatus.FAILED


async def test_a_cancelled_task_is_not_reported_as_failed() -> None:
    """A cancelled task is a run whose process is going away, which the
    checkpoint describes better than a `FAILED` status would."""
    registry = RunRegistry(max_concurrent=4)

    async def forever() -> None:
        await asyncio.Event().wait()

    handle = registry.start("t-1", forever)
    await asyncio.sleep(0)
    assert handle.task is not None
    handle.task.cancel()
    await registry.drain()

    assert handle.failed is False


async def test_drain_does_not_raise_on_a_failing_task() -> None:
    """It runs at shutdown. Raising there would leave the remaining tasks
    unawaited and the checkpointer closed underneath them."""
    registry = RunRegistry(max_concurrent=4)

    async def explode() -> None:
        raise RuntimeError("boom")

    registry.start("t-1", explode)

    await registry.drain()


# -- the ladder -------------------------------------------------------------


def test_an_unknown_thread_with_no_handle_is_orphaned() -> None:
    """The route turns this into a 404; the ladder itself has no notion of
    "never existed", which is why `checkpoint_exists` is a separate check."""
    assert derive_status(None, None) is RunStatus.ORPHANED


def test_orphaned_is_not_terminal() -> None:
    """The run is not finished, it is abandoned, and the UI's affordance is a
    resume button rather than a final report."""
    assert not is_terminal(RunStatus.ORPHANED)
    assert not is_terminal(RunStatus.AWAITING_HUMAN)
    assert is_terminal(RunStatus.COMPLETED)
    assert is_terminal(RunStatus.COMPLETED_WITH_WARNINGS)
    assert is_terminal(RunStatus.FAILED)


# -- orphan detection and resume-from-checkpoint ---------------------------


async def test_a_checkpoint_that_outlived_its_process_reads_as_orphaned(
    tmp_path: Path,
) -> None:
    """Spec 9.2's honest limitation, made concrete.

    The registry is in memory and the checkpoint is on disk, so a server
    restart mid-run leaves a thread nothing is driving. Simulated exactly: the
    graph is run partway under one runtime, and the status is then derived
    with a *fresh* registry -- which is what a restarted process has.
    """
    deps, repo_root, _ = a_graph_environment(
        tmp_path, responses=a_script(), documents=COMPLETE_CORPUS
    )
    settings = a_settings(tmp_path)
    config = config_for("t-orphan")

    async with open_checkpointer(settings.checkpoint_db) as saver:
        graph = compile_graph(deps=deps, checkpointer=saver)
        # `interrupt_before` stops the run mid-graph with a checkpoint that is
        # neither terminal nor awaiting a human -- the shape a killed process
        # leaves behind.
        stopped = compile_graph(deps=deps, checkpointer=saver, interrupt_before=["assess_risk"])
        await stopped.ainvoke(a_state(repo_root, "t-orphan"), config)

        runtime = Runtime(settings=settings, registry=RunRegistry(4), graph=graph)
        snapshot = await snapshot_of(runtime, "t-orphan")

        assert derive_status(snapshot, None) is RunStatus.ORPHANED

        # ...and resuming it without a decision picks it up from the
        # checkpoint rather than starting again.
        status = await resume_run(runtime, "t-orphan", None)
        assert status is RunStatus.ORPHANED
        await runtime.registry.drain()

        resumed = await snapshot_of(runtime, "t-orphan")
        assert derive_status(resumed, runtime.registry.get("t-orphan")) in {
            RunStatus.AWAITING_HUMAN,
            RunStatus.COMPLETED,
            RunStatus.COMPLETED_WITH_WARNINGS,
        }
        assert resumed is not None
        assert resumed.values["risk_analysis"] is not None, (
            "the resume restarted the run rather than continuing it"
        )


async def test_resuming_an_orphan_with_a_decision_is_refused(tmp_path: Path) -> None:
    """An abandoned run is not waiting for an answer, and asking the client to
    invent one for it would be asking for a lie."""
    deps, repo_root, _ = a_graph_environment(
        tmp_path, responses=a_script(), documents=COMPLETE_CORPUS
    )
    settings = a_settings(tmp_path)

    async with open_checkpointer(settings.checkpoint_db) as saver:
        stopped = compile_graph(deps=deps, checkpointer=saver, interrupt_before=["assess_risk"])
        await stopped.ainvoke(a_state(repo_root, "t-orphan"), config_for("t-orphan"))
        runtime = Runtime(
            settings=settings,
            registry=RunRegistry(4),
            graph=compile_graph(deps=deps, checkpointer=saver),
        )

        from upgradepilot.api.schemas import DecisionInput

        with pytest.raises(ThreadNotAwaitingInputError):
            await resume_run(
                runtime,
                "t-orphan",
                DecisionInput(question_id="q", selected_option_id="x"),
            )


# -- the startup sweep ------------------------------------------------------


def test_an_abandoned_workspace_is_swept_at_startup(tmp_path: Path) -> None:
    """Phase 2 left `sweep_stale` implemented and unwired. It belongs at
    startup and nowhere else: the sweep matches on directory name and mtime,
    so running it while a run has a workspace open could remove one
    mid-analysis."""
    import os
    import time

    settings = a_settings(tmp_path)
    settings.workspace_dir.mkdir(parents=True, exist_ok=True)
    stale = settings.workspace_dir / "repo-deadbeef1234"
    stale.mkdir()
    old = time.time() - 60 * 60 * 24
    os.utime(stale, (old, old))

    app = create_app(settings, runtime_factory=a_runtime_factory(tmp_path))
    with TestClient(app) as client:
        client.get("/api/health")

    assert not stale.exists()


# -- the runtime's startup failure --------------------------------------------


def test_a_missing_model_provider_does_not_stop_the_process_starting(
    tmp_path: Path,
) -> None:
    """An API that refuses to boot when its model provider is unconfigured is
    an API that cannot tell anyone the provider is unconfigured."""
    from upgradepilot.api.runtime import open_runtime

    settings = a_settings(tmp_path)
    app = create_app(settings, runtime_factory=open_runtime)

    with TestClient(app) as client:
        health = client.get("/api/health")
        started = client.post("/api/agent/start", json=a_start_body(tmp_path))

    assert health.status_code == 200
    assert health.json()["checks"]["llm_configured"] is False
    assert health.json()["status"] == "degraded"
    assert started.status_code in {502, 503}
    assert started.json()["error"]["message"]


def test_a_snapshot_of_an_uninitialised_runtime_is_not_reachable(tmp_path: Path) -> None:
    """`require_graph` re-raises the startup failure rather than returning
    `None`, so the endpoint answers with the real reason instead of an
    `AttributeError` several frames away."""
    runtime = Runtime(
        settings=a_settings(tmp_path),
        registry=RunRegistry(1),
        startup_error=RuntimeError("no provider"),
    )

    with pytest.raises(RuntimeError, match="no provider"):
        runtime.require_graph()
