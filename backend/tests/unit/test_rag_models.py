"""The RAG loop's records, and the invariants that keep them honest.

Three shapes arrive with Phase 5, and each one carries a constraint that
exists because the alternative is a plausible lie: a gate that disagrees with
its own evidence, a sufficiency claim over nothing retrieved, and a
"retrieval was unnecessary" verdict stamped on a loop that ran.
"""

import pytest
from pydantic import ValidationError

from upgradepilot.models.enums import QueryOrigin, RagStopReason, SourceType
from upgradepilot.models.inputs import DependencySpec
from upgradepilot.models.knowledge import RagContext, RagEvaluation, RagQuery

# -- RagQuery ---------------------------------------------------------------


def test_a_query_records_the_filters_it_actually_sent() -> None:
    query = RagQuery(
        query_id="q-1",
        iteration=1,
        text="validator decorator replacement",
        symbols=("validator",),
        source_types=(SourceType.MIGRATION_GUIDE,),
        to_version_major=2,
        rationale="the repository uses @validator at three sites",
    )

    assert query.symbols == ("validator",)
    assert query.source_types == (SourceType.MIGRATION_GUIDE,)
    assert query.origin is QueryOrigin.MODEL


def test_a_query_must_say_why_it_was_issued() -> None:
    """`rationale` is what the trace renders. A blank one is a row that says
    nothing, indistinguishable from a rendering bug."""
    with pytest.raises(ValidationError):
        RagQuery(query_id="q-1", iteration=1, text="anything", rationale="   ")


def test_a_query_belongs_to_a_real_iteration() -> None:
    with pytest.raises(ValidationError):
        RagQuery(query_id="q-1", iteration=0, text="anything", rationale="because")


# -- RagEvaluation: the gate ------------------------------------------------


def an_evaluation(**overrides: object) -> RagEvaluation:
    fields: dict[str, object] = {
        "iteration": 1,
        "model_sufficient": True,
        "gate_sufficient": True,
        "candidates_considered": 4,
    }
    fields.update(overrides)
    return RagEvaluation(**fields)


def test_the_gate_vetoes_a_model_that_declared_victory() -> None:
    """Spec 7.3's override, at the level of the type.

    The model said sufficient; one high-confidence symbol has no document
    behind it; the verdict is insufficient.
    """
    evaluation = an_evaluation(
        model_sufficient=True,
        gate_sufficient=False,
        uncovered_symbols=("validator",),
        uncovered_high_confidence=("validator",),
    )

    assert evaluation.sufficient is False


def test_the_gate_cannot_rescue_a_model_that_asked_for_another_round() -> None:
    """The override is one-directional. A passing gate over a model that
    wants more evidence buys another iteration, it does not end the loop."""
    evaluation = an_evaluation(model_sufficient=False, gate_sufficient=True)

    assert evaluation.sufficient is False


def test_both_agreeing_is_the_only_way_to_be_sufficient() -> None:
    assert an_evaluation(model_sufficient=True, gate_sufficient=True).sufficient is True


def test_a_passing_gate_beside_an_uncovered_high_confidence_symbol_is_unconstructable() -> None:
    with pytest.raises(ValidationError, match="contradicts"):
        an_evaluation(
            gate_sufficient=True,
            uncovered_symbols=("validator",),
            uncovered_high_confidence=("validator",),
        )


def test_a_failing_gate_with_nothing_uncovered_is_unconstructable() -> None:
    """The other direction of the same rule. A gate that fails for reasons
    not present in its own evidence is a loop that iterates forever on
    something the reader cannot see."""
    with pytest.raises(ValidationError, match="contradicts"):
        an_evaluation(gate_sufficient=False)


def test_a_high_confidence_gap_must_appear_in_the_uncovered_set() -> None:
    """`uncovered_symbols` is what the report prints as unknowns; a
    high-confidence gap missing from it is a gap nobody is shown."""
    with pytest.raises(ValidationError, match="absent from uncovered_symbols"):
        an_evaluation(
            gate_sufficient=False,
            uncovered_symbols=("Config",),
            uncovered_high_confidence=("validator",),
        )


# -- RagContext -------------------------------------------------------------


def a_context(**overrides: object) -> RagContext:
    fields: dict[str, object] = {
        "iterations": 1,
        "sources_considered": 5,
        "sufficient": True,
        "stop_reason": RagStopReason.SUFFICIENT,
    }
    fields.update(overrides)
    return RagContext(**fields)


def test_evidence_available_is_derived_from_the_source_count() -> None:
    assert a_context(sources_considered=5).evidence_available is True
    assert (
        a_context(
            sources_considered=0,
            sufficient=False,
            stop_reason=RagStopReason.ITERATION_LIMIT,
            iterations=3,
        ).evidence_available
        is False
    )


def test_a_sufficient_context_over_no_sources_is_unconstructable() -> None:
    """The empty-corpus lie: the deterministic gate passes trivially over an
    empty inventory, and without this the verdict would travel onward as
    "retrieval succeeded" into a report with nothing behind it."""
    with pytest.raises(ValidationError, match="sources_considered=0"):
        a_context(sources_considered=0)


@pytest.mark.parametrize("reason", [RagStopReason.KB_UNAVAILABLE, RagStopReason.ITERATION_LIMIT])
def test_the_two_failure_stop_reasons_cannot_claim_success(reason: RagStopReason) -> None:
    with pytest.raises(ValidationError, match="stopped without reaching sufficiency"):
        a_context(stop_reason=reason)


def test_retrieval_that_was_unnecessary_cannot_also_have_iterated() -> None:
    with pytest.raises(ValidationError, match="not_necessary"):
        a_context(stop_reason=RagStopReason.NOT_NECESSARY, sufficient=False, iterations=2)


def test_a_skipped_retrieval_is_a_valid_context() -> None:
    context = a_context(
        stop_reason=RagStopReason.NOT_NECESSARY,
        sufficient=False,
        iterations=0,
        sources_considered=0,
    )

    assert context.evidence_available is False


# -- DependencySpec.target_major -------------------------------------------


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("2.9.0", 2),
        ("2.0", 2),
        ("2", 2),
        ("2.0b1", 2),
        ("10.1", 10),
        ("latest", None),
        ("v2.0", None),
    ],
)
def test_target_major_reads_only_the_leading_integer(target: str, expected: int | None) -> None:
    """`None` rather than a guess. An omitted filter retrieves too broadly,
    which is recoverable; a guessed one retrieves confidently from the wrong
    release, which is not."""
    spec = DependencySpec(name="pydantic", current_version="1.10.13", target_version=target)

    assert spec.target_major == expected
