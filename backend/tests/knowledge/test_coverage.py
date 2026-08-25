"""Symbol coverage over retrieved candidates, and the deterministic gate.

Spec §7.3: the LLM grades coverage, and then **a mechanical gate overrides
it** -- if any high-confidence symbol has no candidate chunk documenting it,
retrieval is insufficient regardless of what the model said. This module is
that gate. The LLM half arrives in Phase 5; nothing here consults a model,
which is the point: a gate the model can talk its way past is not a gate.

The failure it exists to prevent is specific. The analyzer found `validator`
used at three real lines; the corpus documents nothing about it; the model,
seeing four confident-looking chunks about `Config`, declares coverage
sufficient. The report then reads as complete while the symbol with the most
usage is the one nothing was found for.
"""

from upgradepilot.models.enums import Confidence, Severity, SourceType
from upgradepilot.models.knowledge import RetrievedChunk
from upgradepilot.models.repo import SymbolInventory, SymbolStat
from upgradepilot.services.knowledge.coverage import annotate_coverage


def inventory(*symbols: tuple[str, Confidence]) -> SymbolInventory:
    return SymbolInventory(
        entries=tuple(
            SymbolStat(symbol=symbol, count=1, files=("src/app/models.py",), confidence=confidence)
            for symbol, confidence in symbols
        )
    )


def chunk(
    chunk_id: str,
    *,
    documents: tuple[str, ...],
    matched: tuple[str, ...] = (),
) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        source_id=chunk_id.split("#chunk-")[0],
        title="A documented change",
        source_type=SourceType.MIGRATION_GUIDE,
        url_or_reference="https://example.invalid/doc",
        text="Some prose.",
        dependency="pydantic",
        to_version_major=2,
        severity=Severity.HIGH,
        affected_symbols=documents,
        distance=0.2,
        relevance=0.8,
        matched_symbols=matched,
    )


# -- annotation ------------------------------------------------------------


def test_a_symbol_is_covered_by_a_chunk_whose_document_names_it() -> None:
    report = annotate_coverage(
        inventory(("validator", Confidence.HIGH)),
        (chunk("s#chunk-0", documents=("validator", "root_validator")),),
    )

    assert [(e.symbol, e.covered) for e in report.entries] == [("validator", True)]
    assert report.entries[0].covering_chunk_ids == ("s#chunk-0",)


def test_every_inventory_symbol_appears_in_the_report() -> None:
    """Including the uncovered ones. A report that lists only what was found
    cannot be read as a coverage report at all -- the gap is the finding."""
    report = annotate_coverage(
        inventory(("validator", Confidence.HIGH), ("Config", Confidence.MEDIUM)),
        (chunk("s#chunk-0", documents=("validator",)),),
    )

    assert [e.symbol for e in report.entries] == ["Config", "validator"]
    assert report.uncovered == ("Config",)


def test_covering_chunk_ids_are_deduplicated_and_ordered() -> None:
    """The ids are printed in the trace, so an unstable order makes two runs
    over identical inputs look like different runs."""
    report = annotate_coverage(
        inventory(("validator", Confidence.HIGH)),
        (
            chunk("z#chunk-0", documents=("validator",)),
            chunk("a#chunk-1", documents=("validator",)),
            chunk("a#chunk-1", documents=("validator",)),
        ),
    )

    assert report.entries[0].covering_chunk_ids == ("a#chunk-1", "z#chunk-0")


def test_coverage_is_read_from_what_the_document_covers_not_from_the_query() -> None:
    """`matched_symbols` is annotated against whatever the *caller* asked
    about, so a caller that forgot to pass its symbols would leave it empty
    on every chunk. If the gate read that field, forgetting an argument would
    silently make retrieval look insufficient -- or, with the opposite
    plumbing bug, sufficient. The gate reads `affected_symbols`, which is the
    document's own claim about itself.
    """
    report = annotate_coverage(
        inventory(("validator", Confidence.HIGH)),
        (chunk("s#chunk-0", documents=("validator",), matched=()),),
    )

    assert report.entries[0].covered is True


def test_a_prefix_colliding_symbol_does_not_count_as_coverage() -> None:
    """The same exactness Chroma's `$contains` gives the query, applied again
    here in Python. A document about `ConfigDict` does not document `Config`,
    and treating it as coverage would let the gate pass on a symbol nothing
    explains."""
    report = annotate_coverage(
        inventory(("Config", Confidence.HIGH)),
        (chunk("s#chunk-0", documents=("ConfigDict",)),),
    )

    assert report.entries[0].covered is False
    assert report.sufficient is False


# -- the gate --------------------------------------------------------------


def test_an_uncovered_high_confidence_symbol_makes_retrieval_insufficient() -> None:
    report = annotate_coverage(
        inventory(("validator", Confidence.HIGH), ("Config", Confidence.HIGH)),
        (chunk("s#chunk-0", documents=("Config",)),),
    )

    assert report.uncovered_high_confidence == ("validator",)
    assert report.sufficient is False


def test_every_high_confidence_symbol_covered_is_sufficient() -> None:
    report = annotate_coverage(
        inventory(("validator", Confidence.HIGH), ("Config", Confidence.HIGH)),
        (
            chunk("s#chunk-0", documents=("Config",)),
            chunk("s#chunk-1", documents=("validator",)),
        ),
    )

    assert report.uncovered_high_confidence == ()
    assert report.sufficient is True


def test_an_uncovered_medium_symbol_does_not_block_sufficiency() -> None:
    """Deliberate, and the asymmetry is spec §7.3's. A medium- or
    low-confidence symbol is one the analyzer itself is unsure the repository
    really uses, so demanding corpus evidence for it would make the loop
    iterate against its own uncertainty rather than against a real gap. It is
    still reported as uncovered -- Phase 5 routes it to `unknowns` -- but it
    does not force another retrieval round.
    """
    report = annotate_coverage(
        inventory(("validator", Confidence.HIGH), ("Config", Confidence.MEDIUM)),
        (chunk("s#chunk-0", documents=("validator",)),),
    )

    assert report.uncovered == ("Config",)
    assert report.uncovered_high_confidence == ()
    assert report.sufficient is True


def test_no_chunks_at_all_is_insufficient_when_anything_is_high_confidence() -> None:
    """The `KB_UNAVAILABLE` and empty-corpus shapes both arrive here as an
    empty candidate set. Neither may read as sufficient."""
    report = annotate_coverage(inventory(("validator", Confidence.HIGH)), ())

    assert report.sufficient is False
    assert report.uncovered_high_confidence == ("validator",)


def test_an_empty_inventory_is_sufficient_because_there_is_nothing_to_cover() -> None:
    """Spec §7.3: zero usage sites means retrieval is not warranted at all.

    Sufficiency here says only "no symbol is missing evidence", which is
    trivially true of no symbols. It is emphatically **not** a claim that the
    upgrade is safe -- that is `evidence_available`'s job and §8.1's
    confidence ceiling, which are separate for exactly this reason.
    """
    report = annotate_coverage(inventory(), ())

    assert report.entries == ()
    assert report.sufficient is True
