"""The clamp and the ceilings: spec 8.1's two guarantees, tested from both ends.

Each is asserted twice on purpose -- once through `build_risk_analysis`, which
is how the graph reaches it, and once against `RiskAnalysis` directly, which
is where it is actually enforced. The second is the one that matters: a rule
that lives in the builder holds until someone writes a second builder, and
the point of putting it in the model's validators is that there is no second
door.
"""

import pytest
from pydantic import ValidationError

from tests.unit.test_risk_factors import (
    a_change,
    an_affected_file,
    an_analysis,
    inputs_for,
)
from upgradepilot.models.enums import (
    Confidence,
    DependencyRole,
    RagStopReason,
    RiskCategory,
    RiskLevel,
    Severity,
)
from upgradepilot.models.evidence import RepoEvidence, RiskFactor
from upgradepilot.models.knowledge import RagContext
from upgradepilot.models.risk import RISK_ORDER, ConfidenceCeiling, RiskAnalysis, higher_risk
from upgradepilot.services.risk.aggregate import (
    BASE_CONFIDENCE,
    NO_EVIDENCE_CEILING,
    NO_FACTORS_CEILING,
    SKIPPED_FILES_CEILING,
    TRANSITIVE_ONLY_CEILING,
    UNDOCUMENTED_SYMBOL_CEILING,
    UNKNOWN_CHURN_CEILING,
    aggregate_level,
    build_risk_analysis,
    clamp_floor,
    confidence_ceilings,
)
from upgradepilot.services.risk.factors import extract_factors
from upgradepilot.services.risk.thresholds import THRESHOLDS


def a_context(sources: int = 5) -> RagContext:
    return RagContext(
        iterations=1,
        sources_considered=sources,
        sufficient=sources > 0,
        stop_reason=RagStopReason.SUFFICIENT if sources else RagStopReason.ITERATION_LIMIT,
    )


def a_factor(category: RiskCategory, level: RiskLevel) -> RiskFactor:
    return RiskFactor(
        id=category.value,
        name=category.value,
        category=category,
        level=level,
        weight=THRESHOLDS[category].weight,
        detail="measured",
        evidence=(RepoEvidence(file="a.py", line=1),),
    )


# -- ordering ---------------------------------------------------------------


def test_risk_levels_are_ordered_by_the_table_not_alphabetically() -> None:
    """`RiskLevel` is a `StrEnum`, so `"low" > "high"` is True and
    `max("high", "low")` is `"low"`. A clamp written with the obvious
    operator would quietly clamp *down* -- which is the one direction that
    under-reports."""
    assert max(RiskLevel.HIGH, RiskLevel.LOW) is RiskLevel.LOW
    assert higher_risk(RiskLevel.HIGH, RiskLevel.LOW) is RiskLevel.HIGH
    assert RISK_ORDER[RiskLevel.HIGH] > RISK_ORDER[RiskLevel.MEDIUM]


# -- the aggregate ----------------------------------------------------------


def test_the_aggregate_divides_by_the_factors_that_are_present() -> None:
    """Dividing by the full table's weight would make an omitted factor pull
    the verdict down: a repository whose history could not be read would
    score lower than an identical one whose history was read and showed
    churn, for no reason but the missing measurement. Absent evidence lowers
    confidence, never risk."""
    one_high = [a_factor(RiskCategory.BREAKING_CHANGE_EXPOSURE, RiskLevel.HIGH)]

    assert aggregate_level(one_high) is RiskLevel.HIGH


def test_an_empty_factor_set_aggregates_low() -> None:
    """And is prevented from reading as reassurance by the no-factors ceiling
    rather than by inventing a level here."""
    assert aggregate_level([]) is RiskLevel.LOW


def test_mixed_factors_land_between_their_levels() -> None:
    factors = [
        a_factor(RiskCategory.BREAKING_CHANGE_EXPOSURE, RiskLevel.HIGH),
        a_factor(RiskCategory.BLAST_RADIUS, RiskLevel.LOW),
        a_factor(RiskCategory.CHURN_ON_AFFECTED, RiskLevel.LOW),
        a_factor(RiskCategory.CONSTRAINT_PRESSURE, RiskLevel.LOW),
    ]

    assert aggregate_level(factors) is RiskLevel.MEDIUM


# -- the clamp --------------------------------------------------------------


def test_the_clamp_raises_a_comfortable_aggregate() -> None:
    """Spec 8.1: `overall_risk` cannot be set below the maximum severity among
    confirmed high-confidence breaking-change exposures. One documented
    high-severity break in a large repository barely moves the weighted mean,
    and is still a high-risk upgrade."""
    analysis = an_analysis(
        affected=(an_affected_file("a.py"),),
        total_python_files=500,
        test_paths=("tests/test_a.py",),
    )
    changes = (a_change(severity=Severity.HIGH),)
    factors = extract_factors(inputs_for(analysis, changes=changes))

    risk = build_risk_analysis(
        analysis=analysis,
        breaking_changes=changes,
        rag_context=a_context(),
        factors=factors,
        summary="prose",
    )

    assert risk.clamp_floor is RiskLevel.HIGH
    assert risk.overall_risk is RiskLevel.HIGH
    assert risk.aggregate_risk is not RiskLevel.HIGH, (
        "the aggregate already reached high, so the clamp proved nothing here"
    )


def test_no_confirmed_exposure_means_no_floor_at_all() -> None:
    """`None` rather than a floor of LOW. A floor of LOW is a floor nothing
    can fall below anyway; `None` is what tells the reader "no documented
    break is in use here" as distinct from "the one in use is minor"."""
    analysis = an_analysis(affected=(an_affected_file("a.py"),))

    assert clamp_floor(analysis, ()) is None


def test_a_medium_severity_break_clamps_only_to_medium() -> None:
    analysis = an_analysis(affected=(an_affected_file("a.py"),), total_python_files=500)
    changes = (a_change(severity=Severity.MEDIUM),)

    assert clamp_floor(analysis, changes) is RiskLevel.MEDIUM


def test_a_verdict_below_its_own_clamp_floor_is_unconstructable() -> None:
    """Where the guarantee actually lives. The builder is one door; this is
    the wall."""
    with pytest.raises(ValidationError, match="clamp_floor"):
        RiskAnalysis(
            overall_risk=RiskLevel.LOW,
            aggregate_risk=RiskLevel.LOW,
            clamp_floor=RiskLevel.HIGH,
            confidence=0.5,
            confidence_ceilings=(ConfidenceCeiling(reason="because", ceiling=0.5),),
            summary="prose",
        )


def test_a_verdict_inflated_past_its_inputs_is_also_unconstructable() -> None:
    """The quieter failure. A verdict above what its own inputs support is
    unfalsifiable in the report and destroys the reader's ability to tell a
    serious finding from a cautious one."""
    with pytest.raises(ValidationError, match="is not"):
        RiskAnalysis(
            overall_risk=RiskLevel.HIGH,
            aggregate_risk=RiskLevel.LOW,
            clamp_floor=None,
            confidence=0.5,
            confidence_ceilings=(ConfidenceCeiling(reason="because", ceiling=0.5),),
            summary="prose",
        )


# -- the ceilings -----------------------------------------------------------


def test_no_retrieved_evidence_caps_confidence_hard() -> None:
    analysis = an_analysis(affected=(an_affected_file("a.py"),))

    ceilings = confidence_ceilings(
        analysis=analysis,
        rag_context=a_context(sources=0),
        breaking_changes=(),
        factors=[a_factor(RiskCategory.BLAST_RADIUS, RiskLevel.LOW)],
    )

    assert min(c.ceiling for c in ceilings) <= NO_EVIDENCE_CEILING


def test_a_missing_rag_context_is_treated_as_no_evidence() -> None:
    """`None` means the retrieval subgraph produced no context -- the analysis
    failed upstream, or the loop was skipped. In every one of those cases no
    corpus document informed the run, so it is the same fact."""
    analysis = an_analysis(affected=(an_affected_file("a.py"),))

    ceilings = confidence_ceilings(
        analysis=analysis, rag_context=None, breaking_changes=(), factors=[]
    )

    assert any(c.ceiling == NO_EVIDENCE_CEILING for c in ceilings)


def test_more_than_a_tenth_unparseable_caps_confidence() -> None:
    analysis = an_analysis(
        affected=(an_affected_file("a.py"),),
        total_python_files=10,
        skipped=("b.py", "c.py"),
    )

    ceilings = confidence_ceilings(
        analysis=analysis,
        rag_context=a_context(),
        breaking_changes=(a_change(),),
        factors=[a_factor(RiskCategory.BLAST_RADIUS, RiskLevel.LOW)],
    )

    assert any(c.ceiling == SKIPPED_FILES_CEILING for c in ceilings)


def test_exactly_a_tenth_unparseable_does_not_cap() -> None:
    """Spec 8.1 says skipped files must *exceed* 10%. The boundary is worth a
    test in its own right: `>` and `>=` differ only here, and only for the
    repository that sits on it."""
    analysis = an_analysis(
        affected=(an_affected_file("a.py"),), total_python_files=10, skipped=("b.py",)
    )

    ceilings = confidence_ceilings(
        analysis=analysis,
        rag_context=a_context(),
        breaking_changes=(a_change(),),
        factors=[a_factor(RiskCategory.BLAST_RADIUS, RiskLevel.LOW)],
    )

    assert not any(c.ceiling == SKIPPED_FILES_CEILING for c in ceilings)


def test_a_transitive_only_pin_caps_confidence() -> None:
    analysis = an_analysis(
        affected=(an_affected_file("a.py"),), role=DependencyRole.TRANSITIVE_ONLY
    )

    ceilings = confidence_ceilings(
        analysis=analysis,
        rag_context=a_context(),
        breaking_changes=(a_change(),),
        factors=[a_factor(RiskCategory.BLAST_RADIUS, RiskLevel.LOW)],
    )

    assert any(c.ceiling == TRANSITIVE_ONLY_CEILING for c in ceilings)


def test_an_undocumented_high_confidence_symbol_caps_confidence() -> None:
    analysis = an_analysis(affected=(an_affected_file("a.py", symbols=("validator", "Config")),))

    ceilings = confidence_ceilings(
        analysis=analysis,
        rag_context=a_context(),
        breaking_changes=(a_change(symbols=("validator",)),),
        factors=[a_factor(RiskCategory.BLAST_RADIUS, RiskLevel.LOW)],
    )

    capped = [c for c in ceilings if c.ceiling == UNDOCUMENTED_SYMBOL_CEILING]
    assert capped and "Config" in capped[0].reason


def test_unreadable_history_caps_confidence_rather_than_reporting_calm() -> None:
    analysis = an_analysis(affected=(an_affected_file("a.py", commit_count=None),))

    ceilings = confidence_ceilings(
        analysis=analysis,
        rag_context=a_context(),
        breaking_changes=(a_change(),),
        factors=[a_factor(RiskCategory.BLAST_RADIUS, RiskLevel.LOW)],
    )

    assert any(c.ceiling == UNKNOWN_CHURN_CEILING for c in ceilings)


def test_a_verdict_from_no_factors_cannot_be_confident() -> None:
    """Without this a repository the analyzer found nothing in produces
    `overall_risk=low` at full confidence -- "we looked and it is fine" --
    when what happened is "we found nothing to look at"."""
    analysis = an_analysis()

    risk = build_risk_analysis(
        analysis=analysis,
        breaking_changes=(),
        rag_context=a_context(),
        factors=(),
        summary="prose",
    )

    assert risk.factors == ()
    assert risk.confidence <= NO_FACTORS_CEILING


def test_a_clean_run_is_confident_but_never_certain() -> None:
    """The missing 0.15 is not modesty. This system reads a repository
    without executing it: a symbol reached through `getattr`, a plugin loaded
    by name, a dependency pinned by an environment the manifest does not
    describe -- none of them are visible to any amount of parsing."""
    analysis = an_analysis(affected=(an_affected_file("a.py"),), test_paths=("tests/test_a.py",))
    changes = (a_change(),)

    risk = build_risk_analysis(
        analysis=analysis,
        breaking_changes=changes,
        rag_context=a_context(),
        factors=extract_factors(inputs_for(analysis, changes=changes)),
        summary="prose",
    )

    assert risk.confidence_ceilings == ()
    assert risk.confidence == BASE_CONFIDENCE
    assert risk.confidence < 1.0


def test_confidence_above_a_recorded_ceiling_is_unconstructable() -> None:
    """The ceilings are in the same object as the number they bound, so the
    check needs nothing from outside and cannot be skipped by a caller that
    forgot to apply one -- only by not recording the ceiling, which is a
    visible omission rather than an invisible one."""
    with pytest.raises(ValidationError, match="exceeds the ceiling"):
        RiskAnalysis(
            overall_risk=RiskLevel.LOW,
            aggregate_risk=RiskLevel.LOW,
            confidence=0.9,
            confidence_ceilings=(
                ConfidenceCeiling(reason="no evidence was retrieved", ceiling=0.3),
            ),
            factors=(a_factor(RiskCategory.BLAST_RADIUS, RiskLevel.LOW),),
            summary="prose",
        )


def test_the_lowest_ceiling_wins_when_several_apply() -> None:
    analysis = an_analysis(
        affected=(an_affected_file("a.py", symbols=("validator", "Config")),),
        total_python_files=10,
        skipped=("b.py", "c.py"),
        role=DependencyRole.TRANSITIVE_ONLY,
    )
    changes = (a_change(symbols=("validator",)),)

    risk = build_risk_analysis(
        analysis=analysis,
        breaking_changes=changes,
        rag_context=a_context(sources=0),
        factors=extract_factors(inputs_for(analysis, changes=changes)),
        summary="prose",
    )

    assert risk.confidence == NO_EVIDENCE_CEILING
    assert len(risk.confidence_ceilings) >= 4


def test_qualitative_notes_carry_no_weight_in_any_level() -> None:
    """Spec 8.1's last line, asserted by construction: the same inputs with
    and without the model's prose produce the same numbers."""
    analysis = an_analysis(affected=(an_affected_file("a.py"),))
    changes = (a_change(),)
    factors = extract_factors(inputs_for(analysis, changes=changes))

    def built(summary: str, notes: tuple[str, ...]) -> RiskAnalysis:
        return build_risk_analysis(
            analysis=analysis,
            breaking_changes=changes,
            rag_context=a_context(),
            factors=factors,
            summary=summary,
            qualitative_notes=notes,
        )

    plain = built("prose", ())
    loud = built("THIS IS CATASTROPHIC AND CERTAIN", ("panic",) * 3)

    assert loud.overall_risk is plain.overall_risk
    assert loud.confidence == plain.confidence
    assert loud.aggregate_risk is plain.aggregate_risk


def test_blank_notes_are_dropped_rather_than_stored() -> None:
    analysis = an_analysis(affected=(an_affected_file("a.py"),))

    risk = build_risk_analysis(
        analysis=analysis,
        breaking_changes=(a_change(),),
        rag_context=a_context(),
        factors=extract_factors(inputs_for(analysis, changes=(a_change(),))),
        summary="prose",
        qualitative_notes=("real note", "   ", ""),
    )

    assert risk.qualitative_notes == ("real note",)


def test_a_medium_confidence_symbol_does_not_trigger_the_undocumented_ceiling() -> None:
    """The ceiling is about symbols the repository *certainly* uses. Applying
    it to inferred usage would cap confidence on every repository whose
    analyzer made a single medium-confidence inference."""
    analysis = an_analysis(
        affected=(an_affected_file("a.py", symbols=("dict",), confidence=Confidence.MEDIUM),)
    )

    ceilings = confidence_ceilings(
        analysis=analysis,
        rag_context=a_context(),
        breaking_changes=(),
        factors=[a_factor(RiskCategory.BLAST_RADIUS, RiskLevel.LOW)],
    )

    assert not any(c.ceiling == UNDOCUMENTED_SYMBOL_CEILING for c in ceilings)
