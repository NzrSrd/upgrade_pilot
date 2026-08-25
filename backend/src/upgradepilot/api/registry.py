"""The in-process run registry, and the honest limitation it carries.

Spec 9.2. A `thread_id` maps to a `RunHandle` holding the `asyncio.Task`
driving the graph, when it started, and whether it has got past the
concurrency semaphore yet.

**Two consequences are recorded here rather than discovered later.**

*Spec 1 must run single-worker.* This registry is in memory. Under
`uvicorn --workers 2` there are two processes and two registries, and half
the status lookups are blind -- a run started by worker A is invisible to
worker B, which would report it as `ORPHANED` and offer to restart work that
is currently in progress. Sub-project 3 moves the registry into Postgres and
lifts this.

*A run beyond the cap is `QUEUED`, not `RUNNING`.* The distinction is not
politeness: a client polling a `RUNNING` run expects the trace to grow, and a
queued run's trace does not move at all. Reporting the truth is what lets the
UI say "waiting for a slot" instead of showing a spinner over nothing.

Handles are kept after their task finishes. That is deliberate: the ladder in
`api/status.py` checks the checkpoint first, so a completed run is reported
from its checkpoint whatever the registry says, and keeping the handle is what
lets a run that *failed before writing a terminal checkpoint* be reported as
`FAILED` rather than as orphaned.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass
class RunHandle:
    """One in-flight run. Mutable, because `waiting` changes as it progresses."""

    thread_id: str
    started_at: datetime
    waiting: bool = True
    """True until the run acquires a slot from the semaphore.

    Initialised `True` rather than `False`: a task that has been created but
    has not yet been scheduled has not acquired anything, and defaulting to
    "running" would report work as started that the event loop has not touched.
    """

    task: asyncio.Task[None] | None = field(default=None, repr=False)

    @property
    def failed(self) -> bool:
        """Whether the task ended by raising.

        `CancelledError` is not a failure of the run's own making, so it is
        excluded: a cancelled task is a run whose process is going away, which
        the checkpoint's own state describes better than a `FAILED` status
        would.
        """
        if self.task is None or not self.task.done():
            return False
        if self.task.cancelled():
            return False
        return self.task.exception() is not None

    @property
    def error(self) -> BaseException | None:
        return self.task.exception() if self.failed and self.task is not None else None

    @property
    def finished(self) -> bool:
        return self.task is not None and self.task.done()


class RunRegistry:
    """Thread id to run handle, with a concurrency cap."""

    def __init__(self, max_concurrent: int) -> None:
        # `max(1, ...)`: a cap of zero would deadlock every run at the
        # semaphore with no way to tell from the outside, which is a worse
        # failure than ignoring a misconfiguration. The setting itself is
        # where a zero should be refused.
        self._semaphore = asyncio.Semaphore(max(1, max_concurrent))
        self._handles: dict[str, RunHandle] = {}

    def get(self, thread_id: str) -> RunHandle | None:
        return self._handles.get(thread_id)

    def start(self, thread_id: str, work: Callable[[], Awaitable[None]]) -> RunHandle:
        """Register a run and schedule it behind the semaphore.

        The handle is created and registered **before** the task, so a status
        poll that lands between `create_task` and the first scheduling of the
        coroutine sees `QUEUED` rather than nothing. Without that ordering
        there is a window in which a just-started run reports as `ORPHANED` --
        rare, entirely real, and exactly the kind of race that only shows up
        under load.
        """
        handle = RunHandle(thread_id=thread_id, started_at=datetime.now(UTC))
        self._handles[thread_id] = handle

        async def guarded() -> None:
            async with self._semaphore:
                handle.waiting = False
                await work()

        handle.task = asyncio.create_task(guarded(), name=f"run:{thread_id}")
        return handle

    async def drain(self) -> None:
        """Await every task, ignoring failures. Used at shutdown and in tests.

        Failures are ignored *here* rather than swallowed: each task's
        exception is still on its handle, and `api/status.py` reports it as
        `FAILED`. What this must not do is raise while shutting down, which
        would leave the remaining tasks unawaited and the checkpointer closed
        underneath them.
        """
        tasks = [handle.task for handle in self._handles.values() if handle.task is not None]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
