"""Deriving a run's status from the checkpoint plus the registry.

Spec 9.2's ladder, in order, and the order is what makes it correct: a
terminal checkpoint always wins over whatever the registry believes, because
the checkpoint is on disk and the registry is a guess about a process.

    checkpoint has interrupts                 -> AWAITING_HUMAN
    checkpoint holds a final report           -> COMPLETED | COMPLETED_WITH_WARNINGS
    registry task raised                      -> FAILED
    registry task awaiting the semaphore      -> QUEUED
    registry task still running               -> RUNNING
    no live task, no report                   -> ORPHANED

**Completion is read from the final report, not from `snapshot.next`.** Spec
9.2 words the second rung as "checkpoint next == ()", and measured against the
pinned LangGraph that condition is true at *two* different moments: at the end
of a run, and immediately after the input is written, before the first node's
task is scheduled. A ladder testing it reported `COMPLETED` -- with an empty
trace and no report -- to a client polling a second after `start`, which then
stopped polling. `final_report` is set by `finalize` and by nothing else, and
`traced` guarantees every path reaches `finalize`, so its presence means the
run is over and its absence means it is not.

Nothing here is stored (spec 6.5). A status field written by a process would
say `RUNNING` forever after that process died -- which is precisely the case
`ORPHANED` exists to describe, so storing it would erase the one state it was
introduced for.
"""

from typing import Any

from langgraph.types import StateSnapshot

from upgradepilot.api.registry import RunHandle
from upgradepilot.graph.inspect import is_awaiting_human
from upgradepilot.models.enums import RunStatus
from upgradepilot.models.plan import FinalReport


def checkpoint_exists(snapshot: StateSnapshot | None) -> bool:
    """Whether this thread has ever run.

    `created_at` rather than `values` or `next`: measured against the pinned
    LangGraph, `aget_state` for a thread that has never run returns a
    perfectly ordinary snapshot with `values={}`, `next=()` and `created_at`
    of `None`. A ladder testing `next == ()` would report every unknown thread
    id as `COMPLETED` -- a 200 with an empty report for a run nobody ever
    started.
    """
    return snapshot is not None and snapshot.created_at is not None


def _final_report(snapshot: StateSnapshot) -> FinalReport | None:
    value = snapshot.values.get("final_report") if isinstance(snapshot.values, dict) else None
    return value if isinstance(value, FinalReport) else None


def derive_status(
    snapshot: StateSnapshot | None,
    handle: RunHandle | None,
) -> RunStatus:
    """The ladder above, evaluated in order. See the module docstring."""
    if checkpoint_exists(snapshot):
        assert snapshot is not None  # narrowed by checkpoint_exists
        if is_awaiting_human(snapshot):
            return RunStatus.AWAITING_HUMAN
        report = _final_report(snapshot)
        if report is not None:
            if report.completed_with_warnings:
                return RunStatus.COMPLETED_WITH_WARNINGS
            return RunStatus.COMPLETED

    if handle is not None:
        if handle.failed:
            return RunStatus.FAILED
        if handle.waiting:
            return RunStatus.QUEUED
        if not handle.finished:
            return RunStatus.RUNNING

    # Either nothing is driving this thread any more, or the task ended
    # without `finalize` producing a report. Both are the same thing from the
    # caller's side -- nothing will advance it without a resume -- and
    # `ORPHANED` is the state that offers that affordance rather than a
    # spinner that never resolves.
    return RunStatus.ORPHANED


TERMINAL_STATUSES = frozenset(
    {RunStatus.COMPLETED, RunStatus.COMPLETED_WITH_WARNINGS, RunStatus.FAILED}
)
"""Statuses a client may stop polling on.

`ORPHANED` is deliberately absent: the run is not finished, it is abandoned,
and the UI's affordance there is a resume button rather than a final report.
`AWAITING_HUMAN` is absent for the same reason in reverse -- it needs input,
not patience.
"""


def is_terminal(status: RunStatus) -> bool:
    return status in TERMINAL_STATUSES


def snapshot_values(snapshot: StateSnapshot | None) -> dict[str, Any]:
    """The checkpoint's channel values, or an empty mapping.

    One place that decides what an absent checkpoint looks like, so every
    caller reads `{}` rather than each one guarding `None` differently.
    """
    if snapshot is None or not isinstance(snapshot.values, dict):
        return {}
    return snapshot.values
