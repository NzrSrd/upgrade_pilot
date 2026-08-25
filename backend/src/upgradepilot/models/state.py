"""`MigrationState`: the graph's channels, and the one custom reducer.

Spec §6. A `TypedDict` with `Annotated` reducers because LangGraph's channel
model requires it, while every value inside is a Pydantic model -- validation
where data lives, merging where merging happens.

**This state is deliberately smaller than spec §6's listing.** The RAG loop
fields (`rag_queries`, `rag_evaluations`, `rag_context`), the judgment fields
(`risk_analysis`, `pending_decision`, `human_decisions`) and the plan fields
(`migration_plan`, `validation`) are absent because the models they hold do
not exist yet; each arrives with the phase that consumes it, where a real
caller can shape it, exactly as the domain models did in Phase 1. Declaring
them now would mean guessing at eight models to satisfy a type annotation.

`llm_calls` carries the append-only usage records that §6.1 keeps instead of a
stored total -- see `models/usage.py` for why a counter is wrong.
"""

import operator
from typing import Annotated, TypedDict

from upgradepilot.models.errors import AppError
from upgradepilot.models.evidence import BreakingChange, SourceRef
from upgradepilot.models.inputs import DependencySpec, RepoRef, UserConstraints
from upgradepilot.models.repo import AffectedFile, RepoAnalysis, SymbolInventory
from upgradepilot.models.trace import TraceEvent
from upgradepilot.models.usage import LLMCall


def merge_sources_by_id(existing: list[SourceRef], new: list[SourceRef]) -> list[SourceRef]:
    """Merge retrieved sources by `source_id`, keeping the best copy of each.

    Spec §6.1: the brief's "avoid duplicate sources where possible" becomes
    structural rather than a rule each call site must remember -- and "where
    possible" is exactly the kind of instruction that holds until the one node
    that forgets. Two RAG iterations routinely return the same document, and
    listing it twice inflates the "sources consulted" count a reader takes as
    breadth of evidence.

    **Highest relevance wins, and the winner is kept whole.** A later, better
    retrieval of the same document should improve what the report shows rather
    than be discarded for arriving second; the surviving entry keeps the
    winning copy's `chunk_id` rather than mixing the two, because a citation
    naming one chunk's id and another chunk's score resolves to text that does
    not support the score beside it.

    **Order is first appearance, and stable.** The trace panel renders this
    list while it is still growing. Sorting by relevance on every append would
    make already-displayed rows jump as later results arrive; a frontend can
    sort a stable list, but it cannot unshuffle an unstable one -- so
    improving a source's score does not move it.

    Neither argument is mutated. LangGraph hands the reducer the live channel
    value, and mutating it in place makes the update visible before the
    checkpoint is written, which is how a resumed run and a fresh one diverge.
    """
    merged: dict[str, SourceRef] = {}
    order: list[str] = []

    for source in [*existing, *new]:
        current = merged.get(source.source_id)
        if current is None:
            merged[source.source_id] = source
            order.append(source.source_id)
        elif source.relevance > current.relevance:
            merged[source.source_id] = source

    return [merged[source_id] for source_id in order]


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

    # Append-only channels. A channel that lost its reducer degrades to
    # last-value, so each node's writes would *replace* the accumulated list
    # rather than extend it and the trace would show only the final node's
    # events -- with nothing else in the system looking wrong.
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
        retrieved_sources=[],
        llm_calls=[],
        agent_trace=[],
        errors=[],
    )
