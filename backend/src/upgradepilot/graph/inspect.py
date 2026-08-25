"""Reading a checkpoint: is this run waiting for a person?

One function, and it exists because the obvious way to answer that question
is wrong on the pinned LangGraph. Measured (`probes/probe_interrupt.py`,
`tests/graph/test_langgraph_contract.py`):

    initial pause                   next=('human_review',)   tasks.interrupts=[1]
    after an unusable answer        next=()                  tasks.interrupts=[1]
    after a second unusable answer  next=()                  tasks.interrupts=[1]
    after a valid answer, Q2 open   next=('human_review',)   tasks.interrupts=[1]
    finished                        next=()                  tasks.interrupts=[]

`StateSnapshot.next` reports `()` for a run that is genuinely paused, whenever
the pause came from a *second* `interrupt()` call inside one node execution --
which is exactly what re-asking a question after an unusable answer does. A
status ladder reading `next` would report that run as COMPLETED: the client
stops polling, the question is never answered, and the report shown is a
partial one presented as final.

`tasks[*].interrupts` is the surface that is right in every case, so it is the
only one this project reads. Spec 9.2's ladder puts "checkpoint has
interrupts" first for exactly this reason, and this is what "has interrupts"
means.
"""

from typing import Any

from langgraph.types import StateSnapshot

from upgradepilot.models.decision import InterruptPayload


def pending_interrupts(snapshot: StateSnapshot) -> tuple[Any, ...]:
    """Every interrupt this checkpoint is currently waiting on."""
    return tuple(entry for task in snapshot.tasks for entry in task.interrupts)


def is_awaiting_human(snapshot: StateSnapshot) -> bool:
    """Whether this run has stopped for a person. See the module docstring."""
    return bool(pending_interrupts(snapshot))


def pending_payload(snapshot: StateSnapshot) -> InterruptPayload | None:
    """The question currently on screen, or `None` if the run is not waiting.

    Returns the payload rather than LangGraph's `Interrupt` wrapper, and the
    distinction matters: every `interrupt()` call inside one node reports the
    *same* `Interrupt.id` -- the id identifies the task, not the question
    (measured in `probes/probe_interrupt.py`). `InterruptPayload.question_id`
    is the identity, and it travels inside the value.

    A value that is not an `InterruptPayload` yields `None` rather than being
    coerced. Nothing in this system interrupts with anything else, so such a
    value means the checkpoint was written by something that is not this
    graph -- and guessing at its shape would put an unvalidated object in
    front of the person answering.
    """
    for entry in pending_interrupts(snapshot):
        value = getattr(entry, "value", None)
        if isinstance(value, InterruptPayload):
            return value
    return None
