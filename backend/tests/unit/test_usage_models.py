"""`LLMCall` and the derived `UsageSummary`.

Spec §6.1 and §9.4. The design decision under test is that usage is an
**append-only list of call records** and every total is derived from it by a
pure function. A stored running counter double-counts the moment LangGraph
replays an interrupted node after a resume, and it does so silently -- the
number stays plausible, just wrong, and no test that never resumes can see it.

So the property that matters most here is idempotence: aggregating the same
call twice must equal aggregating it once. `test_usage_aggregation_is_idempotent`
is that test, and `tests/graph/` exercises the same fact through a real resume.
"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from upgradepilot.models.enums import CostBasis, LLMCallKind
from upgradepilot.models.usage import LLMCall, UsageSummary


def a_call(
    call_id: str = "call-1",
    *,
    node: str = "assess_risk",
    model: str = "gpt-4.1-mini",
    kind: LLMCallKind = LLMCallKind.CHAT,
    input_tokens: int = 100,
    output_tokens: int = 20,
    estimated: bool = False,
    cost_usd: float | None = 0.0001,
    basis: CostBasis = CostBasis.PROVIDER_REPORTED,
) -> LLMCall:
    return LLMCall(
        call_id=call_id,
        node=node,
        model=model,
        kind=kind,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        tokens_estimated=estimated,
        cost_usd=cost_usd,
        cost_basis=basis,
        started_at=datetime(2026, 8, 25, 12, 0, tzinfo=UTC),
    )


# -- LLMCall's own invariants ----------------------------------------------


def test_total_tokens_is_derived_not_stored() -> None:
    """CLAUDE.md rule 21. A stored total can disagree with its own parts, and
    the parts are what the report itemises."""
    assert a_call(input_tokens=100, output_tokens=20).total_tokens == 120


def test_a_cost_without_a_basis_that_explains_it_is_unconstructable() -> None:
    """A number the reader cannot qualify. A charge the provider reported and
    a figure looked up in our own price table are different facts, and the
    report must not print them identically -- so a cost always carries which
    one it is, and `unknown` may not carry a number at all.
    """
    with pytest.raises(ValidationError, match="cost_basis"):
        a_call(cost_usd=0.0001, basis=CostBasis.UNKNOWN)


def test_an_unpriced_call_is_none_and_never_zero() -> None:
    """Spec §9.4: an unknown model yields `cost = None`, never a fabricated
    `$0.00`. Zero is a claim that the call was free."""
    call = a_call(cost_usd=None, basis=CostBasis.UNKNOWN)

    assert call.cost_usd is None


def test_a_basis_that_promises_a_cost_must_have_one() -> None:
    """The other direction: claiming the provider reported a charge while
    carrying no number is the same defect facing the other way."""
    with pytest.raises(ValidationError, match="cost"):
        a_call(cost_usd=None, basis=CostBasis.PROVIDER_REPORTED)


def test_an_embedding_call_cannot_claim_output_tokens() -> None:
    """Spec §9.4 records embeddings as `LLMCall` with `kind=EMBEDDING` and
    zero output tokens. An embedding produces a vector, not completion
    tokens, so any non-zero figure here is a number that was invented
    somewhere and would be priced at the completion rate."""
    with pytest.raises(ValidationError, match="output_tokens"):
        a_call(kind=LLMCallKind.EMBEDDING, output_tokens=5)


def test_an_embedding_call_with_no_output_tokens_is_fine() -> None:
    call = a_call(kind=LLMCallKind.EMBEDDING, output_tokens=0)

    assert call.kind is LLMCallKind.EMBEDDING
    assert call.total_tokens == call.input_tokens


# -- aggregation: the idempotence that the whole design exists for ---------


def test_usage_aggregation_is_idempotent() -> None:
    """THE Phase 4 property.

    LangGraph replays the interrupted node on resume, so the same `LLMCall`
    can be appended to the channel twice. Deduplicating on `call_id` at
    aggregation time -- rather than trusting the channel never to duplicate --
    is what makes the total correct without any node having to remember
    anything.
    """
    call = a_call("call-1")

    once = UsageSummary.from_calls([call])
    twice = UsageSummary.from_calls([call, call])

    assert once == twice
    assert twice.calls == 1
    assert twice.total_tokens == 120


def test_two_different_calls_are_both_counted() -> None:
    """The companion. Deduplication that collapsed genuinely distinct calls
    would satisfy the test above by under-counting everything."""
    summary = UsageSummary.from_calls([a_call("call-1"), a_call("call-2")])

    assert summary.calls == 2
    assert summary.total_tokens == 240


def test_the_first_record_of_a_call_id_wins() -> None:
    """A replayed node re-emits the same `call_id`. If the second copy
    replaced the first, a retry that reported different figures would
    silently rewrite history; keeping the first makes the record immutable
    once written."""
    first = a_call("call-1", input_tokens=100)
    replayed = a_call("call-1", input_tokens=999)

    summary = UsageSummary.from_calls([first, replayed])

    assert summary.input_tokens == 100


def test_an_empty_call_list_is_all_zeroes_and_not_an_error() -> None:
    """A run that has not called a model yet is the normal early state, and
    the metrics panel renders it on every poll."""
    summary = UsageSummary.from_calls([])

    assert summary.calls == 0
    assert summary.total_tokens == 0
    assert summary.estimated_cost_usd is None
    assert summary.by_model == ()
    assert summary.by_node == ()


# -- the two flags that keep the totals honest ------------------------------


def test_one_estimated_call_makes_the_whole_summary_estimated() -> None:
    """Spec §9.4: an estimated count is surfaced as estimated, never passed
    off as exact. A summary mixing measured and estimated figures is not
    exact, so the flag is a disjunction and not a majority vote."""
    summary = UsageSummary.from_calls([a_call("a"), a_call("b", estimated=True)])

    assert summary.estimated is True


def test_a_summary_of_measured_calls_is_not_estimated() -> None:
    assert UsageSummary.from_calls([a_call("a"), a_call("b")]).estimated is False


def test_one_unpriced_call_makes_pricing_incomplete() -> None:
    """`pricing_complete` is what stops the printed total being read as the
    cost. With an unknown model in the mix the figure is a *lower bound*, and
    the flag is the only thing that says so."""
    summary = UsageSummary.from_calls(
        [a_call("a", cost_usd=0.01), a_call("b", cost_usd=None, basis=CostBasis.UNKNOWN)]
    )

    assert summary.pricing_complete is False
    assert summary.estimated_cost_usd == pytest.approx(0.01)


def test_the_cost_of_a_wholly_unpriced_run_is_none_not_zero() -> None:
    """Summing an empty set of known costs to `0.0` would print `$0.00` for a
    run whose cost is simply unknown -- exactly the fabrication §9.4 forbids,
    reintroduced at the aggregate level after being prevented per call."""
    summary = UsageSummary.from_calls([a_call("a", cost_usd=None, basis=CostBasis.UNKNOWN)])

    assert summary.pricing_complete is False
    assert summary.estimated_cost_usd is None


def test_pricing_is_complete_when_every_call_carries_a_cost() -> None:
    summary = UsageSummary.from_calls([a_call("a", cost_usd=0.01), a_call("b", cost_usd=0.02)])

    assert summary.pricing_complete is True
    assert summary.estimated_cost_usd == pytest.approx(0.03)


# -- the breakdowns a developer actually asks for ---------------------------


def test_usage_is_broken_down_by_model() -> None:
    summary = UsageSummary.from_calls(
        [
            a_call("a", model="gpt-4.1-mini", input_tokens=100, output_tokens=20),
            a_call("b", model="gpt-4.1-mini", input_tokens=50, output_tokens=10),
            a_call(
                "c",
                model="text-embedding-3-small",
                kind=LLMCallKind.EMBEDDING,
                input_tokens=7,
                output_tokens=0,
            ),
        ]
    )

    by_model = {entry.model: entry for entry in summary.by_model}
    assert by_model["gpt-4.1-mini"].input_tokens == 150
    assert by_model["gpt-4.1-mini"].calls == 2
    assert by_model["text-embedding-3-small"].total_tokens == 7


def test_usage_is_broken_down_by_node() -> None:
    """Spec §9.4 keeps `by_node` because "where did the tokens actually go" is
    the second question a developer asks."""
    summary = UsageSummary.from_calls(
        [
            a_call("a", node="agentic_rag", input_tokens=100),
            a_call("b", node="agentic_rag", input_tokens=100),
            a_call("c", node="assess_risk", input_tokens=10),
        ]
    )

    by_node = {entry.node: entry for entry in summary.by_node}
    assert by_node["agentic_rag"].input_tokens == 200
    assert by_node["assess_risk"].calls == 1


def test_the_breakdowns_are_ordered_deterministically() -> None:
    """They are rendered in a sidebar that repolls every second. Ordering by
    whatever the dict happened to yield would make the panel reshuffle
    between identical polls."""
    calls = [a_call("a", model="zeta", node="zeta_node"), a_call("b", model="alpha", node="a_node")]

    summary = UsageSummary.from_calls(calls)
    reversed_summary = UsageSummary.from_calls(list(reversed(calls)))

    assert [e.model for e in summary.by_model] == ["alpha", "zeta"]
    assert [e.node for e in summary.by_node] == ["a_node", "zeta_node"]
    assert summary == reversed_summary


def test_the_breakdowns_sum_to_the_totals() -> None:
    """An internal-consistency check the report depends on: the panel shows
    a total and a breakdown side by side, and a reader will add the rows."""
    summary = UsageSummary.from_calls(
        [a_call("a", node="one"), a_call("b", node="two"), a_call("c", node="two")]
    )

    assert sum(e.input_tokens for e in summary.by_node) == summary.input_tokens
    assert sum(e.output_tokens for e in summary.by_model) == summary.output_tokens
    assert sum(e.calls for e in summary.by_node) == summary.calls
