"""The deterministic embedding function every offline knowledge-base test uses.

**Lexical, not semantic, and the distinction is load-bearing.** An earlier
version of this hashed the whole document with SHA-256, which is stable and
offline but assigns unrelated vectors to related texts: the only ranking it
can produce is "an exact repeat comes first". That is enough to prove Chroma
stores and filters what it was given, and *not* enough to prove a retrieval
pipeline ranks anything — a golden set scored against it would be measuring
noise and reporting a floor.

This is a hashing vectorizer instead: tokens are hashed into a fixed number
of dimensions, counted, and the vector is L2-normalised, so cosine distance
falls as word overlap rises. Crude, but real — which makes the golden set's
recall@5 and MRR floors mean something.

What those floors then measure must be stated honestly, because it is easy to
over-read: they exercise the *pipeline* — filters, the symbol join, dedup,
ordering, the distance-to-relevance mapping — under a lexical embedding. They
are not a measurement of `text-embedding-3-small`'s semantic quality, and a
change of embedding model would need its own evidence.
"""

import hashlib
import re
from typing import Any, cast

import numpy as np
import numpy.typing as npt
from chromadb.api.types import Documents, Embeddable, EmbeddingFunction, Embeddings

DIMENSIONS = 256
"""Wide enough that ordinary corpus vocabulary rarely collides, small enough
to keep the offline suite fast. Collisions are harmless in kind -- they add a
little noise to similarity -- but a 16-dimension space collides so often that
ranking becomes arbitrary again."""

_TOKEN = re.compile(r"[a-z0-9_]+")


def _tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


class LexicalEmbedding(EmbeddingFunction[Documents]):
    """A hashing vectorizer: deterministic, offline, and lexically meaningful."""

    def __init__(self) -> None:
        """Declared though it does nothing: chromadb 1.5.9 emits a
        `DeprecationWarning` for an embedding function without one, and this
        project's test output is meant to stay pristine."""

    def get_config(self) -> dict[str, Any]:
        """Configuration needed to rebuild this embedder: none.

        Same reason as `__init__` -- chromadb warns without it. Returning an
        empty mapping is truthful here: every parameter of this embedder is a
        module constant, so there is no per-instance state to persist.
        """
        return {}

    @staticmethod
    def build_from_config(config: dict[str, Any]) -> "LexicalEmbedding":
        return LexicalEmbedding()

    def __call__(self, input: Documents) -> Embeddings:
        vectors: list[npt.NDArray[np.int32 | np.float32]] = []
        for text in input:
            vector = np.zeros(DIMENSIONS, dtype=np.float32)
            for token in _tokenize(text):
                digest = hashlib.sha256(token.encode("utf-8")).digest()
                vector[int.from_bytes(digest[:4], "big") % DIMENSIONS] += 1.0
            norm = float(np.linalg.norm(vector))
            if norm > 0.0:
                vector /= norm
            else:
                # A chunk with no tokens at all cannot be embedded
                # meaningfully. `CorpusDocument.body` is non-blank so this
                # is unreachable through ingestion; a zero vector would make
                # cosine distance undefined, so pick one fixed direction
                # rather than let the store decide what to do with zeros.
                vector[0] = 1.0
            vectors.append(vector)
        return vectors

    @staticmethod
    def name() -> str:
        return "lexical-test-embedding"


def fake_embedding_function() -> EmbeddingFunction[Embeddable]:
    """Shared by every offline knowledge-base test.

    Returns `EmbeddingFunction[Embeddable]` rather than the concrete class so
    the one unavoidable suppression lives here instead of at every call site.
    `EmbeddingFunction.__call__` takes its type parameter as a *parameter*, so
    the protocol is contravariant in it: a `Documents`-only embedder cannot
    structurally satisfy the `EmbeddingFunction[Embeddable]` that
    `create_collection` and `get_collection` declare, even though that is
    exactly what chromadb calls it with. Chromadb hits this in its own code
    and silences it the same way, at the assignment of
    `DefaultEmbeddingFunction` in `chromadb/api/types.py`.

    A `cast`, not a `# type: ignore`: the mismatch is a variance fact about
    the protocol, not an error to suppress, and a cast keeps the return type
    honest for anything that later reads it.
    """
    return cast(EmbeddingFunction[Embeddable], LexicalEmbedding())
