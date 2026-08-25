"""Costing one model call.

Spec §9.4, including the refinement measured on 2026-08-25 that Phase 4 owns:
OpenRouter returns the real per-call charge in
`response_metadata["token_usage"]["cost"]` and OpenAI direct returns no such
field. So a provider-reported charge is preferred where one exists and the
price table is the fallback -- and the two are recorded as different
`CostBasis` values, because a charge and a lookup are different facts.

Three outcomes, and the third is the one this module exists for: an unknown
model produces `None`, never `$0.00`. A fabricated zero is worse than a blank,
because a blank prompts the question and a zero answers it wrongly.
"""

from collections.abc import Mapping

from upgradepilot.config import ModelPrice
from upgradepilot.models.enums import CostBasis

PER_MILLION = 1_000_000


def price_call(
    *,
    model: str,
    input_tokens: int,
    output_tokens: int,
    pricing: Mapping[str, ModelPrice],
    provider_cost: float | None,
) -> tuple[float | None, CostBasis]:
    """Return `(cost_usd, basis)` for one call.

    `provider_cost is not None` rather than a truthiness check, deliberately:
    a free tier really does bill `0.0`, and `0.0` is falsy. Treating it as
    absent would discard the provider's own answer and fall through to the
    table, turning a known-free call into a fabricated charge -- the failure
    this module prevents, arriving by the back door.

    Model lookup is **exact**. `openai/gpt-4.1-mini` and `gpt-4.1-mini` are
    the same underlying model and the tempting shortcut is to strip the vendor
    prefix and price what is left; that is refused. The prefix names who is
    *serving* the model, and a gateway sets its own price, so stripping it
    produces a confident number wrong by the gateway's margin. `None` is the
    better answer, and both spellings are listed explicitly in
    `DEFAULT_MODEL_PRICING` so the common cases are priced without guessing.
    """
    if provider_cost is not None:
        return provider_cost, CostBasis.PROVIDER_REPORTED

    price = pricing.get(model)
    if price is None:
        return None, CostBasis.UNKNOWN

    cost = (input_tokens * price.input_per_1m + output_tokens * price.output_per_1m) / PER_MILLION
    return cost, CostBasis.PRICE_TABLE
