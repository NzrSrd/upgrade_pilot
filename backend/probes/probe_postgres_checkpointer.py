"""Phase 13.0: does AsyncPostgresSaver survive a genuine process restart?

ADR-002 D2 moves the checkpointer to Postgres on one claim: a paused
human-in-the-loop run survives the instance it started on. Cloud Run's
filesystem is in-memory and its SIGTERM grace is 10 seconds, so "survives a
restart" is the whole reason the decision exists, and it is the one property
the existing SQLite test deliberately does not prove. From
`tests/graph/test_langgraph_contract.py`:

    test_state_survives_a_new_saver_instance -- "This is disk durability
    across a closed connection; it is not a process restart, since the test
    runs in one process."

So this probe is two processes. Phase `pause` builds the graph, runs it into
`interrupt()`, and exits -- taking its event loop, its connection pool and its
compiled graph with it. Phase `resume` is a *new interpreter* that has never
seen that graph object, opens its own saver over the same database, and has to
reconstruct the run from the checkpoint alone.

The graph is copied from that same contract test rather than invented, so a
difference in outcome is a difference in the checkpointer and not in the graph.

Run:
    ./.venv/bin/python probes/probe_postgres_checkpointer.py pause
    ./.venv/bin/python probes/probe_postgres_checkpointer.py resume
"""

import asyncio
import operator
import os
import sys
from typing import Annotated, Any, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

CONN = os.environ.get("PROBE_PG_URL", "postgresql://localhost:5432/upgradepilot_probe")
THREAD = "probe-restart-1"


class DemoState(TypedDict):
    trace: Annotated[list[str], operator.add]
    decision: str | None


class _Update(TypedDict, total=False):
    trace: list[str]
    decision: str | None


def build(side_effects: list[str]) -> StateGraph[DemoState, Any, DemoState, DemoState]:
    def first(_state: DemoState) -> _Update:
        return {"trace": ["first"]}

    def review(_state: DemoState) -> _Update:
        # Stands in for a billed LLM call placed before interrupt().
        side_effects.append("billed_work")
        answer = interrupt({"question": "pick one", "options": ["a", "b"]})
        return {"trace": ["review"], "decision": answer["selected"]}

    def last(state: DemoState) -> _Update:
        return {"trace": [f"last:{state['decision']}"]}

    graph = StateGraph(DemoState)
    graph.add_node("first", first)  # type: ignore[call-overload]
    graph.add_node("review", review)  # type: ignore[call-overload]
    graph.add_node("last", last)
    graph.add_edge(START, "first")
    graph.add_edge("first", "review")
    graph.add_edge("review", "last")
    graph.add_edge("last", END)
    return graph


async def pause() -> None:
    side_effects: list[str] = []
    config: RunnableConfig = {"configurable": {"thread_id": THREAD}}
    async with AsyncPostgresSaver.from_conn_string(CONN) as saver:
        await saver.setup()
        app = build(side_effects).compile(checkpointer=saver)
        result = await app.ainvoke(DemoState(trace=[], decision=None), config)
        state = await app.aget_state(config)
        print(f"  __interrupt__ present : {'__interrupt__' in result}")
        print(f"  state.next            : {state.next}")
        print(f"  tasks[*].interrupts   : {[len(t.interrupts) for t in state.tasks]}")
        print(f"  trace so far          : {state.values['trace']}")
        print(f"  billed work this proc : {side_effects}")
    print(f"  pid {os.getpid()} exiting -- pool and graph die here")


async def resume() -> None:
    side_effects: list[str] = []
    config: RunnableConfig = {"configurable": {"thread_id": THREAD}}
    async with AsyncPostgresSaver.from_conn_string(CONN) as saver:
        # No setup() call: the schema must already be there from the other
        # process, and a probe that re-created it would hide a missing one.
        app = build(side_effects).compile(checkpointer=saver)
        state = await app.aget_state(config)
        print(f"  checkpoint found      : {state.created_at is not None}")
        print(f"  state.next            : {state.next}")
        print(f"  tasks[*].interrupts   : {[len(t.interrupts) for t in state.tasks]}")
        print(f"  trace before resume   : {state.values['trace']}")

        resumed = await app.ainvoke(Command(resume={"selected": "b"}), config)
        final = await app.aget_state(config)
        print(f"  decision applied      : {resumed['decision']!r}")
        print(f"  final trace           : {final.values['trace']}")
        print(f"  final state.next      : {final.next}")
        print(f"  billed work this proc : {side_effects}  <-- node body re-ran")


if __name__ == "__main__":
    phase = sys.argv[1] if len(sys.argv) > 1 else ""
    if phase not in ("pause", "resume"):
        sys.exit(__doc__)
    print(f"=== phase: {phase} (pid {os.getpid()}) ===")
    asyncio.run(pause() if phase == "pause" else resume())
