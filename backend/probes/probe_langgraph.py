"""Phase 0 probe: LangGraph interrupt/resume contract.

Run: backend/.venv/bin/python probes/probe_langgraph.py
Records the findings that go into ADR-001's verification table.
"""

import asyncio
import operator
import tempfile
from pathlib import Path
from typing import Annotated, TypedDict

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

SIDE_EFFECTS: list[str] = []


class State(TypedDict):
    trace: Annotated[list[str], operator.add]
    decision: str | None


def review(_state: State) -> dict:
    SIDE_EFFECTS.append("billed_work")
    answer = interrupt({"question": "pick one", "options": ["a", "b"]})
    return {"trace": ["review"], "decision": answer["selected"]}


def build() -> StateGraph:
    graph = StateGraph(State)
    graph.add_node("review", review)
    graph.add_edge(START, "review")
    graph.add_edge("review", END)
    return graph


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "probe.sqlite")
        async with AsyncSqliteSaver.from_conn_string(db) as saver:
            app = build().compile(checkpointer=saver)
            config = {"configurable": {"thread_id": "probe"}}

            first = await app.ainvoke({"trace": [], "decision": None}, config)
            print(f"interrupt payload      : {first['__interrupt__'][0].value}")

            state = await app.aget_state(config)
            print(f"paused at              : {state.next}")
            print(f"side effects (pass 1)  : {len(SIDE_EFFECTS)}")

            resumed = await app.ainvoke(Command(resume={"selected": "b"}), config)
            print(
                f"side effects (resumed) : {len(SIDE_EFFECTS)}  <- twice: no LLM before interrupt()"
            )
            print(f"state writes           : {resumed['trace']}  <- once: aborted pass discarded")
            print(f"decision applied       : {resumed['decision']}")


if __name__ == "__main__":
    asyncio.run(main())
