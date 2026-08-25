"""`TrackedLLM`: the only place this system calls a chat model.

CLAUDE.md rule 18 and spec §9.4. Concentrating model access here is what makes
"every token is counted" checkable at all -- with calls scattered across nodes,
a node that forgot to record its usage would be invisible, and the reported
cost would be quietly low forever. The rule buys nothing unless this module is
exhaustively tested, which is why `tests/llm/test_tracked_llm.py` is larger
than the module.

Spec §9.4's three hazards, each handled here:

1. **Which usage surface is populated is version-dependent.** The extractor
   reads `AIMessage.usage_metadata` first, falls back to
   `response_metadata["token_usage"]`, and only then estimates with tiktoken --
   marking the result `tokens_estimated=True`. Phase 0 found *both* surfaces
   populated on the pinned stack (ADR-001), so the fallback is currently dead
   in production; it is kept and tested because the day it starts being taken
   is the day nothing else would notice.
2. **`with_structured_output()` can swallow the raw message**, and the usage
   with it. `include_raw=True` is therefore not optional here, and the fake
   model refuses to model the unsafe call so it cannot look available.
3. **Pricing is configuration**, and an unknown model yields `None`.

One thing this module deliberately does *not* do: retry. A retry that silently
succeeds on the second attempt hides a provider that is failing half the time,
and it also bills twice while recording once unless the retry is itself
recorded. Retry policy belongs to the caller that knows whether the work is
idempotent.
"""

import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, TypeVar

import openai
import tiktoken
from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel

from upgradepilot.config import ModelPrice
from upgradepilot.models.enums import LLMCallKind
from upgradepilot.models.errors import LLMRateLimitedError, LLMUnavailableError
from upgradepilot.models.usage import LLMCall
from upgradepilot.services.llm.pricing import price_call

SchemaT = TypeVar("SchemaT", bound=BaseModel)

FALLBACK_ENCODING = "cl100k_base"
"""Used when tiktoken has no encoding registered for the configured model.

Model identifiers here are provider-scoped -- `openai/gpt-4.1-mini` is not a
name tiktoken knows -- and a newly released model is unknown to any pinned
tiktoken. The count is then approximate for a second reason on top of being an
estimate at all, which is fine: it is already flagged `tokens_estimated`, and
an approximate flagged number beats no number.
"""


def _extract_tokens(raw: Any) -> tuple[int, int] | None:
    """Read (input, output) tokens from whichever surface the provider filled.

    Returns `None` when neither is present, which is the signal to estimate.
    A zero-filled tuple would be indistinguishable from a real zero-token
    call and would silently suppress the estimation path.
    """
    usage = getattr(raw, "usage_metadata", None)
    if usage:
        return int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))

    metadata = getattr(raw, "response_metadata", None) or {}
    token_usage = metadata.get("token_usage") or {}
    if "prompt_tokens" in token_usage or "completion_tokens" in token_usage:
        return (
            int(token_usage.get("prompt_tokens", 0)),
            int(token_usage.get("completion_tokens", 0)),
        )
    return None


def _extract_provider_cost(raw: Any) -> float | None:
    """The gateway's own charge for this call, when it reports one.

    Measured 2026-08-25: OpenRouter returns it in
    `response_metadata["token_usage"]["cost"]`; OpenAI direct omits the field
    entirely. `None` means "not reported" and is distinct from a reported
    `0.0`, which a free tier really does return -- see `price_call`.
    """
    metadata = getattr(raw, "response_metadata", None) or {}
    cost = (metadata.get("token_usage") or {}).get("cost")
    return float(cost) if cost is not None else None


class TrackedLLM:
    """A chat model that records every call it makes."""

    def __init__(
        self,
        model: BaseChatModel,
        *,
        model_name: str,
        pricing: Mapping[str, ModelPrice],
    ) -> None:
        self._model = model
        self._model_name = model_name
        self._pricing = pricing

    def _encoding(self) -> tiktoken.Encoding:
        try:
            return tiktoken.encoding_for_model(self._model_name)
        except KeyError:
            return tiktoken.get_encoding(FALLBACK_ENCODING)

    def _estimate(self, prompt: str, completion: str) -> tuple[int, int]:
        encoding = self._encoding()
        return len(encoding.encode(prompt)), len(encoding.encode(completion))

    async def invoke_structured(
        self,
        *,
        node: str,
        prompt: str,
        schema: type[SchemaT],
    ) -> tuple[SchemaT, LLMCall]:
        """Call the model for a structured answer, and record what it cost.

        `call_id` is a fresh UUID per *actual provider call*, and that is what
        makes `UsageSummary`'s deduplication safe rather than lossy. The two
        ways a call can appear twice are genuinely different: LangGraph
        replaying a node's state writes appends the same record twice (same
        id, deduplicated, correct), while a node re-executing after a resume
        makes a second real call that is really billed (new id, counted
        twice, also correct). A id derived from the node name would conflate
        them and under-report every run.

        ADR-001 records the companion rule that keeps this honest: a node that
        calls `interrupt()` performs no LLM call before it, because that work
        would be billed twice while only one record survives.
        """
        started_at = datetime.now(UTC)
        structured = self._model.with_structured_output(schema, include_raw=True)

        try:
            result = await structured.ainvoke(prompt)
        except openai.RateLimitError as exc:
            raise LLMRateLimitedError(
                "The model provider is rate limiting this run. Waiting and retrying "
                "should succeed.",
                detail=f"node={node!r} model={self._model_name!r}: {exc}",
            ) from exc
        except (openai.APIConnectionError, openai.APITimeoutError, openai.APIStatusError) as exc:
            raise LLMUnavailableError(
                "The model provider could not be reached, so this step could not complete.",
                detail=f"node={node!r} model={self._model_name!r}: {exc}",
            ) from exc
        # Anything else propagates unchanged, deliberately. A `TypeError` from
        # our own prompt construction is a bug, not an outage; classifying it
        # as LLM_UNAVAILABLE would make it retryable and give it a message
        # blaming the provider, so the run would retry a deterministic failure
        # and then report the wrong cause. Only what is recognised is typed.

        raw = result.get("raw") if isinstance(result, dict) else None
        parsed = result.get("parsed") if isinstance(result, dict) else None
        parsing_error = result.get("parsing_error") if isinstance(result, dict) else None

        if parsed is None:
            # `include_raw=True` reports a parse failure in the mapping rather
            # than raising, so an unchecked caller would carry a `None` into
            # state wearing the shape of a real answer. Both the
            # error-reported and the silently-absent cases land here.
            raise LLMUnavailableError(
                "The model returned a response that did not match the expected format, "
                "so this step could not complete.",
                detail=(
                    f"node={node!r} model={self._model_name!r} schema={schema.__name__}: "
                    f"{parsing_error if parsing_error is not None else 'no parsed value returned'}"
                ),
            )

        tokens = _extract_tokens(raw)
        estimated = tokens is None
        if tokens is None:
            completion = str(getattr(raw, "content", "") or "")
            input_tokens, output_tokens = self._estimate(prompt, completion)
        else:
            input_tokens, output_tokens = tokens

        cost, basis = price_call(
            model=self._model_name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            pricing=self._pricing,
            provider_cost=_extract_provider_cost(raw),
        )

        call = LLMCall(
            call_id=str(uuid.uuid4()),
            node=node,
            model=self._model_name,
            kind=LLMCallKind.CHAT,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            tokens_estimated=estimated,
            cost_usd=cost,
            cost_basis=basis,
            started_at=started_at,
        )
        return parsed, call
