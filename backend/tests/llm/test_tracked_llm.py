"""`TrackedLLM`: the one place a chat model is called, and usage recorded.

CLAUDE.md rule 18 and spec §9.4. Nothing else in the codebase touches a chat
model, so there is exactly one place usage can be missed -- which is only
worth anything if that place is exhaustively tested, because a gap here is
invisible everywhere else. Every token figure the product prints comes from
this module.

Spec §9.4 names three hazards and each has tests below:

1. Which usage surface is populated is version-dependent, so the extractor
   reads `usage_metadata` first, falls back to `response_metadata["token_usage"]`,
   and estimates with tiktoken only when neither is present -- flagging the
   result as estimated rather than passing it off as exact.
2. `with_structured_output()` can swallow the raw message and the usage with
   it, so `include_raw=True` is mandatory and never optional.
3. Pricing is configuration, and an unknown model yields `None`.
"""

import httpx2
import openai
import pytest
from pydantic import BaseModel

from tests.llm.fake_chat_model import ScriptedChatModel, ScriptedResponse
from upgradepilot.config import ModelPrice
from upgradepilot.models.enums import CostBasis, LLMCallKind
from upgradepilot.models.errors import LLMRateLimitedError, LLMUnavailableError
from upgradepilot.services.llm.tracked import TrackedLLM


class Verdict(BaseModel):
    sufficient: bool
    reason: str


PRICING = {"scripted-model": ModelPrice(input_per_1m=1.0, output_per_1m=2.0)}


def tracked(*responses: ScriptedResponse, model_name: str = "scripted-model") -> TrackedLLM:
    return TrackedLLM(
        ScriptedChatModel(responses=list(responses)),
        model_name=model_name,
        pricing=PRICING,
    )


def a_verdict() -> Verdict:
    return Verdict(sufficient=True, reason="two sources cover every symbol")


# -- the parsed result -----------------------------------------------------


async def test_the_parsed_object_is_returned_alongside_the_call_record() -> None:
    llm = tracked(ScriptedResponse(parsed=a_verdict()))

    parsed, call = await llm.invoke_structured(
        node="evaluate_retrieval", prompt="grade this", schema=Verdict
    )

    assert parsed.sufficient is True
    assert call.node == "evaluate_retrieval"
    assert call.kind is LLMCallKind.CHAT


async def test_a_response_that_does_not_match_the_schema_raises_rather_than_returning_none() -> (
    None
):
    """`include_raw=True` reports a parse failure in the mapping instead of
    raising, so an unchecked caller would receive `parsed=None` and carry a
    `None` into state as though the model had answered.

    Typed as `LLMUnavailableError` deliberately, and the code reads oddly for
    it: a model returning unparseable output *is* available. The code is
    machine-facing -- it picks an HTTP status and a retry policy, and "retry,
    this may work next time" is right. The user-facing message says what
    actually happened.
    """
    llm = tracked(ScriptedResponse(parsed=None, parsing_error="expected an object"))

    with pytest.raises(LLMUnavailableError) as excinfo:
        await llm.invoke_structured(node="assess_risk", prompt="p", schema=Verdict)

    assert "expected an object" in (excinfo.value.detail or "")


async def test_a_missing_parse_with_no_error_reported_is_still_refused() -> None:
    """Belt and braces on the same hazard. If a provider ever returns neither
    a parsed object nor a parsing error, silently returning `None` would put
    an absent answer into state wearing the shape of a real one."""
    llm = tracked(ScriptedResponse(parsed=None, parsing_error=None))

    with pytest.raises(LLMUnavailableError):
        await llm.invoke_structured(node="assess_risk", prompt="p", schema=Verdict)


# -- hazard 1: three extraction paths --------------------------------------


async def test_usage_metadata_is_the_primary_surface() -> None:
    llm = tracked(
        ScriptedResponse(
            parsed=a_verdict(), input_tokens=140, output_tokens=31, usage="usage_metadata"
        )
    )

    _, call = await llm.invoke_structured(node="n", prompt="p", schema=Verdict)

    assert (call.input_tokens, call.output_tokens) == (140, 31)
    assert call.tokens_estimated is False


async def test_token_usage_is_the_fallback_when_usage_metadata_is_absent() -> None:
    """Which surface a provider populates is version-dependent (§9.4 hazard
    1). Phase 0 found both present on the pinned stack, so this path is
    unexercised in production today -- which is exactly why it needs a test:
    the day it starts being taken, nothing else would notice."""
    llm = tracked(
        ScriptedResponse(parsed=a_verdict(), input_tokens=77, output_tokens=13, usage="token_usage")
    )

    _, call = await llm.invoke_structured(node="n", prompt="p", schema=Verdict)

    assert (call.input_tokens, call.output_tokens) == (77, 13)
    assert call.tokens_estimated is False


async def test_neither_surface_falls_back_to_a_flagged_estimate() -> None:
    """The estimate is surfaced as estimated, never passed off as exact.

    A silent estimate is worse than a missing number: the metrics panel would
    show a precise-looking figure with no indication that nobody measured it.
    """
    llm = tracked(ScriptedResponse(parsed=a_verdict(), text="a few words of output", usage="none"))

    _, call = await llm.invoke_structured(
        node="n", prompt="a prompt with several words in it", schema=Verdict
    )

    assert call.tokens_estimated is True
    assert call.input_tokens > 0, "the prompt was not counted"
    assert call.output_tokens > 0, "the completion was not counted"


async def test_an_estimated_call_is_still_priced() -> None:
    """An estimate is a worse number, not an absent one. Refusing to price it
    would understate a run's cost precisely when the provider is being least
    forthcoming."""
    llm = tracked(ScriptedResponse(parsed=a_verdict(), usage="none"))

    _, call = await llm.invoke_structured(node="n", prompt="p", schema=Verdict)

    assert call.cost_usd is not None
    assert call.cost_basis is CostBasis.PRICE_TABLE


# -- hazard 3: pricing ------------------------------------------------------


async def test_a_provider_reported_cost_is_preferred_over_the_table() -> None:
    llm = tracked(ScriptedResponse(parsed=a_verdict(), provider_cost=0.0000088))

    _, call = await llm.invoke_structured(node="n", prompt="p", schema=Verdict)

    assert call.cost_usd == pytest.approx(0.0000088)
    assert call.cost_basis is CostBasis.PROVIDER_REPORTED


async def test_without_a_provider_cost_the_table_is_used() -> None:
    llm = tracked(ScriptedResponse(parsed=a_verdict(), input_tokens=1_000_000, output_tokens=0))

    _, call = await llm.invoke_structured(node="n", prompt="p", schema=Verdict)

    assert call.cost_usd == pytest.approx(1.0)
    assert call.cost_basis is CostBasis.PRICE_TABLE


async def test_an_unpriced_model_records_no_cost_rather_than_zero() -> None:
    llm = tracked(ScriptedResponse(parsed=a_verdict()), model_name="model-nobody-priced")

    _, call = await llm.invoke_structured(node="n", prompt="p", schema=Verdict)

    assert call.cost_usd is None
    assert call.cost_basis is CostBasis.UNKNOWN


# -- call identity: the thing that makes aggregation correct ---------------


async def test_each_call_gets_a_distinct_id() -> None:
    """`UsageSummary` deduplicates on `call_id`, so a deterministic id derived
    from the node name would collapse every call a node made into one and
    under-report the run.

    The ids must be unique per *actual provider call*, which is what makes the
    deduplication safe: it removes replayed state writes (the same record
    appended twice) without ever removing a second genuine call.
    """
    llm = tracked(ScriptedResponse(parsed=a_verdict()), ScriptedResponse(parsed=a_verdict()))

    _, first = await llm.invoke_structured(node="n", prompt="p", schema=Verdict)
    _, second = await llm.invoke_structured(node="n", prompt="p", schema=Verdict)

    assert first.call_id != second.call_id


async def test_the_call_records_the_model_and_a_timezone_aware_start() -> None:
    llm = tracked(ScriptedResponse(parsed=a_verdict()))

    _, call = await llm.invoke_structured(node="n", prompt="p", schema=Verdict)

    assert call.model == "scripted-model"
    assert call.started_at.tzinfo is not None


# -- the error taxonomy ----------------------------------------------------


async def test_a_rate_limit_is_typed_as_rate_limited_not_as_unavailable() -> None:
    """The remedy differs -- waiting helps here and does not help a
    misconfigured endpoint -- so a run that reported both as one condition
    could not tell an operator which they have."""
    llm = TrackedLLM(
        ScriptedChatModel(
            raise_on_call=openai.RateLimitError("slow down", response=_a_response(429), body=None)
        ),
        model_name="scripted-model",
        pricing=PRICING,
    )

    with pytest.raises(LLMRateLimitedError):
        await llm.invoke_structured(node="n", prompt="p", schema=Verdict)


async def test_a_connection_failure_is_typed_as_unavailable() -> None:
    llm = TrackedLLM(
        ScriptedChatModel(
            raise_on_call=openai.APIConnectionError(request=_a_request()),
        ),
        model_name="scripted-model",
        pricing=PRICING,
    )

    with pytest.raises(LLMUnavailableError):
        await llm.invoke_structured(node="n", prompt="p", schema=Verdict)


async def test_an_unrecognised_error_is_not_dressed_up_as_a_provider_failure() -> None:
    """A `TypeError` from our own prompt construction is a bug, not an outage.

    Classifying everything as `LLM_UNAVAILABLE` would make it retryable and
    give it a user-facing message blaming the provider, so the run would retry
    a deterministic failure twice and then report the wrong cause. Only what
    is recognised is classified.
    """
    llm = TrackedLLM(
        ScriptedChatModel(raise_on_call=TypeError("a bug in our own code")),
        model_name="scripted-model",
        pricing=PRICING,
    )

    with pytest.raises(TypeError):
        await llm.invoke_structured(node="n", prompt="p", schema=Verdict)


def _a_response(status: int) -> httpx2.Response:
    """Built with `httpx2`, which is what `openai==3.3.1` actually bundles.

    Not `httpx`: both are installed, their `Request` types are unrelated to
    mypy, and passing the wrong one type-checks as an error rather than
    failing at runtime -- so this is one of the few places the stricter check
    is what tells you which library the pinned client really uses."""
    return httpx2.Response(status_code=status, request=_a_request())


def _a_request() -> httpx2.Request:
    return httpx2.Request("POST", "https://x.invalid")
