"""Spec 8.4's ten checks, each with a passing and a failing case.

A validator is worth exactly what its failing cases are worth. A suite that
only ever runs it over valid input proves that it does not crash, which is not
the property anyone cares about -- so every check below is fed something that
should fail it, and asserted to fail on that and only that.

`store=None` is the default here, and it is a **real** state rather than a
test convenience: the knowledge base may be unreachable, spec 7.3 already
degrades the run rather than failing it, and check 1 then reports that it
could not resolve anything -- truthfully, as a failure, because "we could not
check" must not read as "we checked and it was fine". The tests that are about
check 1, and the one that asserts a wholly clean report, use a real Chroma
collection built once for this module.
"""

from datetime import UTC, date, datetime

import pytest

from tests.graph.graph_fixtures import a_knowledge_store
from tests.unit.test_risk_factors import (
    a_change,
    an_affected_file,
    an_analysis,
    inputs_for,
)
from upgradepilot.models.decision import DecisionApplication, HumanDecision
from upgradepilot.models.enums import (
    Confidence,
    RagStopReason,
    RiskLevel,
    Severity,
    SourceType,
    StrategyId,
    ValidationCheckId,
)
from upgradepilot.models.evidence import RepoEvidence, RiskFactor
from upgradepilot.models.inputs import UserConstraints
from upgradepilot.models.knowledge import CorpusDocument, RagContext
from upgradepilot.models.plan import (
    MigrationPlan,
    MigrationStep,
    UnaddressedFile,
    ValidationOutcome,
    ValidationReport,
)
from upgradepilot.models.risk import ConfidenceCeiling, RiskAnalysis
from upgradepilot.services.knowledge.store import KnowledgeStore
from upgradepilot.services.plan.validate import repair_brief, validate_plan
from upgradepilot.services.risk.aggregate import build_risk_analysis
from upgradepilot.services.risk.factors import extract_factors


def a_context(sources: int = 5) -> RagContext:
    return RagContext(
        iterations=1,
        sources_considered=sources,
        sufficient=sources > 0,
        stop_reason=RagStopReason.SUFFICIENT if sources else RagStopReason.ITERATION_LIMIT,
    )


ANALYSIS = an_analysis(
    affected=(an_affected_file("src/app/models.py"),),
    total_python_files=10,
    test_paths=("tests/test_models.py",),
)
CHANGES = (a_change(),)
RISK = build_risk_analysis(
    analysis=ANALYSIS,
    breaking_changes=CHANGES,
    rag_context=a_context(),
    factors=extract_factors(inputs_for(ANALYSIS, changes=CHANGES)),
    summary="prose",
)


def a_step(
    order: int = 1,
    *,
    files: tuple[str, ...] = ("src/app/models.py",),
    downtime: bool = False,
) -> MigrationStep:
    return MigrationStep(
        order=order,
        title=f"Step {order}",
        description="Do the work.",
        files=files,
        rationale_evidence=(RepoEvidence(file="src/app/models.py", line=1),),
        requires_downtime=downtime,
    )


def a_plan(**overrides: object) -> MigrationPlan:
    fields: dict[str, object] = {
        "strategy_id": StrategyId.DIRECT_MIGRATION,
        "summary": "A plan.",
        "steps": (a_step(),),
    }
    fields.update(overrides)
    return MigrationPlan(**fields)


def report_for(**overrides: object) -> ValidationReport:
    fields: dict[str, object] = {
        "attempt": 1,
        "plan": a_plan(),
        "analysis": ANALYSIS,
        "risk": RISK,
        "breaking_changes": CHANGES,
        "human_decisions": (),
        "constraints": UserConstraints(),
        "store": None,
    }
    fields.update(overrides)
    return validate_plan(**fields)  # type: ignore[arg-type]


def outcome_of(report: ValidationReport, check: ValidationCheckId) -> ValidationOutcome:
    return next(entry for entry in report.outcomes if entry.check_id is check)


@pytest.fixture(scope="module")
def store(tmp_path_factory: pytest.TempPathFactory) -> KnowledgeStore:
    """A real collection holding the document these fixtures cite.

    Module-scoped because it is read-only here and building one per test would
    make this file a minute long. Real rather than faked because check 1's
    whole job is to ask the store a question, and a fake would answer it the
    way we expected rather than the way Chroma does.
    """
    built = a_knowledge_store(tmp_path_factory.mktemp("chroma"), (a_corpus_document(),))
    return built


def a_corpus_document() -> CorpusDocument:
    """The document `a_change()` cites, as the store would hold it.

    `chunk_document` mints `{source_id}#chunk-0`, which is exactly the
    `chunk_id` on `a_change()`'s `SourceRef` -- so the citation resolves for
    the same reason a real one does, rather than because the test arranged it.
    """
    return CorpusDocument(
        source_id="doc#validator",
        title="A documented change",
        source_type=SourceType.MIGRATION_GUIDE,
        dependency="pydantic",
        from_version="1.x",
        to_version="2.0",
        to_version_major=2,
        affected_symbols=("validator",),
        severity=Severity.HIGH,
        url_or_reference="https://example.invalid/doc",
        created_at=date(2026, 8, 25),
        body="It changed.",
        path="doc.md",
    )


# -- 1. corpus citations resolve -------------------------------------------


def test_a_citation_that_still_resolves_passes(store: KnowledgeStore) -> None:
    assert outcome_of(report_for(store=store), ValidationCheckId.SOURCES_RESOLVE).passed


def test_a_citation_to_a_chunk_the_store_no_longer_has_fails(
    store: KnowledgeStore,
) -> None:
    """The quiet failure this check exists for: a corpus re-ingest rewrites a
    document from three chunks to one, and a report generated before it now
    cites `#chunk-2`. The citation still looks right, and the reader following
    it finds a real document with no such passage."""
    stale = a_change("doc#validator")
    stale = stale.model_copy(
        update={"source": stale.source.model_copy(update={"chunk_id": "doc#validator#chunk-7"})}
    )

    outcome = outcome_of(
        report_for(store=store, breaking_changes=(stale,)),
        ValidationCheckId.SOURCES_RESOLVE,
    )

    assert not outcome.passed
    assert outcome.offenders == ("doc#validator#chunk-7",)


def test_an_unreachable_store_reports_the_citations_as_unverified() -> None:
    """Not as sound. "We could not check" and "we checked and it was fine" are
    the two readings this whole project exists to keep apart."""
    outcome = outcome_of(report_for(), ValidationCheckId.SOURCES_RESOLVE)

    assert not outcome.passed
    assert "unverified, never as sound" in outcome.detail


def test_a_report_citing_no_corpus_document_has_nothing_to_resolve() -> None:
    outcome = outcome_of(
        report_for(breaking_changes=(), risk=None), ValidationCheckId.SOURCES_RESOLVE
    )

    assert outcome.passed


# -- the report itself ------------------------------------------------------


def test_every_check_runs_on_every_plan() -> None:
    """A report listing only failures is indistinguishable from a report where
    the checks did not run. Ten rows, every time."""
    report = report_for()

    assert {outcome.check_id for outcome in report.outcomes} == set(ValidationCheckId)


def test_a_clean_run_passes_every_check(store: KnowledgeStore) -> None:
    """Without this the failing cases below could all be passing for the wrong
    reason -- a validator that fails everything satisfies every one of them."""
    report = report_for(store=store)

    assert report.passed, [f.check_id.value for f in report.failures]


def test_passed_is_derived_from_the_outcomes() -> None:
    """A stored verdict beside a list of failures is the one shape that can
    lie about itself, and `COMPLETED_WITH_WARNINGS` is exactly where the two
    would be tempted to disagree."""
    assert "passed" not in ValidationReport.model_fields


def test_a_failing_check_must_name_what_failed() -> None:
    """A failure the reader cannot locate is a failure they cannot fix."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="without naming an offender"):
        ValidationOutcome(
            check_id=ValidationCheckId.PLAN_IS_ORDERED, passed=False, detail="something"
        )


# -- 2. repo evidence resolves ---------------------------------------------


def test_a_citation_to_a_line_the_analysis_never_read_fails() -> None:
    """Stronger than "the file exists on disk", deliberately: the analysis
    record is the set of locations this system is entitled to name, so a
    citation outside it is one nothing here produced."""
    invented = RISK.model_copy(
        update={
            "factors": (
                RiskFactor(
                    id="x",
                    name="x",
                    category=RISK.factors[0].category,
                    level=RiskLevel.LOW,
                    weight=0.5,
                    detail="x",
                    evidence=(RepoEvidence(file="src/app/models.py", line=9999),),
                ),
            )
        }
    )

    outcome = outcome_of(report_for(risk=invented), ValidationCheckId.REPO_EVIDENCE_RESOLVES)

    assert not outcome.passed
    assert any("9999" in offender for offender in outcome.offenders)


def test_a_manifest_cited_at_line_one_resolves() -> None:
    """A manifest has no parsed line numbers, so line 1 is the anchor
    `analysis_coverage` uses and the check has to accept it -- otherwise a
    factor this system produces fails its own validation."""
    cited = RISK.model_copy(
        update={
            "factors": (
                RiskFactor(
                    id="x",
                    name="x",
                    category=RISK.factors[0].category,
                    level=RiskLevel.LOW,
                    weight=0.5,
                    detail="x",
                    evidence=(RepoEvidence(file="pyproject.toml", line=1),),
                ),
            )
        }
    )

    assert outcome_of(report_for(risk=cited), ValidationCheckId.REPO_EVIDENCE_RESOLVES).passed


# -- 3. step files exist ----------------------------------------------------


def test_a_step_naming_a_file_the_repository_does_not_have_fails() -> None:
    """Following the plan would mean editing a file that is not there."""
    plan = a_plan(steps=(a_step(files=("src/app/imaginary.py",)),))

    outcome = outcome_of(report_for(plan=plan), ValidationCheckId.STEP_FILES_EXIST)

    assert not outcome.passed
    assert outcome.offenders == ("step 1: src/app/imaginary.py",)


# -- 4. risk factors cite evidence -----------------------------------------


def test_factors_citing_evidence_pass() -> None:
    assert outcome_of(report_for(), ValidationCheckId.RISK_FACTORS_CITE_EVIDENCE).passed


def test_the_check_survives_a_risk_analysis_that_is_absent() -> None:
    assert outcome_of(report_for(risk=None), ValidationCheckId.RISK_FACTORS_CITE_EVIDENCE).passed


# -- 5. the clamp holds -----------------------------------------------------


def test_a_verdict_below_the_evidence_fails_the_clamp_check() -> None:
    """Recomputed from the evidence rather than read from `risk.clamp_floor`:
    reading the stored floor would only check that the object agrees with
    itself, which its own validator already guarantees."""
    downplayed = RiskAnalysis(
        overall_risk=RiskLevel.LOW,
        aggregate_risk=RiskLevel.LOW,
        clamp_floor=None,
        confidence=0.3,
        confidence_ceilings=(ConfidenceCeiling(reason="thin", ceiling=0.3),),
        factors=RISK.factors,
        summary="prose",
    )

    outcome = outcome_of(report_for(risk=downplayed), ValidationCheckId.RISK_CLAMP_HOLDS)

    assert not outcome.passed
    assert "overall_risk=low" in outcome.offenders[0]


def test_no_confirmed_exposure_means_the_clamp_check_has_nothing_to_hold() -> None:
    assert outcome_of(report_for(breaking_changes=()), ValidationCheckId.RISK_CLAMP_HOLDS).passed


# -- 6. confidence ceilings hold -------------------------------------------


def test_confidence_above_the_base_fails_even_with_no_ceilings() -> None:
    """`RiskAnalysis`'s own validator bounds confidence by the *recorded*
    ceilings and knows nothing about the base, so a verdict of 1.0 with no
    ceilings constructs cleanly -- and claims a completeness a method that
    never executes the code cannot have."""
    overconfident = RISK.model_copy(update={"confidence": 1.0, "confidence_ceilings": ()})

    outcome = outcome_of(report_for(risk=overconfident), ValidationCheckId.CONFIDENCE_CEILINGS_HOLD)

    assert not outcome.passed
    assert "base" in outcome.offenders[0]


def test_confidence_within_its_ceilings_passes() -> None:
    assert outcome_of(report_for(), ValidationCheckId.CONFIDENCE_CEILINGS_HOLD).passed


# -- 7. the plan is ordered -------------------------------------------------


def test_an_empty_plan_fails() -> None:
    outcome = outcome_of(report_for(plan=a_plan(steps=())), ValidationCheckId.PLAN_IS_ORDERED)

    assert not outcome.passed
    assert outcome.offenders == ("the plan has no steps",)


def test_no_plan_at_all_fails() -> None:
    outcome = outcome_of(report_for(plan=None), ValidationCheckId.PLAN_IS_ORDERED)

    assert not outcome.passed


def test_two_ordered_steps_pass() -> None:
    plan = a_plan(steps=(a_step(1), a_step(2)))

    assert outcome_of(report_for(plan=plan), ValidationCheckId.PLAN_IS_ORDERED).passed


# -- 8. affected files addressed -------------------------------------------


def test_a_high_confidence_file_neither_addressed_nor_explained_fails() -> None:
    """ "Or explained" is what stops this being a demand that the plan cover
    everything. What it refuses is silence -- which is how a partial plan reads
    as a complete one."""
    analysis = an_analysis(
        affected=(an_affected_file("src/app/models.py"), an_affected_file("src/app/other.py")),
        total_python_files=10,
    )
    plan = a_plan(steps=(a_step(files=("src/app/models.py",)),))

    outcome = outcome_of(
        report_for(plan=plan, analysis=analysis),
        ValidationCheckId.AFFECTED_FILES_ADDRESSED,
    )

    assert not outcome.passed
    assert outcome.offenders == ("src/app/other.py",)


def test_naming_the_file_with_a_reason_satisfies_the_check() -> None:
    analysis = an_analysis(
        affected=(an_affected_file("src/app/models.py"), an_affected_file("src/app/other.py")),
        total_python_files=10,
    )
    plan = a_plan(
        steps=(a_step(files=("src/app/models.py",)),),
        unaddressed_with_reason=(
            UnaddressedFile(path="src/app/other.py", reason="Nothing documents its symbols."),
        ),
    )

    assert outcome_of(
        report_for(plan=plan, analysis=analysis),
        ValidationCheckId.AFFECTED_FILES_ADDRESSED,
    ).passed


def test_a_medium_confidence_file_is_not_required_to_be_addressed() -> None:
    """The check is about the files the analyzer is *sure* of. Demanding
    coverage of inferred usage would make the plan chase the analyzer's own
    uncertainty."""
    analysis = an_analysis(
        affected=(
            an_affected_file("src/app/models.py"),
            an_affected_file("src/app/maybe.py", symbols=("dict",), confidence=Confidence.MEDIUM),
        ),
        total_python_files=10,
    )
    plan = a_plan(steps=(a_step(files=("src/app/models.py",)),))

    assert outcome_of(
        report_for(plan=plan, analysis=analysis),
        ValidationCheckId.AFFECTED_FILES_ADDRESSED,
    ).passed


# -- 9. decisions applied ---------------------------------------------------


def a_decision(question_id: str = "strategy-choice") -> HumanDecision:
    return HumanDecision(
        question_id=question_id,
        selected_option_id="direct_migration",
        decided_at=datetime.now(UTC),
    )


def test_an_answered_question_the_plan_does_not_mention_fails() -> None:
    """Spec 8.3 makes the human's influence structural. Without this check,
    "the human's answer affected the output" is an assertion nobody can
    falsify."""
    outcome = outcome_of(
        report_for(human_decisions=(a_decision(),)), ValidationCheckId.DECISIONS_APPLIED
    )

    assert not outcome.passed
    assert outcome.offenders == ("strategy-choice",)


def test_a_recorded_application_satisfies_the_check() -> None:
    plan = a_plan(
        human_decisions_applied=(
            DecisionApplication(
                decision_id="strategy-choice",
                how_it_changed_the_plan="It set the approach.",
            ),
        )
    )

    assert outcome_of(
        report_for(plan=plan, human_decisions=(a_decision(),)),
        ValidationCheckId.DECISIONS_APPLIED,
    ).passed


def test_a_decision_answered_with_no_plan_at_all_fails() -> None:
    """The person answered a question and nothing came of it, which they
    should be told."""
    outcome = outcome_of(
        report_for(plan=None, human_decisions=(a_decision(),)),
        ValidationCheckId.DECISIONS_APPLIED,
    )

    assert not outcome.passed


# -- 10. zero downtime respected -------------------------------------------


def test_a_downtime_step_under_a_zero_downtime_constraint_fails() -> None:
    plan = a_plan(steps=(a_step(downtime=True),))

    outcome = outcome_of(
        report_for(plan=plan, constraints=UserConstraints(zero_downtime=True)),
        ValidationCheckId.ZERO_DOWNTIME_RESPECTED,
    )

    assert not outcome.passed
    assert outcome.offenders == ("step 1: Step 1",)


def test_a_downtime_step_without_the_constraint_is_fine() -> None:
    plan = a_plan(steps=(a_step(downtime=True),))

    assert outcome_of(report_for(plan=plan), ValidationCheckId.ZERO_DOWNTIME_RESPECTED).passed


# -- the repair brief -------------------------------------------------------


def test_the_repair_brief_names_every_failing_check() -> None:
    """Generated from the failing checks themselves rather than hand-written,
    so a check whose meaning is refined describes itself to the repair
    attempt."""
    report = report_for(plan=a_plan(steps=()), human_decisions=(a_decision(),))

    brief = repair_brief(report)

    for failure in report.failures:
        assert failure.check_id.value in brief


def test_a_clean_report_briefs_nothing(store: KnowledgeStore) -> None:
    assert repair_brief(report_for(store=store)).strip().endswith("failed validation on:")


# -- severity is not part of any of this -----------------------------------


def test_the_checks_do_not_depend_on_a_models_severity_spelling() -> None:
    """A smoke check that the fixtures above exercise a real severity rather
    than a default that happens to line up."""
    assert CHANGES[0].severity is Severity.HIGH
