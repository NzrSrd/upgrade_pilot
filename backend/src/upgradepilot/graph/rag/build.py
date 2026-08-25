"""Wiring the retrieval loop, and the two conditions that end it.

Spec 7.3's topology, with both of its branches:

    START -> plan_retrieval -> [necessary?] -> retrieve -> evaluate_retrieval
                    ^               |                              |
                    |               +-------------> build_context <+
                    +--------------- [not sufficient, budget left] -+

The loop bound is not a safety net here; it is the design. `MAX_RAG_ITERATIONS`
caps how many rounds a run may spend, and the run that hits the cap reports
`ITERATION_LIMIT` rather than pretending it found what it was looking for.
"""

from typing import Any, Literal

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from upgradepilot.graph.nodes.base import traced
from upgradepilot.graph.rag.nodes import (
    make_build_context,
    make_evaluate_retrieval,
    make_plan_retrieval,
    make_retrieve,
)
from upgradepilot.graph.rag.state import RAGState, rounds_started
from upgradepilot.services.knowledge.store import DEFAULT_LIMIT, KnowledgeStore
from upgradepilot.services.llm.tracked import TrackedLLM


def route_after_plan(state: RAGState) -> Literal["retrieve", "build_context"]:
    """Skip the loop when there is nothing to look up, or when planning died.

    The first condition reads the flag `plan_retrieval` set rather than
    re-deriving it, so the decision is taken once, in the node that recorded
    its reason in the trace. A predicate that recomputed "is the inventory
    empty?" here would be a second implementation of the same rule with no
    trace event behind it, and the two could disagree after any change to
    what counts as a usage site.

    The second condition is the failure case. `plan_retrieval` already
    handles a provider outage itself -- it records the error and falls back to
    a query composed from the symbol inventory -- so reaching `traced`'s catch
    means an *unexpected* exception, which is a bug rather than a condition,
    and retrying a bug achieves nothing. Its update is discarded when that
    happens, so `iteration` lags `rounds_started` by exactly one; that
    inequality is the signal, and the loop ends rather than sending `retrieve`
    to re-run the previous round's queries against a store that would bill
    for them again.
    """
    if not state["retrieval_necessary"]:
        return "build_context"
    if state["iteration"] != rounds_started(state):
        return "build_context"
    return "retrieve"


def route_after_evaluate(state: RAGState) -> Literal["plan_retrieval", "build_context"]:
    """Iterate only while another round could actually change the answer.

    Three ways to stop, and the order matters only for readability -- they are
    mutually compatible and any of them ends the loop:

    - the evidence is sufficient (both the model and the gate agree);
    - the knowledge base is unreachable, so another round would issue the same
      failing query and record the same error a second time;
    - the iteration budget is spent.

    The budget is measured with `rounds_started`, **not** with `iteration`.
    They are equal on every successful round; they diverge exactly when a node
    body failed, and that is the case where the difference decides whether the
    graph terminates at all. See `rounds_started` for the measurement that
    caused this to be written down.

    `>=` rather than `==`: `max_iterations` arrives from configuration, and a
    value of zero must stop the loop rather than run it forever looking for an
    equality it will never reach.
    """
    evaluations = state["rag_evaluations"]
    if evaluations and evaluations[-1].sufficient:
        return "build_context"
    if state["kb_unavailable"]:
        return "build_context"
    if rounds_started(state) >= state["max_iterations"]:
        return "build_context"
    return "plan_retrieval"


def build_rag_graph(
    *,
    llm: TrackedLLM,
    store: KnowledgeStore,
    limit: int = DEFAULT_LIMIT,
) -> StateGraph[RAGState, Any, RAGState, RAGState]:
    """Assemble the subgraph. Every node wears `traced`, as the parent's do.

    Rule 20 is a rule about nodes, not about `MigrationState`: a subgraph node
    that died unrecorded would take the retrieval loop down with no explanation
    the user could act on, which is exactly the failure the wrapper exists to
    prevent one level up.
    """
    graph = StateGraph(RAGState)
    graph.add_node("plan_retrieval", traced("plan_retrieval", make_plan_retrieval(llm)))  # type: ignore[call-overload]
    graph.add_node("retrieve", traced("retrieve", make_retrieve(store, limit=limit)))  # type: ignore[call-overload]
    graph.add_node(  # type: ignore[call-overload]
        "evaluate_retrieval", traced("evaluate_retrieval", make_evaluate_retrieval(llm))
    )
    graph.add_node("build_context", traced("build_context", make_build_context()))  # type: ignore[call-overload]

    graph.add_edge(START, "plan_retrieval")
    graph.add_conditional_edges("plan_retrieval", route_after_plan)
    graph.add_edge("retrieve", "evaluate_retrieval")
    graph.add_conditional_edges("evaluate_retrieval", route_after_evaluate)
    graph.add_edge("build_context", END)
    return graph


def compile_rag_graph(
    *,
    llm: TrackedLLM,
    store: KnowledgeStore,
    limit: int = DEFAULT_LIMIT,
) -> CompiledStateGraph[RAGState, Any, RAGState, RAGState]:
    """Compile the subgraph with **no checkpointer**, deliberately.

    The subgraph runs to completion inside one parent node, so its
    intermediate states are not resume points: the parent checkpoints before
    and after `agentic_rag`, and a resume re-enters at the node boundary.
    Giving the child its own checkpointer would write a second, parallel
    thread history that nothing reads and that no `thread_id` in the API
    contract names.
    """
    return build_rag_graph(llm=llm, store=store, limit=limit).compile()
