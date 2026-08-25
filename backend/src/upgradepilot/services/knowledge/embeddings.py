"""The production embedding function, and where its tokens are recorded.

Spec §7.2 fixes the model as `text-embedding-3-small`. ADR-001's Phase 0
embedding row left two things open for Phase 3, and this module closes the
first and prepares the second:

1. **Does this work through a configured `llm_base_url`?** Phase 0 verified
   the endpoint with `curl` only. `tests/knowledge/test_embeddings_live.py`
   answers it through the client this code actually uses.
2. **How are embedding tokens accounted?** Embedding calls are cheap but not
   free, and spec §9.4 is explicit that they enter the cost table *separately*
   from chat calls -- otherwise "estimated cost" is simply wrong.

On (2), what belongs to Phase 3 is *capturing* the number, and that is all
this module does: every call appends an `EmbeddingCall` to `.calls`.
Aggregating those into a `UsageSummary` is Phase 4's job, alongside the same
work for chat calls, and doing it here would mean building half of Phase 4's
cost model against one caller. The recording seam is the part that cannot be
retrofitted -- a token count not captured at the call is gone -- so it is the
part that lands now.

The provider is reached through the `openai` client directly rather than
through `langchain-openai`. Spec rule 18 routes *chat* models through
`TrackedLLM` because that is the only place chat usage can be missed; an
embedding call has no such indirection to hide in, and the raw client returns
`response.usage` without the structured-output complications §9.4 documents.
"""

from typing import cast

from chromadb.api.types import Documents, Embeddable, EmbeddingFunction, Embeddings
from openai import OpenAI

from upgradepilot.config import Settings
from upgradepilot.models.base import HonestModel
from upgradepilot.models.errors import KnowledgeBaseUnavailableError
from upgradepilot.models.evidence import NonBlankStr

EMBEDDING_BATCH_SIZE = 100
"""Texts per request. The corpus is tens of chunks, so this exists to keep a
single ingest from becoming one enormous request rather than to manage
throughput."""


class EmbeddingCall(HonestModel):
    """One embedding request and the tokens the provider charged for it.

    `tokens` is what the provider reported, never an estimate: an estimated
    figure recorded in the same field as a measured one is a number nobody
    downstream can qualify. If a provider ever omits `usage`, that must
    surface as its own state rather than as a plausible guess.
    """

    model: NonBlankStr
    texts: int
    tokens: int


class OpenAIEmbedding(EmbeddingFunction[Documents]):
    """`text-embedding-3-small` (or whatever `Settings.embedding_model` names).

    Holds its own list of `EmbeddingCall`s. Chroma constructs and calls the
    embedding function itself, so this object is the only place the token
    counts pass through -- there is no outer wrapper that could record them.
    """

    def __init__(self, client: OpenAI, *, model: str) -> None:
        self._client = client
        self._model = model
        self.calls: list[EmbeddingCall] = []

    def get_config(self) -> dict[str, object]:
        return {"model": self._model}

    @staticmethod
    def build_from_config(config: dict[str, object]) -> "OpenAIEmbedding":
        """Refused: rebuilding needs a credential, and a credential must not
        be persisted into a collection so that it can be.

        Chroma calls this when reconstructing an embedding function from a
        collection's stored configuration -- which happens only on a
        `get_collection` that passes no `embedding_function`. `KnowledgeStore`
        always passes one, so this path is never taken in this system.

        The refusal makes chroma log one `DeprecationWarning` at collection
        creation ("legacy embedding function config") and store the config in
        its legacy form. That warning is expected and is visible in the live
        test's output; it is not a defect, and it is left unsuppressed so that
        a future, real chroma deprecation is not hidden along with it.
        """
        raise NotImplementedError(
            "OpenAIEmbedding cannot be rebuilt from stored configuration: it needs a "
            "configured client, which carries a credential that is deliberately not "
            "persisted in the collection. Construct it via openai_embedding_function()."
        )

    @property
    def total_tokens(self) -> int:
        """Tokens across every call made by this instance."""
        return sum(call.tokens for call in self.calls)

    def __call__(self, input: Documents) -> Embeddings:
        vectors: list[list[float]] = []
        for start in range(0, len(input), EMBEDDING_BATCH_SIZE):
            batch = list(input[start : start + EMBEDDING_BATCH_SIZE])
            if not batch:
                continue
            try:
                response = self._client.embeddings.create(model=self._model, input=batch)
            except Exception as exc:
                # Rule 20: nothing is swallowed. A failed embedding call means
                # the knowledge base cannot answer, which is exactly
                # KB_UNAVAILABLE -- and typing it here is what lets §8.1's
                # confidence ceiling engage rather than the run failing as an
                # unclassified internal error.
                raise KnowledgeBaseUnavailableError(
                    "The embedding provider could not be reached, so the knowledge "
                    "base could not be searched.",
                    detail=f"embeddings.create(model={self._model!r}, {len(batch)} texts): {exc}",
                ) from exc

            usage = getattr(response, "usage", None)
            self.calls.append(
                EmbeddingCall(
                    model=self._model,
                    texts=len(batch),
                    # `or 0` is a real case, not defensive padding: a
                    # gateway that omits `usage` must contribute zero rather
                    # than a guess. Phase 4 distinguishes "measured zero"
                    # from "never reported" when it builds the cost table;
                    # inventing a number here would remove its ability to.
                    tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
                )
            )
            vectors.extend(item.embedding for item in response.data)

        return cast(Embeddings, vectors)

    @staticmethod
    def name() -> str:
        return "upgradepilot-openai-embedding"


def openai_embedding_function(settings: Settings) -> EmbeddingFunction[Embeddable]:
    """Build the production embedding function from configuration.

    Raises rather than falling back to an offline embedder when no key is
    configured. A silent fallback would produce a collection whose vectors are
    meaningless against a real query and identical in every other respect --
    retrieval would return confident, wrong evidence with no signal anywhere
    that the wrong embedder ran.
    """
    if not settings.llm_configured:
        raise KnowledgeBaseUnavailableError(
            "No embedding provider is configured, so the knowledge base cannot be built.",
            detail=(
                "Settings.llm_api_key is unset or blank; set OPENROUTER_API_KEY, "
                "OPENAI_API_KEY or UP_LLM_API_KEY"
            ),
        )
    assert settings.llm_api_key is not None  # narrowed by llm_configured

    client = OpenAI(
        api_key=settings.llm_api_key.get_secret_value(),
        base_url=settings.llm_base_url,
    )
    # See `fake_embedding.fake_embedding_function` for why this is a cast and
    # not a `type: ignore`: `EmbeddingFunction` is contravariant in its
    # parameter, so a `Documents`-only embedder cannot structurally satisfy
    # the `EmbeddingFunction[Embeddable]` chroma's signatures declare, even
    # though documents are exactly what it passes.
    return cast(
        EmbeddingFunction[Embeddable],
        OpenAIEmbedding(client, model=settings.embedding_model),
    )
