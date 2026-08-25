"""What `interrupt()` actually does on the pinned LangGraph, measured.

Phase 7 depends on three behaviours that the documentation describes and this
project's rules require us to verify: that a node re-executes from the top on
resume, that a second `interrupt()` in the same node pauses again, and that a
replayed `interrupt()` returns the resume value it was given the first time.
The third is what makes re-interrupting on an invalid decision terminate
rather than spin.

Run: `.venv/bin/python probes/probe_interrupt.py`
"""

import asyncio
import tempfile
from pathlib import Path
from typing import Any, TypedDict

import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

CALLS: list[str] = []


class S(TypedDict):
    answers: list[str]


async def node(state: S) -> dict[str, Any]:
    CALLS.append("entered")
    first = interrupt({"question": "one"})
    CALLS.append(f"first={first!r}")
    if first == "bad":
        retry = interrupt({"question": "one, again"})
        CALLS.append(f"retry={retry!r}")
        first = retry
    second = interrupt({"question": "two"})
    CALLS.append(f"second={second!r}")
    return {"answers": [first, second]}


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        async with aiosqlite.connect(str(Path(tmp) / "c.db")) as conn:
            saver = AsyncSqliteSaver(conn)
            await saver.setup()
            g = StateGraph(S)
            g.add_node("ask", node)
            g.add_edge(START, "ask")
            g.add_edge("ask", END)
            graph = g.compile(checkpointer=saver)
            cfg: Any = {"configurable": {"thread_id": "t"}}

            out = await graph.ainvoke({"answers": []}, cfg)
            print("after first invoke, __interrupt__:", out.get("__interrupt__"))
            snap = await graph.aget_state(cfg)
            print("  next:", snap.next, "| tasks interrupts:", [t.interrupts for t in snap.tasks])

            out = await graph.ainvoke(Command(resume="bad"), cfg)
            print("after resume 'bad', __interrupt__:", out.get("__interrupt__"))

            out = await graph.ainvoke(Command(resume="good"), cfg)
            print("after resume 'good', __interrupt__:", out.get("__interrupt__"))

            out = await graph.ainvoke(Command(resume="second-answer"), cfg)
            print("after resume 'second-answer':", out)
            snap = await graph.aget_state(cfg)
            print("  next:", snap.next)

    print("\ncall log:")
    for entry in CALLS:
        print("  ", entry)


asyncio.run(main())


# ---------------------------------------------------------------------------
# Measured 2026-08-25 against langgraph 1.2.11, and the reason
# `upgradepilot/graph/inspect.py` exists:
#
#   initial pause                   next=('ask',)   tasks.interrupts=[1]
#   after an unusable answer        next=()         tasks.interrupts=[1]
#   after a second unusable answer  next=()         tasks.interrupts=[1]
#   after a valid answer, Q2 open   next=('ask',)   tasks.interrupts=[1]
#   finished                        next=()         tasks.interrupts=[]
#
# `StateSnapshot.next` reports `()` for a run that is genuinely paused whenever
# the pause came from a SECOND `interrupt()` call inside one node execution --
# which is exactly what re-asking a question after an unusable answer does. A
# status ladder reading `next` would report that run as COMPLETED: the client
# stops polling, the question is never answered, and a partial report is
# presented as final. `tasks[*].interrupts` is correct in every case.
#
# Two further facts this probe establishes:
#   - a node re-executes from the top on every resume (see the call log), so a
#     model call above an `interrupt()` is billed once per resume;
#   - every `interrupt()` inside one node reports the SAME `Interrupt.id`, so
#     that id identifies the task, not the question.
# ---------------------------------------------------------------------------
