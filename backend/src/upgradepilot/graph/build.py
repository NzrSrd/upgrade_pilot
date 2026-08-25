"""Assembling the graph.

**The spine only, deliberately.** Spec §8.5's topology has two conditional
edges -- the decision predicate before `human_review`, and validation's
bounded repair retry -- and both are omitted here. Each is defined by a
predicate that belongs to a later phase (§8.2's "≥2 viable strategies
differing on an axis constraints do not settle" is Phase 7; the ten validation
checks are Phase 8), and a conditional edge wired now would have to guess at
its own condition. The nodes all exist and run in order; the branches land
with the phases that own their predicates.

What is real here is everything the branches would sit on: the state channels
and their reducers, the checkpointer, the trace, the usage records, and rule
20's error handling.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from upgradepilot.graph.nodes import NodeBody, make_assess_risk, make_stub, traced
from upgradepilot.models.state import MigrationState
from upgradepilot.services.llm.tracked import TrackedLLM

NODE_SEQUENCE: tuple[str, ...] = (
    "analyze_repo",
    "inspect_dependency",
    "agentic_rag",
    "assess_risk",
    "generate_plan",
    "validate_plan",
    "finalize",
)
"""Spec §8.5's node list, minus `human_review`.

`human_review` is absent rather than stubbed because a stub of it would be
actively misleading: the node's entire content is an `interrupt()` call, so a
version that does not interrupt is a node that does nothing while occupying
the place where the run is supposed to stop. Phase 7 adds it with the
predicate that decides whether it fires at all.
"""


def build_graph(
    *,
    llm: TrackedLLM,
    fail_in: Mapping[str, Exception] | None = None,
) -> StateGraph[MigrationState, Any, MigrationState, MigrationState]:
    """Wire the spine.

    `fail_in` injects an exception into a named node. It exists so the error
    handling in `traced` can be tested through the real graph rather than by
    calling the wrapper directly -- the thing worth proving is that a failure
    in a node reaches *state*, and that only the assembled graph can show.
    """
    failures = dict(fail_in or {})

    def body_for(name: str) -> NodeBody:
        base = make_assess_risk(llm) if name == "assess_risk" else make_stub(name)
        if name not in failures:
            return base

        async def failing(state: MigrationState) -> dict[str, Any]:
            raise failures[name]

        return failing

    graph = StateGraph(MigrationState)
    for name in NODE_SEQUENCE:
        # `type: ignore[call-overload]`, for the reason already established in
        # `tests/graph/test_langgraph_contract.py`: langgraph resolves the
        # contravariant NodeInputT per `add_node` call rather than once per
        # graph, so which call trips the overload set is an artifact of how
        # many precede it, not of this callable's shape.
        graph.add_node(name, traced(name, body_for(name)))  # type: ignore[call-overload]

    graph.add_edge(START, NODE_SEQUENCE[0])
    for earlier, later in zip(NODE_SEQUENCE, NODE_SEQUENCE[1:], strict=False):
        graph.add_edge(earlier, later)
    graph.add_edge(NODE_SEQUENCE[-1], END)
    return graph


def compile_graph(
    *,
    llm: TrackedLLM,
    checkpointer: BaseCheckpointSaver[Any],
    interrupt_before: Sequence[str] | None = None,
    fail_in: Mapping[str, Exception] | None = None,
) -> CompiledStateGraph[MigrationState, Any, MigrationState, MigrationState]:
    """Compile the spine against a checkpointer.

    The checkpointer is injected rather than constructed here. `AsyncSqliteSaver`
    is an async context manager whose lifetime has to be owned by whoever
    outlives the graph -- the API's lifespan in Phase 9, a `with` block in a
    test -- and a graph that opened its own would either close it too early or
    leak it.

    `interrupt_before` is LangGraph's own breakpoint, not spec §8.2's
    `interrupt()`. Phase 4 needs somewhere to stop and continue in order to
    prove usage survives a resume; the decision machinery is Phase 7's.
    """
    return build_graph(llm=llm, fail_in=fail_in).compile(
        checkpointer=checkpointer,
        interrupt_before=list(interrupt_before) if interrupt_before else None,
    )
