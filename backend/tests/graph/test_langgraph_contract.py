"""Locks the LangGraph 1.x interrupt/resume contract the design depends on.

If a LangGraph upgrade changes these semantics, this test fails and the
design rules in the plan's Global Constraints must be re-derived.
"""

import operator
from pathlib import Path
from typing import Annotated, Any, TypedDict

import pytest
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt


class DemoState(TypedDict):
    trace: Annotated[list[str], operator.add]
    decision: str | None


class _DemoStateUpdate(TypedDict, total=False):
    trace: list[str]
    decision: str | None


def _build_graph(side_effects: list[str]) -> StateGraph[DemoState, Any, DemoState, DemoState]:
    def first(_state: DemoState) -> _DemoStateUpdate:
        return {"trace": ["first"]}

    def review(_state: DemoState) -> _DemoStateUpdate:
        # Stands in for a billed LLM call placed before interrupt().
        side_effects.append("billed_work")
        answer = interrupt({"question": "pick one", "options": ["a", "b"]})
        return {"trace": ["review"], "decision": answer["selected"]}

    def last(state: DemoState) -> _DemoStateUpdate:
        return {"trace": [f"last:{state['decision']}"]}

    graph = StateGraph(DemoState)
    # langgraph's `_Node` overload set resolves the contravariant NodeInputT
    # type variable per call rather than once per graph, and (verified: the
    # same `Callable[[DemoState], _DemoStateUpdate]` shape, repeated with a
    # third such call added or removed, flips which calls the overload
    # matches) which specific call trips it is an artifact of how many
    # add_node calls precede it in the same StateGraph[...], not of this
    # callable's shape. `add_node(..., input_schema=DemoState)` on every
    # call would pin it, but that changes the graph's node schema
    # semantics, not just satisfies mypy -- narrowly ignored instead.
    graph.add_node("first", first)  # type: ignore[call-overload]
    graph.add_node("review", review)  # type: ignore[call-overload]
    graph.add_node("last", last)
    graph.add_edge(START, "first")
    graph.add_edge("first", "review")
    graph.add_edge("review", "last")
    graph.add_edge("last", END)
    return graph


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    return str(tmp_path / "ckpt.sqlite")


async def test_interrupt_exposes_payload_and_pauses(db_path: str) -> None:
    side_effects: list[str] = []
    async with AsyncSqliteSaver.from_conn_string(db_path) as saver:
        app = _build_graph(side_effects).compile(checkpointer=saver)
        config: RunnableConfig = {"configurable": {"thread_id": "t1"}}

        result = await app.ainvoke(DemoState(trace=[], decision=None), config)

        assert "__interrupt__" in result
        assert result["__interrupt__"][0].value == {
            "question": "pick one",
            "options": ["a", "b"],
        }

        state = await app.aget_state(config)
        assert state.next == ("review",)
        assert len(state.interrupts) == 1


async def test_resume_continues_same_thread_and_applies_decision(db_path: str) -> None:
    side_effects: list[str] = []
    async with AsyncSqliteSaver.from_conn_string(db_path) as saver:
        app = _build_graph(side_effects).compile(checkpointer=saver)
        config: RunnableConfig = {"configurable": {"thread_id": "t1"}}

        await app.ainvoke(DemoState(trace=[], decision=None), config)
        resumed = await app.ainvoke(Command(resume={"selected": "b"}), config)

        assert resumed["trace"] == ["first", "review", "last:b"]
        assert resumed["decision"] == "b"

        state = await app.aget_state(config)
        assert state.next == ()
        assert len(state.interrupts) == 0


async def test_side_effects_before_interrupt_run_twice_but_writes_do_not(db_path: str) -> None:
    """The reason a node that interrupts must do no LLM work before interrupt().

    The node body re-executes on resume, so pre-interrupt side effects are
    billed twice, while the aborted pass's state writes are discarded.
    """
    side_effects: list[str] = []
    async with AsyncSqliteSaver.from_conn_string(db_path) as saver:
        app = _build_graph(side_effects).compile(checkpointer=saver)
        config: RunnableConfig = {"configurable": {"thread_id": "t1"}}

        await app.ainvoke(DemoState(trace=[], decision=None), config)
        assert side_effects == ["billed_work"]

        resumed = await app.ainvoke(Command(resume={"selected": "a"}), config)

        assert side_effects == ["billed_work", "billed_work"], "side effect ran twice"
        assert resumed["trace"].count("review") == 1, "aborted pass wrote no state"


async def test_threads_are_isolated(db_path: str) -> None:
    side_effects: list[str] = []
    async with AsyncSqliteSaver.from_conn_string(db_path) as saver:
        app = _build_graph(side_effects).compile(checkpointer=saver)
        first_cfg: RunnableConfig = {"configurable": {"thread_id": "t1"}}
        second_cfg: RunnableConfig = {"configurable": {"thread_id": "t2"}}

        await app.ainvoke(DemoState(trace=[], decision=None), first_cfg)
        await app.ainvoke(Command(resume={"selected": "a"}), first_cfg)
        await app.ainvoke(DemoState(trace=[], decision=None), second_cfg)

        first_state = await app.aget_state(first_cfg)
        second_state = await app.aget_state(second_cfg)

        assert first_state.values["trace"] == ["first", "review", "last:a"]
        assert second_state.values["trace"] == ["first"]
        assert second_state.next == ("review",)


async def test_state_survives_a_new_saver_instance(db_path: str) -> None:
    """Checkpoint durability: a fresh connection sees the interrupted state."""
    side_effects: list[str] = []
    config: RunnableConfig = {"configurable": {"thread_id": "t1"}}

    async with AsyncSqliteSaver.from_conn_string(db_path) as saver:
        app = _build_graph(side_effects).compile(checkpointer=saver)
        await app.ainvoke(DemoState(trace=[], decision=None), config)

    async with AsyncSqliteSaver.from_conn_string(db_path) as saver:
        app = _build_graph(side_effects).compile(checkpointer=saver)
        state = await app.aget_state(config)
        assert state.next == ("review",)

        resumed = await app.ainvoke(Command(resume={"selected": "b"}), config)
        assert resumed["decision"] == "b"
