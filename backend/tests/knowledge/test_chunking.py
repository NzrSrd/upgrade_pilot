"""Splitting a corpus document into the units that get embedded and cited.

A chunk is what a `SourceRef` points at, so the chunker has two obligations
that outrank retrieval quality: it must lose nothing, and it must produce the
same `chunk_id` for the same content every time. A dropped paragraph makes
the breaking change it documents permanently unretrievable while the corpus
still reports the document as ingested; an unstable id makes yesterday's
citation resolve to today's different text.
"""

from tests.knowledge.corpus_fixtures import document
from upgradepilot.models.knowledge import CorpusDocument
from upgradepilot.services.knowledge.chunking import MAX_CHUNK_CHARS, chunk_document
from upgradepilot.services.knowledge.corpus import parse_document


def a_document(body: str) -> CorpusDocument:
    """The shared sample's frontmatter over a body this file chooses.

    Replaces the sample body rather than appending to it -- appending leaves
    the sample's own two paragraphs in front of the fixture, which silently
    changes every chunk boundary being asserted.
    """
    frontmatter, _, _ = document().partition("\n---\n\n")
    return parse_document(f"{frontmatter}\n---\n\n{body}\n", path="p.md")


def test_a_short_document_is_one_chunk() -> None:
    """One breaking change per document is the authoring rule, so the common
    case must not be fragmented for no reason -- a change split across two
    chunks can have its 'old form' retrieved without its 'new form'."""
    doc = a_document("The v1 form is `@validator`; the v2 form is `@field_validator`.\n")

    chunks = chunk_document(doc)

    assert len(chunks) == 1
    assert chunks[0].ordinal == 0
    assert chunks[0].source_id == doc.source_id
    assert chunks[0].chunk_id == f"{doc.source_id}#chunk-0"
    assert chunks[0].text == doc.body


def test_chunk_ids_are_ordered_unique_and_derived_from_the_source_id() -> None:
    doc = a_document("\n\n".join(f"Paragraph {i}. " + "word " * 60 for i in range(12)))

    chunks = chunk_document(doc)

    assert len(chunks) > 1, "the fixture must actually exceed one chunk"
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))
    assert [c.chunk_id for c in chunks] == [
        f"{doc.source_id}#chunk-{i}" for i in range(len(chunks))
    ]
    assert len(set(c.chunk_id for c in chunks)) == len(chunks)


def test_chunking_is_deterministic() -> None:
    """`chunk_id` is a citation key. If two ingests of identical content
    produced different ids, a report written yesterday would cite a chunk
    that no longer exists, or -- worse -- one that now holds other text."""
    doc = a_document("\n\n".join(f"Paragraph {i}. " + "word " * 60 for i in range(12)))

    assert [c.model_dump() for c in chunk_document(doc)] == [
        c.model_dump() for c in chunk_document(doc)
    ]


def test_no_authored_text_is_lost() -> None:
    """The invariant that matters most. A chunker that drops a paragraph
    leaves the corpus reporting the document as ingested while the change
    described in that paragraph is unretrievable -- a silent under-report
    with no signal anywhere that it happened.

    Compared with whitespace collapsed, because chunk boundaries are allowed
    to consume the blank lines between paragraphs and nothing else.
    """
    body = "\n\n".join(f"Paragraph {i}. " + "word " * 60 for i in range(12))
    doc = a_document(body)

    rejoined = " ".join(c.text for c in chunk_document(doc))

    assert rejoined.split() == doc.body.split()


def test_a_paragraph_is_not_split_when_it_fits() -> None:
    """Boundaries fall between paragraphs, not inside them. Half a sentence
    is not evidence, and a `SourceRef` pointing at it reads as a quotation
    the author never wrote."""
    first = "First paragraph. " + "alpha " * 40
    second = "Second paragraph. " + "beta " * 40
    doc = a_document(f"{first.strip()}\n\n{second.strip()}")

    chunks = chunk_document(doc, max_chars=len(first) + 20)

    assert len(chunks) == 2
    assert chunks[0].text == first.strip()
    assert chunks[1].text == second.strip()


def test_a_fenced_code_block_is_never_split() -> None:
    """Code is the highest-value content in this corpus -- the old form and
    the new form of a changed API. A boundary inside a fence yields a chunk
    holding an unclosed fence and a snippet that will not run, cited as if
    it were the migration.

    The code block below contains blank lines, which is the only thing that
    makes this test bite: paragraph splitting is on blank lines, so a fence
    with none inside it is indivisible whatever the chunker does. An earlier
    version of this fixture had no blank lines and passed with the fence
    tracking deleted -- it was asserting nothing.
    """
    body = "\n\n".join(f"field_{i}: int = {i}" for i in range(40))
    fence = f"```python\nclass Model(BaseModel):\n\n{body}\n```"
    doc = a_document(f"Before the block.\n\n{fence}\n\nAfter the block.")

    chunks = chunk_document(doc, max_chars=200)

    holding = [c for c in chunks if "```python" in c.text]
    assert len(holding) == 1, "the fence opened in more than one chunk"
    assert holding[0].text.count("```") == 2, holding[0].text
    assert "field_39: int = 39" in holding[0].text


def test_a_single_paragraph_larger_than_the_budget_is_emitted_whole() -> None:
    """`max_chars` is a target, not a guarantee, and this is the deliberate
    overshoot. Cutting mid-paragraph to honour the budget would trade a
    chunk that is too big -- a retrieval-quality problem -- for a citation
    that quotes a fragment, which is a correctness problem.
    """
    huge = "One very long paragraph. " + "word " * 400
    doc = a_document(huge.strip())

    chunks = chunk_document(doc, max_chars=100)

    assert len(chunks) == 1
    assert chunks[0].text == huge.strip()
    assert len(chunks[0].text) > 100


def test_the_default_budget_is_the_one_the_module_publishes() -> None:
    """Ingestion and any future re-chunking must agree on the default, or
    the ids drift between them."""
    body = "\n\n".join(f"Paragraph {i}. " + "word " * 60 for i in range(12))
    doc = a_document(body)

    assert [c.model_dump() for c in chunk_document(doc)] == [
        c.model_dump() for c in chunk_document(doc, max_chars=MAX_CHUNK_CHARS)
    ]
