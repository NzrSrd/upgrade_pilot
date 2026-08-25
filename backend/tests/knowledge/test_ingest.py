"""The ingestion entry point: corpus on disk to a served collection.

The interesting behaviour here is not the write, it is the *deletion*.
`upsert` alone leaves behind any chunk whose document has gone away or whose
chunking produced fewer pieces than last time — and a leftover chunk is still
retrieved, still ranked, and still cited, quoting text that is no longer in the
corpus at all. That is a citation to a source the reader cannot find, which is
the failure this whole product exists to avoid.
"""

from pathlib import Path

import pytest

from tests.knowledge.corpus_fixtures import FRONTMATTER, document
from tests.knowledge.fake_embedding import fake_embedding_function
from upgradepilot.services.knowledge.corpus import CORPUS_ROOT, CorpusDocumentError
from upgradepilot.services.knowledge.ingest import ingest_corpus
from upgradepilot.services.knowledge.store import KnowledgeStore


def a_corpus(tmp_path: Path, **files: str) -> Path:
    root = tmp_path / "corpus"
    root.mkdir(exist_ok=True)
    for name, text in files.items():
        (root / f"{name}.md").write_text(text, encoding="utf-8")
    return root


def opened(tmp_path: Path) -> KnowledgeStore:
    return KnowledgeStore.open(tmp_path / "chroma", embedding_function=fake_embedding_function())


def test_ingesting_the_shipped_corpus_reports_what_it_wrote(tmp_path: Path) -> None:
    report = ingest_corpus(
        corpus_root=CORPUS_ROOT,
        chroma_dir=tmp_path / "chroma",
        embedding_function=fake_embedding_function(),
    )

    assert report.documents == 19
    assert report.chunks >= report.documents
    assert opened(tmp_path).count() == report.chunks


def test_a_deleted_document_leaves_no_chunk_behind(tmp_path: Path) -> None:
    """A document removed from the corpus must stop being retrievable.

    `upsert` writes by id and never deletes, so without a rebuild the old
    chunks stay in the collection forever. They keep their metadata, keep
    ranking, and keep being cited — a `SourceRef` pointing at a file that no
    longer exists.
    """
    root = a_corpus(
        tmp_path,
        keep=FRONTMATTER,
        remove=document(source_id="pydantic-v2#doomed", title='"A document about to go"'),
    )
    chroma = tmp_path / "chroma"
    ingest_corpus(corpus_root=root, chroma_dir=chroma, embedding_function=fake_embedding_function())
    assert "pydantic-v2#doomed" in {
        r.source_id for r in opened(tmp_path).search("a document", dependency="pydantic", limit=20)
    }

    (root / "remove.md").unlink()
    ingest_corpus(corpus_root=root, chroma_dir=chroma, embedding_function=fake_embedding_function())

    remaining = opened(tmp_path).search("a document", dependency="pydantic", limit=20)
    assert "pydantic-v2#doomed" not in {r.source_id for r in remaining}


def test_a_document_that_shrank_leaves_no_orphan_chunk(tmp_path: Path) -> None:
    """The subtler half, and the one `upsert` cannot reach at all.

    `chunk_id` is positional — `{source_id}#chunk-{n}` — so a document edited
    from three chunks down to one re-writes `#chunk-0` and abandons `#chunk-1`
    and `#chunk-2`. Those orphans carry the *old* text under a live
    `source_id`, so a citation to them resolves to a real document and quotes
    a passage that was deleted from it. Nothing about that looks wrong in the
    output.
    """
    frontmatter, _, _ = document().partition("\n---\n\n")
    long_body = "\n\n".join(f"Paragraph {i}. " + "word " * 60 for i in range(12))
    root = a_corpus(tmp_path, only=f"{frontmatter}\n---\n\n{long_body}\n")
    chroma = tmp_path / "chroma"

    ingest_corpus(corpus_root=root, chroma_dir=chroma, embedding_function=fake_embedding_function())
    assert opened(tmp_path).count() > 1, "the fixture must produce more than one chunk"

    (root / "only.md").write_text(f"{frontmatter}\n---\n\nOne short paragraph now.\n", "utf-8")
    report = ingest_corpus(
        corpus_root=root, chroma_dir=chroma, embedding_function=fake_embedding_function()
    )

    assert report.chunks == 1
    assert opened(tmp_path).count() == 1


def test_a_failed_re_ingest_leaves_the_working_collection_intact(tmp_path: Path) -> None:
    """The whole corpus is parsed *before* the rebuild drops anything.

    Ingestion being a rebuild is what keeps the store honest, and it is also
    what makes this ordering matter: drop first and a single typo in one
    corpus document takes down a knowledge base that was serving correctly a
    moment ago. Every subsequent run then reports no documented breaking
    changes — which is indistinguishable, in the output, from a clean upgrade.

    An earlier version of this test asserted only that a first-ever failed
    ingest left the collection empty. That is true whichever order the two
    steps run in, so it distinguished nothing; this starts from a populated
    collection, which is the only state where the ordering is observable.
    """
    root = a_corpus(tmp_path, good=FRONTMATTER)
    chroma = tmp_path / "chroma"
    before = ingest_corpus(
        corpus_root=root, chroma_dir=chroma, embedding_function=fake_embedding_function()
    )
    assert before.chunks > 0

    (root / "bad.md").write_text("no frontmatter here\n", encoding="utf-8")
    with pytest.raises(CorpusDocumentError):
        ingest_corpus(
            corpus_root=root, chroma_dir=chroma, embedding_function=fake_embedding_function()
        )

    assert opened(tmp_path).count() == before.chunks, (
        "a broken corpus document wiped a collection that was serving correctly"
    )
