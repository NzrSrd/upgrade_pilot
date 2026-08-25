"""`MigrationState`'s channels and the one custom reducer among them.

Spec §6: the state is a `TypedDict` with `Annotated` reducers because
LangGraph's channel model requires it, while every value inside is a Pydantic
model. Validation where data lives, merging where merging happens.

`merge_sources_by_id` is the only hand-written reducer. Spec §6.1: the brief's
"avoid duplicate sources where possible" becomes structural rather than a rule
each call site has to remember -- and "where possible" is exactly the kind of
instruction that holds until the one node that forgets.
"""

import operator
from typing import get_args, get_type_hints

from upgradepilot.models.enums import SourceType
from upgradepilot.models.evidence import SourceRef
from upgradepilot.models.inputs import (
    DependencySpec,
    LocalRepoRef,
    UserConstraints,
)
from upgradepilot.models.state import MigrationState, initial_state, merge_sources_by_id


def _a_repo_ref() -> LocalRepoRef:
    return LocalRepoRef(path="/tmp/repo")


def _a_dependency() -> DependencySpec:
    return DependencySpec(name="pydantic", current_version="1.10.13", target_version="2.9.0")


def _constraints() -> UserConstraints:
    return UserConstraints()


def a_source(source_id: str, *, chunk: str = "chunk-0", relevance: float = 0.5) -> SourceRef:
    return SourceRef(
        source_id=source_id,
        title=f"Title for {source_id}",
        source_type=SourceType.MIGRATION_GUIDE,
        url_or_reference="https://example.invalid/doc",
        chunk_id=f"{source_id}#{chunk}",
        relevance=relevance,
    )


# -- merge_sources_by_id ----------------------------------------------------


def test_a_source_retrieved_twice_appears_once() -> None:
    """Two RAG iterations routinely return the same document. Listing it
    twice inflates the "sources consulted" count the report prints, which a
    reader reasonably takes as breadth of evidence."""
    merged = merge_sources_by_id([a_source("doc-a")], [a_source("doc-a")])

    assert [s.source_id for s in merged] == ["doc-a"]


def test_the_highest_relevance_copy_wins() -> None:
    """Spec §6.1. A later, better-scoring retrieval of the same document
    should improve what the report shows, not be discarded because the
    document was seen first."""
    merged = merge_sources_by_id(
        [a_source("doc-a", chunk="chunk-0", relevance=0.2)],
        [a_source("doc-a", chunk="chunk-7", relevance=0.9)],
    )

    assert len(merged) == 1
    assert merged[0].relevance == 0.9
    assert merged[0].chunk_id == "doc-a#chunk-7", (
        "the surviving entry must be the winning chunk whole, not a mix of both"
    )


def test_a_lower_relevance_copy_does_not_displace_a_better_one() -> None:
    """The direction that fails silently: taking the newest would let a weak
    third-iteration hit overwrite the strong first-iteration one, quietly
    lowering every relevance the report shows."""
    merged = merge_sources_by_id(
        [a_source("doc-a", chunk="chunk-7", relevance=0.9)],
        [a_source("doc-a", chunk="chunk-0", relevance=0.2)],
    )

    assert merged[0].relevance == 0.9
    assert merged[0].chunk_id == "doc-a#chunk-7"


def test_distinct_sources_are_all_kept() -> None:
    """A merge that collapsed everything would pass the deduplication tests
    above by destroying the evidence."""
    merged = merge_sources_by_id([a_source("doc-a")], [a_source("doc-b"), a_source("doc-c")])

    assert {s.source_id for s in merged} == {"doc-a", "doc-b", "doc-c"}


def test_order_is_first_appearance_and_stable() -> None:
    """The trace panel renders this list while it is still growing. Sorting
    by relevance on every append would make already-displayed rows jump as
    later results arrive; the frontend can sort a stable list, but it cannot
    unshuffle an unstable one.

    The relevances are chosen so first-appearance order and relevance order
    *disagree* -- doc-b is seen first and scores worst. An earlier version of
    this fixture had the two coincide, so a reducer that sorted by relevance
    on every call passed it while doing exactly what the test forbids.
    """
    merged = merge_sources_by_id(
        [a_source("doc-b", relevance=0.1), a_source("doc-a", relevance=0.9)],
        [a_source("doc-c", relevance=0.5), a_source("doc-b", relevance=0.2)],
    )

    assert [s.source_id for s in merged] == ["doc-b", "doc-a", "doc-c"]
    assert [s.relevance for s in merged] != sorted((s.relevance for s in merged), reverse=True), (
        "the fixture no longer distinguishes insertion order from relevance order"
    )
    assert merged[0].relevance == 0.2, "improving a source must not move it"


def test_merging_is_idempotent() -> None:
    """Same reason `UsageSummary` deduplicates: a replayed node re-applies its
    writes, and a channel that grew on every replay would report more sources
    consulted after a resume than before it."""
    existing = [a_source("doc-a", relevance=0.4)]
    new = [a_source("doc-b", relevance=0.6)]

    once = merge_sources_by_id(existing, new)
    twice = merge_sources_by_id(once, new)

    assert once == twice


def test_merging_does_not_mutate_either_input() -> None:
    """LangGraph hands the reducer the live channel value. Mutating it in
    place makes the update visible before the checkpoint is written, which is
    how a resumed run and a fresh one diverge."""
    existing = [a_source("doc-a")]
    new = [a_source("doc-b")]

    merge_sources_by_id(existing, new)

    assert [s.source_id for s in existing] == ["doc-a"]
    assert [s.source_id for s in new] == ["doc-b"]


def test_merging_onto_nothing_returns_the_new_sources() -> None:
    """The first retrieval of a run, and the shape LangGraph starts every
    reducer channel in."""
    assert [s.source_id for s in merge_sources_by_id([], [a_source("doc-a")])] == ["doc-a"]


def test_merging_nothing_onto_existing_sources_keeps_them() -> None:
    """A retrieval round that found nothing must not clear what earlier
    rounds found."""
    existing = [a_source("doc-a")]

    assert merge_sources_by_id(existing, []) == existing


# -- the state's own shape --------------------------------------------------


def test_the_append_only_channels_use_the_reducers_the_spec_names() -> None:
    """A channel that lost its `Annotated` reducer degrades to last-value:
    each node's writes would *replace* the accumulated list rather than
    extend it, so the trace and the call records would show only whatever the
    final node wrote. Nothing else in the system would look wrong.
    """
    hints = get_type_hints(MigrationState, include_extras=True)

    for channel in ("llm_calls", "agent_trace", "errors"):
        assert operator.add in get_args(hints[channel]), channel
    assert merge_sources_by_id in get_args(hints["retrieved_sources"])


def test_initial_state_populates_every_declared_key() -> None:
    """LangGraph does not default a plain (non-reducer) channel, so a key the
    entry point forgets is simply absent and every `state["..."]` on it raises
    `KeyError` mid-run. Building the whole state in one place means that
    cannot happen one node at a time.
    """
    state = initial_state(
        thread_id="t-1",
        repo_ref=_a_repo_ref(),
        dependency=_a_dependency(),
        constraints=_constraints(),
    )

    assert set(state) == set(get_type_hints(MigrationState, include_extras=True))


def test_initial_state_starts_every_append_only_channel_empty() -> None:
    state = initial_state(
        thread_id="t-1",
        repo_ref=_a_repo_ref(),
        dependency=_a_dependency(),
        constraints=_constraints(),
    )

    assert state["llm_calls"] == []
    assert state["agent_trace"] == []
    assert state["errors"] == []
    assert state["retrieved_sources"] == []
