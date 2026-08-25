"""`RAGState`: the retrieval loop's own channels, and how they meet the parent.

Spec 6.4. The subgraph has two kinds of channel and the distinction is the
whole design:

- **Child-only loop fields** (`iteration`, `candidates`, `uncovered_symbols`,
  `max_iterations`, `kb_unavailable`) exist for the duration of the loop and
  are meaningless outside it. A parent channel for `iteration` would be a
  counter the parent can neither interpret nor reset.
- **Shared channels** (`agent_trace`, `llm_calls`, `errors`, `rag_queries`,
  `rag_evaluations`, `retrieved_sources`) carry *identical names and
  reducers* to the parent's. Identical on purpose: the wrapper in
  `graph/nodes/evidence.py` maps the child's accumulated values straight back
  onto the parent's channels, and a reducer that differed on either side
  would merge the same list two different ways depending on which graph it
  was in.

**The child's shared channels start empty, always.** Seeding them with the
parent's current trace would return that trace as the wrapper's update, and
the parent's `operator.add` would then append it to itself -- every event
before `agentic_rag` duplicated, with the run still completing and the panel
simply showing everything twice.
"""

import operator
from typing import Annotated, TypedDict

from upgradepilot.models.errors import AppError
from upgradepilot.models.evidence import BreakingChange, SourceRef
from upgradepilot.models.inputs import DependencySpec
from upgradepilot.models.knowledge import (
    RagContext,
    RagEvaluation,
    RagQuery,
    RetrievedChunk,
)
from upgradepilot.models.repo import SymbolInventory
from upgradepilot.models.state import merge_by_relevance, merge_sources_by_id
from upgradepilot.models.trace import TraceEvent
from upgradepilot.models.usage import LLMCall


def merge_chunks_by_id(
    existing: list[RetrievedChunk], new: list[RetrievedChunk]
) -> list[RetrievedChunk]:
    """Accumulate retrieved chunks across iterations, best copy of each id.

    **Accumulate, not replace**, and that is the point of having a reducer
    here at all. Without one this channel is last-value, so round two's
    results would delete round one's -- and the coverage gate, which runs
    over everything retrieved so far, would grade the final round in
    isolation. A loop that found `validator` in round one and `Config` in
    round two would then conclude, correctly for its inputs and wrongly for
    the run, that `validator` was never covered.

    Deduplicated by `chunk_id` rather than `source_id`: two chunks of one
    long document are two distinct pieces of evidence and both may be cited,
    while the same chunk returned by two queries would double-count in the
    coverage judgement built on top of it.

    Same policy as the parent's `retrieved_sources` channel, sharing one
    implementation with it -- see `models/state.merge_by_relevance` for why
    the two must not diverge.
    """
    return merge_by_relevance(existing, new, key=lambda chunk: chunk.chunk_id)


class RAGState(TypedDict):
    """The retrieval subgraph's state. See the module docstring for the split."""

    # Inputs, mapped in by the wrapper and never written by a node.
    dependency: DependencySpec
    symbol_inventory: SymbolInventory | None
    max_iterations: int

    # Loop control, child-only.
    iteration: int
    retrieval_necessary: bool
    """Whether there is anything worth asking the corpus about.

    Decided once, deterministically, by `plan_retrieval`: a repository with no
    usage sites has no symbols to look up, and a loop that queried anyway
    would spend three rounds and a model call proving it. Stored as its own
    field rather than inferred from an empty query list, because "no query was
    planned this round" and "no query will ever be planned" route to different
    places and an inference cannot tell them apart on iteration two.

    Deliberately **not** something the model decides. Letting an LLM answer
    "is retrieval warranted?" hands it a way to skip gathering the evidence
    its own later answers are graded against, and the honest version of that
    question -- are there any usage sites? -- is arithmetic.
    """

    kb_unavailable: bool
    """Set when the store could not be reached. Ends the loop rather than
    retrying: a knowledge base that is down for query one is down for query
    two, and three rounds of the same failure produce three identical errors
    and no evidence."""

    candidates: Annotated[list[RetrievedChunk], merge_chunks_by_id]
    uncovered_symbols: list[str]

    # Shared with the parent, under identical names and reducers.
    rag_queries: Annotated[list[RagQuery], operator.add]
    rag_evaluations: Annotated[list[RagEvaluation], operator.add]
    retrieved_sources: Annotated[list[SourceRef], merge_sources_by_id]
    llm_calls: Annotated[list[LLMCall], operator.add]
    agent_trace: Annotated[list[TraceEvent], operator.add]
    errors: Annotated[list[AppError], operator.add]

    # Outputs the wrapper reads back.
    rag_context: RagContext | None
    breaking_changes: list[BreakingChange]


def initial_rag_state(
    *,
    dependency: DependencySpec,
    symbol_inventory: SymbolInventory | None,
    max_iterations: int,
) -> RAGState:
    """A complete starting state for one pass through the loop.

    Every declared key is populated here for the same reason
    `initial_state` does it for the parent: LangGraph does not default a
    plain channel, so a key the entry point forgets is simply absent and the
    first node that reads it raises `KeyError` mid-run.
    """
    return RAGState(
        dependency=dependency,
        symbol_inventory=symbol_inventory,
        max_iterations=max_iterations,
        iteration=0,
        retrieval_necessary=True,
        kb_unavailable=False,
        candidates=[],
        uncovered_symbols=[],
        rag_queries=[],
        rag_evaluations=[],
        retrieved_sources=[],
        llm_calls=[],
        agent_trace=[],
        errors=[],
        rag_context=None,
        breaking_changes=[],
    )
