"""Assembling the parent graph.

Spec 8.5's topology. One of its two conditional edges is now real -- the
decision predicate before `human_review` -- and one is still absent:
validation's bounded repair retry is defined by the ten validation checks,
which are Phase 8's, and an edge wired now would have to guess at its own
condition.

What is real: the evidence layer end to end (`analyze_repo`,
`inspect_dependency`, and the retrieval subgraph behind `agentic_rag`), the
judgment layer through `human_review`, the state channels and their reducers,
the checkpointer, the trace, the usage records, and rule 20's error handling.
"""

from collections.abc import Mapping, Sequence
from typing import Any, Literal

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from upgradepilot.graph.deps import GraphDeps
from upgradepilot.graph.nodes import NodeBody, make_stub, traced
from upgradepilot.graph.nodes.evidence import (
    make_agentic_rag,
    make_analyze_repo,
    make_inspect_dependency,
)
from upgradepilot.graph.nodes.judgment import make_assess_risk, make_human_review
from upgradepilot.models.decision import unanswered
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
"""The nodes every run passes through, in order.

`human_review` is deliberately not in this tuple even though it now exists:
it is the one node a run may legitimately skip, so a list used to assert
"every node ran" must not contain it. It is added separately below, behind
the conditional edge that decides whether it fires at all.
"""

HUMAN_REVIEW = "human_review"


def route_after_assess_risk(state: MigrationState) -> Literal["human_review", "generate_plan"]:
    """Pause for a human only when there is a real question still waiting.

    Used for both edges into and out of `human_review`, deliberately: one
    predicate, evaluated the same way whether the run is arriving at the
    question stage or coming back for the next question. Two copies would be
    two chances to disagree about when a run is finished asking.

    Spec 8.2: "If constraints decide it, no interrupt occurs." The check is
    for an *unanswered* question rather than for any question at all, because
    this router runs again on every resume: after the last answer lands,
    `pending_decisions` is still full and `human_decisions` now matches it, so
    a router asking "are there decisions?" would send the run back into
    `human_review` forever.
    """
    if unanswered(state["pending_decisions"], state["human_decisions"]):
        return "human_review"
    return "generate_plan"


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
        "assess_risk": make_assess_risk(deps.llm),
        HUMAN_REVIEW: make_human_review(),
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
    for name in (*NODE_SEQUENCE, HUMAN_REVIEW):
        # `type: ignore[call-overload]`, for the reason already established in
        # `tests/graph/test_langgraph_contract.py`: langgraph resolves the
        # contravariant NodeInputT per `add_node` call rather than once per
        # graph, so which call trips the overload set is an artifact of how
        # many precede it, not of this callable's shape.
        graph.add_node(name, traced(name, body_for(name)))  # type: ignore[call-overload]

    graph.add_edge(START, NODE_SEQUENCE[0])
    for earlier, later in zip(NODE_SEQUENCE, NODE_SEQUENCE[1:], strict=False):
        if earlier == "assess_risk":
            # Spec 8.5's first conditional edge. `assess_risk` has already
            # decided *whether* there is a question -- the predicate lives in
            # `services/strategy/catalog.py` and its answer is in
            # `pending_decisions` -- so the router reads the answer rather
            # than recomputing it. A predicate evaluated twice is a predicate
            # that can disagree with the trace event explaining it.
            graph.add_conditional_edges(earlier, route_after_assess_risk)
            continue
        graph.add_edge(earlier, later)
    # `human_review` routes through the same predicate it was reached by.
    # It answers one question per execution (see the node's own comment), so
    # a run with two questions comes back here, finds the second still
    # unanswered, and pauses again -- which is what makes each answer reach
    # `human_decisions` before the next question is asked.
    graph.add_conditional_edges(HUMAN_REVIEW, route_after_assess_risk)
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
