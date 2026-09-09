"""Shutdown has a deadline, so `drain()` needs one too.

ADR-002's platform fact 2: Cloud Run sends `SIGTERM` and kills the container
**10 seconds later**, and that grace period is not configurable. `drain()`
awaited every in-flight task with no limit, against runs the README describes
as taking minutes. So the drain could not finish, and the shutdown was not
deliberate -- it was truncated by the platform at an arbitrary point, with the
checkpointer closed underneath tasks still running.

**Cancelling on timeout is the part that matters, and it is not tidiness.**
Returning from `drain()` while tasks keep running is worse than the unbounded
wait it replaces: the lifespan proceeds to close the checkpointer and the
connection pool, and a task that touches either afterwards fails in a way that
has nothing to do with what it was doing. Cancellation instead unwinds each
task through its own `finally` blocks while its resources are still open. The
run is lost either way -- that is ADR-002's accepted cost -- but it is lost at
a known point rather than mid-write.

The default stays unbounded. `drain()` is also how tests await completion, and
a default deadline there would turn a slow machine into a flaky suite. The
deadline belongs to the caller that actually has one, which is the lifespan.
"""

import asyncio

from upgradepilot.api.registry import RunRegistry


async def test_an_unfinishable_run_does_not_hold_shutdown_open() -> None:
    """The defect, reproduced: without a timeout this call never returns.

    The run here waits on an event nothing sets, which is what a run in the
    middle of a multi-minute LLM call looks like from `drain()`'s point of
    view. `asyncio.timeout` around the call rather than trusting it would
    hang the suite instead of failing it.
    """
    registry = RunRegistry(max_concurrent=2)
    never = asyncio.Event()

    async def forever() -> None:
        await never.wait()

    registry.start("t-1", forever)
    await asyncio.sleep(0)

    async with asyncio.timeout(2):
        await registry.drain(timeout=0.05)


async def test_a_timed_out_run_is_cancelled_rather_than_left_running() -> None:
    """The assertion with teeth.

    A `drain()` that returned on timeout and left the task running would
    satisfy the test above completely, and would then have the lifespan close
    the checkpointer under a live task. So the task must be *done* by the time
    `drain()` returns, and done by cancellation rather than by completing.
    """
    registry = RunRegistry(max_concurrent=2)
    never = asyncio.Event()

    async def forever() -> None:
        await never.wait()

    handle = registry.start("t-1", forever)
    await asyncio.sleep(0)

    await registry.drain(timeout=0.05)

    assert handle.task is not None
    assert handle.task.done(), "drain returned while the task was still running"
    assert handle.task.cancelled(), "the task ended, but not by cancellation"


async def test_a_run_gets_to_finish_its_own_cleanup() -> None:
    """Cancellation must reach the task's `finally`, not bypass it.

    This is the difference between cancelling and abandoning. A run holds a
    workspace directory and a checkpointer connection; its unwind path is
    where those are released. If `drain()` cancelled without awaiting the
    cancellation, the `finally` below would not have run by the time it
    returned, and the process would exit mid-cleanup -- the same outcome as
    the platform kill this timeout exists to get ahead of.
    """
    registry = RunRegistry(max_concurrent=2)
    never = asyncio.Event()
    cleaned_up = False

    async def forever() -> None:
        nonlocal cleaned_up
        try:
            await never.wait()
        finally:
            cleaned_up = True

    registry.start("t-1", forever)
    await asyncio.sleep(0)

    await registry.drain(timeout=0.05)

    assert cleaned_up, "the task was cancelled without its cleanup being awaited"


async def test_a_run_that_finishes_in_time_is_not_cancelled() -> None:
    """The timeout must not punish a run that was about to finish.

    Guards against a drain that cancels unconditionally, which would turn
    every shutdown into a lost run even when the work had already completed.
    """
    registry = RunRegistry(max_concurrent=2)
    finished = False

    async def quick() -> None:
        nonlocal finished
        finished = True

    handle = registry.start("t-1", quick)

    await registry.drain(timeout=5)

    assert finished
    assert handle.task is not None
    assert handle.task.done()
    assert not handle.task.cancelled()


async def test_drain_still_awaits_everything_when_given_no_timeout() -> None:
    """The default is unchanged, which is what keeps the suite honest.

    Every existing `await registry.drain()` in the tests means "wait for the
    work to actually finish". A default deadline would quietly convert those
    into "wait a bit", and assertions about completed runs would start
    depending on machine speed.
    """
    registry = RunRegistry(max_concurrent=2)
    ran = asyncio.Event()

    async def slow() -> None:
        await asyncio.sleep(0.05)
        ran.set()

    registry.start("t-1", slow)

    await registry.drain()

    assert ran.is_set()


async def test_a_failing_run_does_not_raise_out_of_drain() -> None:
    """Preserved behaviour, re-asserted because the timeout path is new.

    Raising here would leave the remaining tasks unawaited and abort the rest
    of the shutdown. The failure is not lost -- it stays on the handle and
    `derive_status` reports `FAILED`.
    """
    registry = RunRegistry(max_concurrent=2)

    async def boom() -> None:
        raise RuntimeError("the run failed")

    handle = registry.start("t-1", boom)

    await registry.drain(timeout=5)

    assert handle.task is not None
    assert handle.task.done()
    assert isinstance(handle.task.exception(), RuntimeError)
