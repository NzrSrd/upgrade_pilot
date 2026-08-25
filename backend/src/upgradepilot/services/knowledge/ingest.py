"""Ingestion: the authored corpus on disk becomes a served collection.

Run as a module against the shipped corpus and the configured store:

    python -m upgradepilot.services.knowledge.ingest

**Ingestion is a rebuild, not an update.** That is the one design decision in
this file and it exists for a correctness reason rather than a simplicity one.
`upsert` writes by id and never deletes, so two things survive it:

- A document removed from the corpus keeps every chunk it ever wrote. Those
  chunks keep their metadata, keep ranking, and keep being cited -- a
  `SourceRef` pointing at a file that no longer exists.
- `chunk_id` is positional (`{source_id}#chunk-{n}`), so a document edited
  from three chunks down to one rewrites `#chunk-0` and abandons `#chunk-1`
  and `#chunk-2`. Those orphans carry the *old* text under a live `source_id`,
  so a citation to them resolves to a real document and quotes a passage that
  was deleted from it. Nothing about that looks wrong in the output.

The corpus is small enough that rebuilding costs a second, and the alternative
is a store that drifts away from the corpus in a direction no test can see.
"""

import sys
from pathlib import Path

from chromadb.api.types import Embeddable, EmbeddingFunction

from upgradepilot.config import get_settings
from upgradepilot.models.knowledge import IngestReport
from upgradepilot.services.knowledge.corpus import CORPUS_ROOT, load_corpus
from upgradepilot.services.knowledge.store import KnowledgeStore


def ingest_corpus(
    *,
    corpus_root: Path,
    chroma_dir: Path,
    embedding_function: EmbeddingFunction[Embeddable],
) -> IngestReport:
    """Replace the collection's contents with the corpus at `corpus_root`.

    The whole corpus is parsed *before* anything is written. Parsing lazily
    and writing as it goes would leave the collection holding the documents
    before a broken one and none of those after it -- a corpus that is
    silently a prefix of itself, with nothing visible at query time to say so.
    """
    documents = load_corpus(corpus_root)

    store = KnowledgeStore.open(chroma_dir, embedding_function=embedding_function)
    store.drop()
    # `open` created the collection; `drop` removed it. Re-opening is what
    # recreates it with the right configuration, rather than letting the
    # first `upsert` create one with chroma's defaults and the wrong
    # distance space.
    store = KnowledgeStore.open(chroma_dir, embedding_function=embedding_function)
    return store.ingest(documents)


def main() -> int:
    """Ingest the shipped corpus into the configured store."""
    settings = get_settings()
    corpus_root = settings.corpus_dir or CORPUS_ROOT

    # Imported here rather than at module scope: the real embedding function
    # constructs a provider client and needs a key, and `ingest_corpus` is
    # called from tests with a fake one. Only this entry point requires the
    # provider to be configured at all.
    from upgradepilot.services.knowledge.embeddings import openai_embedding_function

    report = ingest_corpus(
        corpus_root=corpus_root,
        chroma_dir=settings.chroma_dir,
        embedding_function=openai_embedding_function(settings),
    )
    print(
        f"ingested {report.documents} documents as {report.chunks} chunks "
        f"from {corpus_root} into {settings.chroma_dir}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by running the module
    sys.exit(main())
