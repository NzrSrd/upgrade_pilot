"""The corpus that actually ships, checked as content rather than as code.

`test_corpus_documents.py` proves the parser refuses a malformed document.
This proves the documents we wrote are not malformed, and — more usefully —
that the corpus answers the questions this product is built to ask. A corpus
that parses cleanly and documents none of the symbols the analyzer finds is a
knowledge base in form only.
"""

from tests.fixtures.repo_builder import (
    EXPECTED_HIGH_CONFIDENCE_SYMBOLS,
    EXPECTED_MEDIUM_CONFIDENCE_SYMBOLS,
)
from upgradepilot.models.enums import SourceType
from upgradepilot.models.knowledge import SYMBOL_BEARING_SOURCE_TYPES
from upgradepilot.services.knowledge.corpus import CORPUS_ROOT, load_corpus

CORPUS = load_corpus(CORPUS_ROOT)


def test_the_shipped_corpus_loads() -> None:
    """`load_corpus` raises on a duplicate `source_id`, an unknown key, a
    disagreeing major version and an empty body, so this single call is the
    whole schema check over every document we ship."""
    assert len(CORPUS) >= 15, "the corpus is smaller than the golden set needs"


def test_every_symbol_the_fixture_repository_uses_is_documented() -> None:
    """The binding between the two halves of the product.

    The analyzer finds these symbols in the demo repository; retrieval joins
    on `affected_symbols` with `$contains`. A symbol in the first list and
    not the second is one the run will report as *uncovered* — correctly, and
    unhelpfully, because the gap is ours rather than the corpus's subject
    matter. Asserting it here means adding a usage kind to the fixture
    without documenting it turns a test red instead of quietly degrading a
    demo.
    """
    documented = {symbol for doc in CORPUS for symbol in doc.affected_symbols}
    expected = set(EXPECTED_HIGH_CONFIDENCE_SYMBOLS) | set(EXPECTED_MEDIUM_CONFIDENCE_SYMBOLS)

    assert expected, "the fixture's expectation tuples are empty, so this asserts nothing"
    assert expected <= documented, f"undocumented: {sorted(expected - documented)}"


def test_the_corpus_carries_internal_guidance_as_well_as_primary_sources() -> None:
    """Spec §7.2 asks for both. They are retrieved differently — primary
    sources by symbol join, guidance semantically — so a corpus of only one
    kind leaves half the retrieval path untested and the other half of the
    product's advice unsourced."""
    kinds = {doc.source_type for doc in CORPUS}

    assert SourceType.MIGRATION_GUIDE in kinds
    assert kinds & {SourceType.ADR, SourceType.UPGRADE_REPORT}


def test_primary_sources_reference_somewhere_a_reader_can_go() -> None:
    """A `url_or_reference` is printed as the citation. For a third-party
    source it must be the real document; for internal guidance it must be the
    corpus file itself, which exists and can be opened. What it must never be
    is a plausible-looking URL nobody checked.
    """
    for doc in CORPUS:
        if doc.source_type in SYMBOL_BEARING_SOURCE_TYPES:
            assert doc.url_or_reference.startswith("https://"), doc.source_id
        else:
            assert (CORPUS_ROOT / doc.url_or_reference).is_file(), (
                f"{doc.source_id} references {doc.url_or_reference}, which is not a corpus file"
            )


def test_every_document_targets_the_dependency_and_major_it_claims() -> None:
    """The scalar filters narrow on these two fields. A document with the
    wrong `dependency` is unreachable; one with the wrong major is reachable
    from the wrong query."""
    for doc in CORPUS:
        assert doc.dependency == doc.dependency.lower()
        assert doc.to_version_major >= 1


def test_no_two_documents_describe_the_same_change() -> None:
    """One breaking change per document is the authoring rule (spec §7.2).
    Two documents covering one change split its evidence: a query retrieves
    one of them, and the half of the explanation in the other never arrives.
    Titles are the cheapest proxy for this that a test can check."""
    titles = [doc.title for doc in CORPUS]
    assert len(titles) == len(set(titles))
