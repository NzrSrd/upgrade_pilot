"""Ingestion into, and retrieval out of, the ChromaDB knowledge base.

Real Chroma (spec §11 layer 2), never a mock: the facts this depends on --
list-valued metadata, `$contains` being exact-element, `$in` silently
returning nothing against a list -- are properties of the pinned
`chromadb==1.5.9` that a mock would let us assume wrongly. `test_chroma_contract.py`
pins those facts; this file pins what UpgradePilot builds on top of them.
"""

from copy import deepcopy
from datetime import date
from pathlib import Path
from typing import cast

import pytest
from chromadb.api.types import Embeddable, EmbeddingFunction

from tests.knowledge.fake_embedding import LexicalEmbedding, fake_embedding_function
from upgradepilot.models.enums import Severity, SourceType
from upgradepilot.models.errors import ErrorCode, KnowledgeBaseUnavailableError
from upgradepilot.models.knowledge import CorpusDocument
from upgradepilot.services.knowledge.store import CORPUS_CONFIGURATION, KnowledgeStore


def a_document(
    source_id: str,
    *,
    title: str = "A documented change",
    body: str = "Some prose about a change.",
    symbols: tuple[str, ...] = ("validator",),
    source_type: SourceType = SourceType.MIGRATION_GUIDE,
    dependency: str = "pydantic",
    major: int = 2,
    severity: Severity = Severity.HIGH,
) -> CorpusDocument:
    return CorpusDocument(
        source_id=source_id,
        title=title,
        source_type=source_type,
        dependency=dependency,
        from_version="1.x",
        to_version=f"{major}.0",
        to_version_major=major,
        affected_symbols=symbols,
        severity=severity,
        url_or_reference="https://example.invalid/doc",
        created_at=date(2026, 8, 24),
        tags=("a-tag",),
        body=body,
        path=f"{source_id}.md",
    )


CORPUS = (
    a_document(
        "pydantic-v2#validator",
        title="@validator replaced by @field_validator",
        body="The validator decorator was renamed. Use field_validator instead.",
        symbols=("validator", "root_validator"),
    ),
    a_document(
        "pydantic-v2#config",
        title="class Config replaced by model_config",
        body="The nested Config class is replaced by a model_config ConfigDict assignment.",
        symbols=("Config",),
    ),
    a_document(
        "pydantic-v2#configdict",
        title="ConfigDict is the new settings container",
        body="ConfigDict carries the settings that the nested class used to hold.",
        symbols=("ConfigDict",),
    ),
    a_document(
        "sqlalchemy-2#select",
        title="Legacy Query API gives way to select()",
        body="The Query object is replaced by select() in SQLAlchemy 2.0.",
        symbols=("Query",),
        dependency="sqlalchemy",
    ),
    a_document(
        "pydantic-v3#speculative",
        title="A change targeting a different major",
        body="Something about a future major version of the library.",
        symbols=("validator",),
        major=3,
    ),
    a_document(
        "internal#adr-migrations",
        title="How this team approaches dependency migrations",
        body="Prefer incremental migration behind a compatibility shim.",
        symbols=(),
        source_type=SourceType.ADR,
    ),
)


@pytest.fixture
def store(tmp_path: Path) -> KnowledgeStore:
    opened = KnowledgeStore.open(tmp_path / "chroma", embedding_function=fake_embedding_function())
    opened.ingest(CORPUS)
    return opened


# -- ingestion -------------------------------------------------------------


def test_ingest_reports_what_it_actually_wrote(tmp_path: Path) -> None:
    """The count is the operator's only confirmation that the corpus they
    authored is the corpus that got indexed. A report that echoes the input
    rather than the store would hide a write that silently did nothing."""
    opened = KnowledgeStore.open(tmp_path / "chroma", embedding_function=fake_embedding_function())

    report = opened.ingest(CORPUS)

    assert report.documents == len(CORPUS)
    assert report.chunks == opened.count()
    assert report.chunks >= len(CORPUS)


def test_ingesting_twice_replaces_rather_than_duplicates(tmp_path: Path) -> None:
    """`chunk_id` is deterministic, so a re-ingest of unchanged content must
    land on the same ids. Duplicated chunks would let one document occupy
    several of the top-`n` slots and crowd out the others -- degrading
    retrieval while every count still looked plausible."""
    opened = KnowledgeStore.open(tmp_path / "chroma", embedding_function=fake_embedding_function())
    opened.ingest(CORPUS)
    first = opened.count()

    opened.ingest(CORPUS)

    assert opened.count() == first


def test_ingested_metadata_survives_a_reopen(tmp_path: Path) -> None:
    """Ingestion is a separate run from serving, so nothing may live only in
    the writing client's memory. `affected_symbols` in particular must come
    back as a real list, since the symbol join filters on it in the store."""
    path = tmp_path / "chroma"
    writer = KnowledgeStore.open(path, embedding_function=fake_embedding_function())
    writer.ingest(CORPUS)

    reader = KnowledgeStore.open(path, embedding_function=fake_embedding_function())
    results = reader.search("validator renamed", dependency="pydantic")

    assert results
    found = next(r for r in results if r.source_id == "pydantic-v2#validator")
    assert found.affected_symbols == ("validator", "root_validator")
    assert found.severity is Severity.HIGH
    assert found.source_type is SourceType.MIGRATION_GUIDE
    assert found.to_version_major == 2


# -- every result can cite itself ------------------------------------------


def test_every_result_carries_the_metadata_a_citation_needs(store: KnowledgeStore) -> None:
    """CLAUDE.md rule 1. A retrieved chunk that cannot name its own source is
    not evidence -- it is prose the model may then repeat uncited."""
    results = store.search("validator renamed", dependency="pydantic")

    assert results
    for result in results:
        ref = result.to_source_ref()
        assert ref.source_id == result.source_id
        assert ref.chunk_id.startswith(result.source_id)
        assert ref.title
        assert ref.url_or_reference
        assert 0.0 <= ref.relevance <= 1.0


def test_relevance_ranks_the_document_that_shares_the_query_wording_first(
    store: KnowledgeStore,
) -> None:
    """Not a claim about semantic quality -- the offline embedding is
    lexical. It is a claim that the distance-to-relevance mapping preserves
    order rather than inverting it, which is the part that could silently be
    backwards while every other test still passed.
    """
    results = store.search("the nested Config class and model_config", dependency="pydantic")

    assert results[0].source_id == "pydantic-v2#config"
    assert results[0].relevance >= results[-1].relevance


# -- scalar filters --------------------------------------------------------


def test_the_dependency_filter_excludes_another_library(store: KnowledgeStore) -> None:
    results = store.search("Query replaced by select", dependency="pydantic")

    assert results
    assert all(r.dependency == "pydantic" for r in results)
    assert "sqlalchemy-2#select" not in {r.source_id for r in results}


def test_the_dependency_filter_is_matched_on_the_canonical_name(store: KnowledgeStore) -> None:
    """Both sides go through `canonicalize_name`, so a caller asking for
    `Pydantic` must reach documents ingested as `pydantic`. Without it the
    filter matches nothing and an empty result reads as "no known breaking
    changes" rather than "you spelled it differently"."""
    results = store.search("validator renamed", dependency="Pydantic")

    assert results
    assert all(r.dependency == "pydantic" for r in results)


def test_the_major_version_filter_excludes_another_major(store: KnowledgeStore) -> None:
    results = store.search("validator", dependency="pydantic", to_version_major=2)

    assert results
    assert "pydantic-v3#speculative" not in {r.source_id for r in results}
    assert all(r.to_version_major == 2 for r in results)


def test_a_source_type_filter_narrows_to_those_types(store: KnowledgeStore) -> None:
    results = store.search(
        "how to approach a migration",
        dependency="pydantic",
        source_types=(SourceType.ADR,),
    )

    assert results
    assert all(r.source_type is SourceType.ADR for r in results)


def test_one_filter_and_several_filters_both_work(store: KnowledgeStore) -> None:
    """Chroma raises `ValueError` on a single-clause `$and`/`$or` (recorded in
    ADR-001). A where-builder that always wraps would therefore break on the
    commonest query of all -- dependency alone -- so both arities are pinned.
    """
    one = store.search("validator", dependency="pydantic")
    several = store.search(
        "validator",
        dependency="pydantic",
        to_version_major=2,
        source_types=(SourceType.MIGRATION_GUIDE, SourceType.CHANGELOG),
    )

    assert one
    assert several
    assert {r.source_id for r in several} <= {r.source_id for r in one}


# -- the symbol join, both directions --------------------------------------


def test_a_symbol_filter_returns_only_documents_naming_that_symbol(
    store: KnowledgeStore,
) -> None:
    results = store.search("anything at all", dependency="pydantic", symbols=("Config",))

    assert {r.source_id for r in results} == {"pydantic-v2#config"}


def test_a_prefix_colliding_symbol_does_not_match(store: KnowledgeStore) -> None:
    """THE negative direction, called out explicitly in PLANNING.md and spec
    §11. `$contains` is exact-element; a substring match would make a query
    for `Config` return the `ConfigDict` document and the report would cite
    a change to a symbol the repository never uses.

    Both halves are asserted in one test on purpose. "`configdict` is absent"
    alone is satisfied by a filter that returns *nothing* -- which is exactly
    what the wrong operator does here, since `$in` against list metadata
    silently returns an empty set (ADR-001). Requiring the `Config` document
    to be present is what separates "matched precisely" from "matched
    nothing".
    """
    results = store.search("anything at all", dependency="pydantic", symbols=("Config",))

    found = {r.source_id for r in results}
    assert "pydantic-v2#config" in found
    assert "pydantic-v2#configdict" not in found


def test_a_symbol_that_is_a_substring_of_a_real_one_matches_nothing(
    store: KnowledgeStore,
) -> None:
    """The same fact from the other side: `valid` is a prefix of `validator`
    and must match neither it nor anything else. A substring operator would
    make every short symbol name a wildcard."""
    assert store.search("anything at all", dependency="pydantic", symbols=("valid",)) == ()


def test_several_symbols_are_joined_as_a_union(store: KnowledgeStore) -> None:
    results = store.search(
        "anything at all",
        dependency="pydantic",
        symbols=("Config", "root_validator"),
    )

    assert {r.source_id for r in results} == {"pydantic-v2#config", "pydantic-v2#validator"}


def test_a_symbol_nothing_documents_returns_nothing(store: KnowledgeStore) -> None:
    """An empty result is the honest answer, and it must be distinguishable
    from a filter that silently matched everything. §7.3 turns exactly this
    into an uncovered symbol rather than into a confident claim."""
    results = store.search("anything", dependency="pydantic", symbols=("no_such_symbol",))

    assert results == ()


def test_results_are_annotated_with_the_symbols_they_actually_cover(
    store: KnowledgeStore,
) -> None:
    """`matched_symbols` is the input to the deterministic sufficiency gate.
    Annotating a chunk with symbols its document does not name would let the
    gate believe a symbol is covered when nothing documents it."""
    results = store.search(
        "validator and config",
        dependency="pydantic",
        symbols=("validator", "Config"),
    )

    by_id = {r.source_id: r for r in results}
    assert by_id["pydantic-v2#validator"].matched_symbols == ("validator",)
    assert by_id["pydantic-v2#config"].matched_symbols == ("Config",)


def test_a_semantic_result_covering_no_queried_symbol_is_annotated_empty(
    store: KnowledgeStore,
) -> None:
    """The complement. A chunk reached semantically that covers none of the
    asked-about symbols is a legitimate result, and the gate must be able to
    see that it covers nothing rather than inferring coverage from its mere
    presence.

    The documents retrieved here deliberately *do* name symbols -- just not
    the one asked about. An earlier version asked about the ADR, whose
    `affected_symbols` is empty anyway, so `matched_symbols` was empty for
    the wrong reason and the test passed even when the annotation ignored the
    query entirely.
    """
    results = store.search(
        "validator renamed",
        dependency="pydantic",
        symbol_annotations=("Query",),
    )

    assert results
    assert any(r.affected_symbols for r in results), (
        "the fixture must return documents that name symbols, or this asserts nothing"
    )
    assert all(r.matched_symbols == () for r in results)


# -- bounds and failure ----------------------------------------------------


def test_limit_bounds_the_result_set(store: KnowledgeStore) -> None:
    results = store.search("validator config query migration", dependency="pydantic", limit=2)

    assert len(results) <= 2


def test_results_are_deduplicated_by_chunk_id(store: KnowledgeStore) -> None:
    results = store.search("validator config", dependency="pydantic", limit=10)

    chunk_ids = [r.chunk_id for r in results]
    assert len(chunk_ids) == len(set(chunk_ids))


def test_an_unopenable_store_raises_kb_unavailable(tmp_path: Path) -> None:
    """Spec §7.3: Chroma unreachable becomes `AppError(KB_UNAVAILABLE)` and
    an empty context flagged `evidence_available: False`, which §8.1 turns
    into a hard confidence ceiling. That chain starts with a typed error --
    a bare `OSError` escaping here would be caught, if at all, as an internal
    fault and the ceiling would never engage."""
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("this is a file, not a chroma directory", encoding="utf-8")

    with pytest.raises(KnowledgeBaseUnavailableError) as excinfo:
        KnowledgeStore.open(blocker, embedding_function=fake_embedding_function())

    assert excinfo.value.code is ErrorCode.KB_UNAVAILABLE
    assert excinfo.value.to_app_error().retryable is True


def test_searching_a_store_whose_collection_is_gone_raises_kb_unavailable(
    tmp_path: Path,
) -> None:
    """Opening succeeded and the corpus then disappeared -- a deleted
    collection, a wiped directory. The failure must still be the typed one,
    because it reaches the graph at exactly the same place."""
    path = tmp_path / "chroma"
    opened = KnowledgeStore.open(path, embedding_function=fake_embedding_function())
    opened.ingest(CORPUS)
    opened.drop()

    with pytest.raises(KnowledgeBaseUnavailableError):
        opened.search("validator", dependency="pydantic")


def test_two_stores_with_different_embedders_do_not_interfere(tmp_path: Path) -> None:
    """Opening a store must not leave anything behind that changes what the
    next one gets.

    Found by running the suite with `--live`: the live embedding test opened a
    store with the real embedder, and every offline test after it failed at
    fixture setup with an embedding-function conflict. The cause was not test
    isolation but shared mutable state in the store itself — chroma **writes**
    the embedding function into the `configuration` mapping it is handed, so a
    module-level configuration constant is stamped by whichever store opens
    first and then forces that embedder on every store after it.

    The consequence outside the test suite is the same and worse: a process
    that opens the corpus store and any second collection would have the first
    one's embedder imposed on it, or would fail outright.
    """

    class OtherEmbedding(LexicalEmbedding):
        @staticmethod
        def name() -> str:
            return "other-test-embedding"

    first = KnowledgeStore.open(tmp_path / "a", embedding_function=fake_embedding_function())
    second = KnowledgeStore.open(
        tmp_path / "b",
        embedding_function=cast(EmbeddingFunction[Embeddable], OtherEmbedding()),
    )

    first.ingest(CORPUS[:1])
    second.ingest(CORPUS[:1])

    assert first.count() == second.count() >= 1


def test_opening_a_store_does_not_mutate_the_shared_configuration(tmp_path: Path) -> None:
    """The invariant directly, so the cause is named and not only its symptom.

    A future refactor that reintroduces a shared configuration object would
    fail the test above only when two *different* embedders meet — which is
    rare, and did not happen for days. This one fails immediately.
    """
    before = deepcopy(CORPUS_CONFIGURATION)

    KnowledgeStore.open(tmp_path / "chroma", embedding_function=fake_embedding_function())

    assert before == CORPUS_CONFIGURATION, (
        "chroma mutated the shared configuration; it must be given a fresh one per call"
    )
