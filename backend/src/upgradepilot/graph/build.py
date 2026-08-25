"""Assembling the parent graph.

Spec 8.5's topology, complete: both conditional edges are real. The decision
predicate before `human_review` reads `pending_decisions`, and validation's
bounded repair retry reads the report `validate_plan` just wrote.

Every node has a real body. What remains stubbed is nothing.
"""

from collections.abc import Mapping, Sequence
from typing import Any, Literal

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from upgradepilot.graph.deps import GraphDeps
from upgradepilot.graph.nodes import NodeBody, traced
from upgradepilot.graph.nodes.evidence import (
    make_agentic_rag,
    make_analyze_repo,
    make_inspect_dependency,
)
from upgradepilot.graph.nodes.judgment import make_assess_risk, make_human_review
from upgradepilot.graph.nodes.planning import (
    MAX_PLAN_ATTEMPTS,
    make_finalize,
    make_generate_plan,
    make_validate_plan,
)
from upgradepilot.models.decision import unanswered
from upgradepilot.models.enums import TraceEventKind
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


def attempts_started(state: MigrationState) -> int:
    """How many times plan generation has begun, counted from the trace.

    See `route_after_validate` for why this is not `state["plan_attempts"]`,
    and `graph/rag/state.rounds_started` for the same argument in the other
    loop.
    """
    return sum(
        1
        for event in state["agent_trace"]
        if event.node == "generate_plan" and event.kind is TraceEventKind.NODE_STARTED
    )


def route_after_validate(state: MigrationState) -> Literal["generate_plan", "finalize"]:
    """Spec 8.4's bounded repair: one retry, then finish and say so.

    Three outcomes and no fourth. A passing report finishes. A failing report
    with budget left goes back to `generate_plan`, which is handed the failed
    checks as repair input. A failing report with the budget spent **still
    finishes** -- the run terminates as `COMPLETED_WITH_WARNINGS` with the
    failures shown in the report, because a validator that can be retried
    indefinitely is a validator the generator learns to satisfy by attrition,
    and a run that loops is worse for the user than a run that says what is
    wrong with its own output.

    A missing report finishes too. `validate_plan` always writes one, so
    `None` here means that node itself failed and `traced` recorded it;
    looping back to regenerate a plan whose validator is broken would spend
    the budget learning nothing.

    **The budget is counted from the trace, not from `plan_attempts`.** Same
    reason the RAG loop counts `rounds_started`: `traced` discards a failed
    body's update, so an unexpected exception inside `generate_plan` leaves
    the counter it writes exactly where it was, and a router bounded on that
    counter never terminates -- the run never completes, the API never
    returns, and the only symptom is a checkpoint file growing on disk.
    Measured twice now, in two different loops, which is why it is stated as a
    rule: *a loop bound written by a node body is a bound that stops
    advancing the moment that body fails.* `traced` emits `node_started`
    whatever the body does, so counting those advances unconditionally.
    """
    report = state["validation"]
    if report is None or report.passed:
        return "finalize"
    if attempts_started(state) >= MAX_PLAN_ATTEMPTS:
        return "finalize"
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
        "generate_plan": make_generate_plan(deps.llm),
        "validate_plan": make_validate_plan(deps.store),
        "finalize": make_finalize(),
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
        if earlier == "validate_plan":
            graph.add_conditional_edges(earlier, route_after_validate)
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
