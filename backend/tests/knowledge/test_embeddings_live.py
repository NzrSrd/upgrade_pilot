"""One real embedding call, closing ADR-001's open Phase 3 question.

ADR-001's Phase 0 embedding row verified reachability with `curl` and left two
things explicitly unverified: whether the *client library path* works through a
configured `llm_base_url`, and how embedding tokens should be accounted. This
file answers the first by exercising the code that actually ships, and pins the
second by asserting the provider reports usage the recorder can capture.

The gap this closes is the same shape as the chat-model one in
`tests/llm/test_usage_metadata_live.py`: every offline knowledge-base test
uses a fake embedder, so all of them pass while the real embedding path is
broken, returns the wrong number of dimensions, or reports no usage at all and
the cost table silently reads zero.

Same scope discipline as the chat live test — this asserts what the *configured*
endpoint does. A pass against OpenRouter is not a claim about OpenAI direct.
"""

from pathlib import Path
from typing import cast

import pytest
from chromadb.api.types import Embeddable, EmbeddingFunction

from upgradepilot.config import get_settings
from upgradepilot.services.knowledge.corpus import CORPUS_ROOT, load_corpus
from upgradepilot.services.knowledge.embeddings import (
    OpenAIEmbedding,
    openai_embedding_function,
)
from upgradepilot.services.knowledge.store import KnowledgeStore

pytestmark = pytest.mark.live


@pytest.fixture
def embedder() -> OpenAIEmbedding:
    settings = get_settings()
    if not settings.llm_configured:
        pytest.skip("no LLM API key configured")
    function = openai_embedding_function(settings)
    assert isinstance(function, OpenAIEmbedding)
    return function


def test_a_real_embedding_call_returns_vectors_and_reports_its_tokens(
    embedder: OpenAIEmbedding,
) -> None:
    """The whole of what Phase 3 needs from the provider, in one call."""
    vectors = embedder(["@validator was replaced by @field_validator in Pydantic v2."])

    assert len(vectors) == 1
    assert len(vectors[0]) > 0, "the provider returned an empty vector"
    assert embedder.calls, "the call was not recorded, so its tokens are unrecoverable"
    assert embedder.calls[0].texts == 1
    assert embedder.total_tokens > 0, (
        "usage was not reported; embedding cost would silently read zero"
    )


def test_every_text_in_a_batch_gets_its_own_vector(embedder: OpenAIEmbedding) -> None:
    """Chroma passes a list and matches vectors to documents positionally, so
    a batching bug that returned the wrong count or the wrong order would
    attach each chunk's metadata to another chunk's embedding — every citation
    resolvable, every one attached to the wrong text."""
    texts = ["the nested class Config", "the @validator decorator", "BaseSettings moved"]

    vectors = embedder(texts)

    assert len(vectors) == len(texts)
    assert len({len(v) for v in vectors}) == 1, "vectors differ in dimension"


def test_the_shipped_corpus_ingests_and_retrieves_with_real_embeddings(
    embedder: OpenAIEmbedding, tmp_path: Path
) -> None:
    """End to end on the real path: author, ingest, retrieve, cite.

    Deliberately asserts on the *citation* rather than on which document ranks
    first. Ranking under a real embedding is a quality property that belongs
    to the golden set; what has to hold here is that the real path produces
    results that can name their own source, because that is the property no
    offline test can confirm.
    """
    documents = load_corpus(CORPUS_ROOT)
    # The same protocol-variance cast `openai_embedding_function` makes, and
    # for the same reason (see `fake_embedding.fake_embedding_function`).
    # The fixture hands back the concrete class so `.calls` and
    # `.total_tokens` are reachable, which the erased type hides.
    store = KnowledgeStore.open(
        tmp_path / "chroma",
        embedding_function=cast(EmbeddingFunction[Embeddable], embedder),
    )
    report = store.ingest(documents)

    assert report.documents == len(documents)
    assert embedder.total_tokens > 0

    results = store.search(
        "the @validator decorator is deprecated, what replaces it",
        dependency="pydantic",
    )

    assert results
    for result in results:
        ref = result.to_source_ref()
        assert ref.source_id in {doc.source_id for doc in documents}
        assert 0.0 <= ref.relevance <= 1.0
