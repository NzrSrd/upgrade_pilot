"""The golden retrieval evaluation, asserted in CI.

Spec §7.2 and PLANNING.md ask for RAG evaluation as an executable test rather
than a paragraph. `golden_set.py` holds the cases and the metrics; this file
runs them against the corpus that actually ships, through the store that
actually serves, and asserts floors.

**Read the floors correctly.** They are a regression barrier over the
retrieval *pipeline* -- filters, the symbol join, dedup, ordering, the
distance-to-relevance mapping -- scored under a deliberately crude offline
embedding. Every real pipeline break collapses them to near zero: an inverted
ranking, a filter that matches nothing, a dedup that drops results. What they
do **not** measure is semantic retrieval quality, because the embedding is
lexical. Nobody should quote 0.79 as this system's recall.
"""

from pathlib import Path

import pytest

from tests.knowledge.fake_embedding import fake_embedding_function
from tests.knowledge.golden_set import (
    GOLDEN_CASES,
    mean_reciprocal_rank,
    rank_of,
    recall_at_k,
)
from upgradepilot.services.knowledge.corpus import CORPUS_ROOT, load_corpus
from upgradepilot.services.knowledge.store import DEFAULT_LIMIT, KnowledgeStore

RECALL_AT_5_FLOOR = 0.70
MRR_FLOOR = 0.60
"""Measured 0.789 and 0.695 respectively, against the 19-document corpus on
2026-08-25. The floors sit roughly 0.09 below, which is enough headroom to add
a document or two without a red build and nowhere near enough to hide a
pipeline regression -- every one of those drives both numbers to approximately
zero rather than shaving a few points off.

The gap between 0.789 and 1.0 is the offline embedder, not the pipeline. All
four misses were diagnosed rather than accepted: the correct document ranks
6th, 6th, 16th and 13th out of 30 chunks, losing to documents that share
common vocabulary ("model", "a", "for") because a raw count vector has no
notion of a term being rare. That is the known cost of an embedding with no
corpus statistics.

Two alternative term weightings were measured against these same cases --
binary (recall@5 0.737, MRR 0.559) and sublinear tf (0.895, 0.686). Neither
dominates, and the embedder was **not** switched to the better-looking one:
choosing an embedder by its score on the evaluation set is fitting to the
evaluation set, and a floor derived that way measures how hard we tuned rather
than how well retrieval works. The variants are recorded here so the next
person does not have to rediscover them."""


@pytest.fixture(scope="module")
def store(tmp_path_factory: pytest.TempPathFactory) -> KnowledgeStore:
    """Module-scoped: ingesting 19 documents per test would dominate the
    suite's runtime, and nothing here mutates the store."""
    path: Path = tmp_path_factory.mktemp("golden") / "chroma"
    opened = KnowledgeStore.open(path, embedding_function=fake_embedding_function())
    opened.ingest(load_corpus(CORPUS_ROOT))
    return opened


def _ranks(store: KnowledgeStore) -> list[int | None]:
    return [
        rank_of(
            case.expected_source_id,
            [
                result.source_id
                for result in store.search(case.query, dependency="pydantic", limit=DEFAULT_LIMIT)
            ],
        )
        for case in GOLDEN_CASES
    ]


def test_recall_at_5_meets_its_floor(store: KnowledgeStore) -> None:
    recall = recall_at_k(_ranks(store), DEFAULT_LIMIT)
    assert recall >= RECALL_AT_5_FLOOR, f"recall@{DEFAULT_LIMIT} fell to {recall:.3f}"


def test_mean_reciprocal_rank_meets_its_floor(store: KnowledgeStore) -> None:
    """Asserted alongside recall because the two fail differently: a change
    that pushes every correct answer from rank 1 to rank 5 leaves recall@5
    untouched and halves MRR."""
    mrr = mean_reciprocal_rank(_ranks(store))
    assert mrr >= MRR_FLOOR, f"MRR fell to {mrr:.3f}"


def test_every_corpus_document_has_a_golden_case() -> None:
    """CLAUDE.md rule 25 -- a new corpus document requires a golden-set case
    in the same change -- enforced rather than remembered.

    Without this, the floors quietly become easier to meet as the corpus
    grows: new documents add distractors to every existing query while
    contributing no case of their own, so the metric drifts down for a reason
    that has nothing to do with retrieval getting worse.
    """
    documented = {case.expected_source_id for case in GOLDEN_CASES}
    shipped = {doc.source_id for doc in load_corpus(CORPUS_ROOT)}

    assert shipped - documented == set(), f"no golden case for: {sorted(shipped - documented)}"


def test_every_golden_case_names_a_document_that_exists() -> None:
    """The other direction. A case naming a deleted or renamed `source_id`
    can never be satisfied, so it drags both metrics down permanently and
    reads as a retrieval failure rather than as a stale test."""
    shipped = {doc.source_id for doc in load_corpus(CORPUS_ROOT)}

    for case in GOLDEN_CASES:
        assert case.expected_source_id in shipped, case.expected_source_id


def test_the_metrics_can_actually_fail() -> None:
    """A guard on the guards.

    `recall_at_k` and `mean_reciprocal_rank` over an all-miss list must be
    zero. If either returned a default of 1.0 on unexpected input -- an empty
    list, all `None` -- the two floor tests above would pass no matter what
    retrieval did, and nothing else in the suite would notice.
    """
    all_missed: list[int | None] = [None] * len(GOLDEN_CASES)

    assert recall_at_k(all_missed, 5) == 0.0
    assert mean_reciprocal_rank(all_missed) == 0.0
    assert recall_at_k([], 5) == 0.0
    assert mean_reciprocal_rank([]) == 0.0
    assert recall_at_k([1, None], 5) == 0.5
    assert mean_reciprocal_rank([1, 2]) == 0.75
