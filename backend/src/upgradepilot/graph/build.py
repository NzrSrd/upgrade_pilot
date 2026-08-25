"""Assembling the parent graph.

Spec 8.5's topology. The two conditional edges it draws -- the decision
predicate before `human_review`, and validation's bounded repair retry --
are still absent: each is defined by a predicate belonging to a later phase
(Phase 7 owns "at least two viable strategies differing on an axis the
constraints do not settle"; Phase 8 owns the ten validation checks), and a
conditional edge wired now would have to guess at its own condition.

What is real: the evidence layer end to end (`analyze_repo`,
`inspect_dependency`, and the retrieval subgraph behind `agentic_rag`), the
state channels and their reducers, the checkpointer, the trace, the usage
records, and rule 20's error handling.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from upgradepilot.graph.deps import GraphDeps
from upgradepilot.graph.nodes import NodeBody, make_assess_risk, make_stub, traced
from upgradepilot.graph.nodes.evidence import (
    make_agentic_rag,
    make_analyze_repo,
    make_inspect_dependency,
)
from upgradepilot.models.state import MigrationState

NODE_SEQUENCE: tuple[str, ...] = (
    "analyze_repo",
    "inspect_dependency",
    "agentic_rag",
    "assess_risk",
    "generate_plan",
    "validate_plan",
    "finalize",
)
"""Spec 8.5's node list, minus `human_review`.

`human_review` is absent rather than stubbed because a stub of it would be
actively misleading: the node's entire content is an `interrupt()` call, so a
version that does not interrupt is a node that does nothing while occupying
the place where the run is supposed to stop. Phase 7 adds it with the
predicate that decides whether it fires at all.
"""


def _bodies(deps: GraphDeps) -> dict[str, NodeBody[MigrationState]]:
    """One body per node, real where the phase that owns it has landed.

    The stubs are named here rather than defaulted anywhere, so the set of
    nodes that do not yet do anything is a list a reader can see at a glance
    instead of an absence they have to infer.
    """
    return {
        "analyze_repo": make_analyze_repo(deps.workspaces),
        "inspect_dependency": make_inspect_dependency(),
        "agentic_rag": make_agentic_rag(
            llm=deps.llm,
            store=deps.store,
            max_iterations=deps.max_rag_iterations,
            limit=deps.retrieval_limit,
        ),
        # Phase 6 replaces this with real factor extraction; today it is the
        # skeleton's single model call, kept because it is what the resume
        # and usage-aggregation tests exercise.
        "assess_risk": make_assess_risk(deps.llm),
        "generate_plan": make_stub("generate_plan"),  # Phase 8
        "validate_plan": make_stub("validate_plan"),  # Phase 8
        "finalize": make_stub("finalize"),  # Phase 8
    }


def build_graph(
    *,
    deps: GraphDeps,
    fail_in: Mapping[str, Exception] | None = None,
) -> StateGraph[MigrationState, Any, MigrationState, MigrationState]:
    """Wire the spine.

    `fail_in` injects an exception into a named node. It exists so the error
    handling in `traced` can be tested through the real graph rather than by
    calling the wrapper directly -- the thing worth proving is that a failure
    in a node reaches *state*, and that only the assembled graph can show.
    """
    failures = dict(fail_in or {})
    bodies = _bodies(deps)

    def body_for(name: str) -> NodeBody[MigrationState]:
        if name not in failures:
            return bodies[name]

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
    deps: GraphDeps,
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

    `interrupt_before` is LangGraph's own breakpoint, not spec 8.2's
    `interrupt()`. Phase 4 needed somewhere to stop and continue in order to
    prove usage survives a resume; the decision machinery is Phase 7's.
    """
    return build_graph(deps=deps, fail_in=fail_in).compile(
        checkpointer=checkpointer,
        interrupt_before=list(interrupt_before) if interrupt_before else None,
    )
