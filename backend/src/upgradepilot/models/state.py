"""`MigrationState`: the graph's channels, and the one custom reducer.

Spec §6. A `TypedDict` with `Annotated` reducers because LangGraph's channel
model requires it, while every value inside is a Pydantic model -- validation
where data lives, merging where merging happens.

**This state grows phase by phase, and deliberately so.** The RAG loop
fields (`rag_queries`, `rag_evaluations`, `rag_context`) arrived with Phase 5
and `risk_analysis` with Phase 6. The remaining judgment fields
(`pending_decision`, `human_decisions`) and the plan fields
(`migration_plan`, `validation`) are still absent because the models they
hold do not exist yet; each arrives with the phase that consumes it, where a
real caller can shape it, exactly as the domain models did in Phase 1.
Declaring them early would mean guessing at models to satisfy a type
annotation.

`llm_calls` carries the append-only usage records that §6.1 keeps instead of a
stored total -- see `models/usage.py` for why a counter is wrong.
"""

import operator
from collections.abc import Callable
from typing import Annotated, Protocol, TypedDict

from upgradepilot.models.errors import AppError
from upgradepilot.models.evidence import BreakingChange, SourceRef
from upgradepilot.models.inputs import DependencySpec, RepoRef, UserConstraints
from upgradepilot.models.knowledge import RagContext, RagEvaluation, RagQuery
from upgradepilot.models.repo import AffectedFile, RepoAnalysis, SymbolInventory
from upgradepilot.models.risk import RiskAnalysis
from upgradepilot.models.trace import TraceEvent
from upgradepilot.models.usage import LLMCall


class Scored(Protocol):
    """Anything carrying a printable 0-1 relevance. See `merge_by_relevance`."""

    @property
    def relevance(self) -> float: ...


def merge_by_relevance[ItemT: Scored](
    existing: list[ItemT],
    new: list[ItemT],
    *,
    key: Callable[[ItemT], str],
) -> list[ItemT]:
    """Merge two lists of relevance-scored items, best copy of each id wins.

    Extracted so the parent's `retrieved_sources` channel and the RAG
    subgraph's `candidates` channel share one implementation rather than two
    that agree today. They are the same list seen from two sides -- a
    `RetrievedChunk` and the `SourceRef` built from it -- so a policy that
    differed between them would let the trace's source list and the coverage
    gate's candidate list disagree about which copy of a document is the good
    one, and the gate's verdict would then be computed over evidence the
    reader was never shown.

    The three properties this policy has, and why each one:

    - **Highest relevance wins.** A later, better retrieval of the same
      document improves what the report shows rather than being discarded for
      arriving second.
    - **The winner is kept whole.** The surviving entry keeps the winning
      copy's own chunk id and text rather than mixing two copies, because a
      citation naming one chunk's id beside another chunk's score resolves to
      text that does not support the number next to it.
    - **Order is first appearance, and stable.** The trace panel renders this
      list while it is still growing. Sorting by relevance on every append
      would make already-displayed rows jump as later results arrive; a
      frontend can sort a stable list, but it cannot unshuffle an unstable
      one.

    Neither argument is mutated. LangGraph hands the reducer the live channel
    value, and mutating it in place makes the update visible before the
    checkpoint is written, which is how a resumed run and a fresh one diverge.
    """
    merged: dict[str, ItemT] = {}
    order: list[str] = []

    for item in [*existing, *new]:
        identifier = key(item)
        current = merged.get(identifier)
        if current is None:
            merged[identifier] = item
            order.append(identifier)
        elif item.relevance > current.relevance:
            merged[identifier] = item

    return [merged[identifier] for identifier in order]


def merge_sources_by_id(existing: list[SourceRef], new: list[SourceRef]) -> list[SourceRef]:
    """Merge retrieved sources by `source_id`, keeping the best copy of each.

    Spec 6.1: the brief's "avoid duplicate sources where possible" becomes
    structural rather than a rule each call site must remember -- and "where
    possible" is exactly the kind of instruction that holds until the one node
    that forgets. Two RAG iterations routinely return the same document, and
    listing it twice inflates the "sources consulted" count a reader takes as
    breadth of evidence.

    The merge policy itself lives in `merge_by_relevance` above, shared with
    the RAG subgraph's candidate channel -- see that docstring for what each
    of its three properties buys and why the two channels must not diverge.
    """
    return merge_by_relevance(existing, new, key=lambda source: source.source_id)


class MigrationState(TypedDict):
    """The parent graph's state. See the module docstring for what is not here yet."""

    # Inputs -- set once, at the entry point.
    thread_id: str
    repo_ref: RepoRef
    dependency: DependencySpec
    constraints: UserConstraints

    # Evidence, filled in as the run proceeds.
    repo_analysis: RepoAnalysis | None
    affected_files: list[AffectedFile]
    symbol_inventory: SymbolInventory | None
    breaking_changes: list[BreakingChange]
    rag_context: RagContext | None

    # Judgment.
    risk_analysis: RiskAnalysis | None

    # Append-only channels. A channel that lost its reducer degrades to
    # last-value, so each node's writes would *replace* the accumulated list
    # rather than extend it and the trace would show only the final node's
    # events -- with nothing else in the system looking wrong.
    rag_queries: Annotated[list[RagQuery], operator.add]
    rag_evaluations: Annotated[list[RagEvaluation], operator.add]
    retrieved_sources: Annotated[list[SourceRef], merge_sources_by_id]
    llm_calls: Annotated[list[LLMCall], operator.add]
    agent_trace: Annotated[list[TraceEvent], operator.add]
    errors: Annotated[list[AppError], operator.add]


def initial_state(
    *,
    thread_id: str,
    repo_ref: RepoRef,
    dependency: DependencySpec,
    constraints: UserConstraints,
) -> MigrationState:
    """A complete starting state, with every declared key present.

    LangGraph does not default a plain (non-reducer) channel, so a key the
    entry point forgets is simply absent and the first `state["..."]` that
    reads it raises `KeyError` in the middle of a run. Building the whole
    state in one place means that cannot happen one node at a time, and
    `test_initial_state_populates_every_declared_key` fails the moment a new
    channel is declared without being initialised here.
    """
    return MigrationState(
        thread_id=thread_id,
        repo_ref=repo_ref,
        dependency=dependency,
        constraints=constraints,
        repo_analysis=None,
        affected_files=[],
        symbol_inventory=None,
        breaking_changes=[],
        rag_context=None,
        risk_analysis=None,
        rag_queries=[],
        rag_evaluations=[],
        retrieved_sources=[],
        llm_calls=[],
        agent_trace=[],
        errors=[],
    )
