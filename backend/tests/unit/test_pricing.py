"""Costing one call: provider-reported first, price table second, never guessed.

Spec §9.4, including the measured refinement it assigns to Phase 4. OpenRouter
returns a real per-call charge in `response_metadata["token_usage"]["cost"]`;
OpenAI direct returns no such field. So a measured charge is preferred where
one exists and the table is the fallback -- and the two are recorded as
different `CostBasis` values, because a charge and a lookup are different
facts and the report must not print them identically.

The third outcome is the one that matters most: an unknown model produces
`None`, never `$0.00`. A fabricated zero is worse than a blank, because a
blank prompts the question and a zero answers it wrongly.
"""

import pytest

from upgradepilot.config import ModelPrice
from upgradepilot.models.enums import CostBasis
from upgradepilot.services.llm.pricing import price_call

PRICING = {
    "gpt-4.1-mini": ModelPrice(input_per_1m=0.40, output_per_1m=1.60),
    "text-embedding-3-small": ModelPrice(input_per_1m=0.02, output_per_1m=0.0),
}


def test_a_provider_reported_charge_is_used_as_is() -> None:
    """It is the actual amount billed. Recomputing it from a table would
    replace a measurement with an estimate and lose the gateway's own
    markup."""
    cost, basis = price_call(
        model="gpt-4.1-mini",
        input_tokens=1_000_000,
        output_tokens=0,
        pricing=PRICING,
        provider_cost=0.000123,
    )

    assert cost == pytest.approx(0.000123)
    assert basis is CostBasis.PROVIDER_REPORTED


def test_a_reported_charge_of_exactly_zero_is_still_a_measurement() -> None:
    """A free tier really does bill zero, and `0.0` is falsy.

    A truthiness check here would silently discard the provider's answer and
    fall through to the table, turning a known-free call into a fabricated
    charge -- the failure this module exists to prevent, arriving by the back
    door.
    """
    cost, basis = price_call(
        model="gpt-4.1-mini",
        input_tokens=1000,
        output_tokens=1000,
        pricing=PRICING,
        provider_cost=0.0,
    )

    assert cost == 0.0
    assert basis is CostBasis.PROVIDER_REPORTED


def test_the_table_prices_input_and_output_at_their_own_rates() -> None:
    """Output tokens cost several times input tokens on every provider in
    use, so a single blended rate would misprice every call whose output-to-
    input ratio differs from whatever ratio the blend assumed."""
    cost, basis = price_call(
        model="gpt-4.1-mini",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        pricing=PRICING,
        provider_cost=None,
    )

    assert cost == pytest.approx(0.40 + 1.60)
    assert basis is CostBasis.PRICE_TABLE


def test_the_table_scales_per_million_tokens() -> None:
    cost, _ = price_call(
        model="gpt-4.1-mini",
        input_tokens=1000,
        output_tokens=500,
        pricing=PRICING,
        provider_cost=None,
    )

    assert cost == pytest.approx(0.40 * 0.001 + 1.60 * 0.0005)


def test_an_unknown_model_costs_none_and_never_zero() -> None:
    """Spec §9.4. The `UNKNOWN` basis travels with it so nothing downstream
    can print the blank as a figure."""
    cost, basis = price_call(
        model="some-model-nobody-configured",
        input_tokens=1000,
        output_tokens=1000,
        pricing=PRICING,
        provider_cost=None,
    )

    assert cost is None
    assert basis is CostBasis.UNKNOWN


def test_a_vendor_prefixed_model_id_is_not_silently_stripped() -> None:
    """`openai/gpt-4.1-mini` and `gpt-4.1-mini` are the same model, and the
    temptation is to strip the prefix and price it anyway.

    Refused. The prefix names *who is serving* the model, and a gateway sets
    its own price -- OpenRouter's charge for `openai/gpt-4.1-mini` is not
    OpenAI's list price for `gpt-4.1-mini`. Stripping it would produce a
    confident number that is wrong by the gateway's margin, which is exactly
    the failure mode a `None` avoids. Both spellings are instead listed
    explicitly in the shipped table, where they are greppable and each can
    carry its own rate.
    """
    cost, basis = price_call(
        model="openai/gpt-4.1-mini",
        input_tokens=1000,
        output_tokens=1000,
        pricing=PRICING,
        provider_cost=None,
    )

    assert cost is None
    assert basis is CostBasis.UNKNOWN


def test_a_zero_token_call_against_a_known_model_costs_zero_not_none() -> None:
    """`0.0` here is a computed fact, not a fabrication: the model is priced
    and no tokens were used. Conflating it with the unknown case would lose
    the distinction the `CostBasis` exists to carry."""
    cost, basis = price_call(
        model="gpt-4.1-mini",
        input_tokens=0,
        output_tokens=0,
        pricing=PRICING,
        provider_cost=None,
    )

    assert cost == 0.0
    assert basis is CostBasis.PRICE_TABLE


def test_the_shipped_defaults_price_the_models_the_project_configures() -> None:
    """The default chat and embedding models must be in the default table, or
    the product ships reporting an unknown cost for its own configuration --
    technically honest, and useless."""
    from upgradepilot.config import Settings

    settings = Settings()

    assert settings.chat_model in settings.model_pricing
    assert settings.embedding_model in settings.model_pricing


def test_pricing_can_be_overridden_from_the_environment() -> None:
    """Rates change, and a redeploy is a worse answer than a setting."""
    from upgradepilot.config import Settings

    settings = Settings(model_pricing={"my-model": ModelPrice(input_per_1m=1.0, output_per_1m=2.0)})

    cost, basis = price_call(
        model="my-model",
        input_tokens=1_000_000,
        output_tokens=0,
        pricing=settings.model_pricing,
        provider_cost=None,
    )
    assert cost == pytest.approx(1.0)
    assert basis is CostBasis.PRICE_TABLE
