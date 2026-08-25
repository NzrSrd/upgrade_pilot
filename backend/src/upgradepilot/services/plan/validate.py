"""Spec 8.4's ten checks. No LLM, and a real gate.

Every check here answers a question the reader would otherwise have to answer
by hand: does this citation resolve, does this file exist, does the plan
actually cover what the analysis found. The value is entirely in the failing
cases, which is why each check names its offenders -- a failure the reader
cannot locate is a failure they cannot fix.

**What "exists" means here, and why it is not the filesystem.** Spec 8.4
phrases checks 2 and 3 as "the file exists in the workspace". The workspace is
gone by then: `analyze_repo` opens and closes it inside its own node, because
a run pauses at `human_review` and may be resumed days later by a different
process, and a remote clone re-opened on resume is a different checkout of a
branch that may have moved. So both checks resolve against
`RepoAnalysis.citable_paths()` / `.citable_lines()` instead.

That is a strengthening, not a compromise. "Exists on disk" would accept any
path in the repository, including one no part of this analysis ever read; the
analysis record is the set of paths this system is entitled to name, so a
citation outside it is one nothing here produced. The one check that does
reach a live store is the first: a `SourceRef` has to resolve in Chroma,
because Chroma is still there and a citation to a chunk that has since been
re-ingested away is exactly the failure that check exists for.
"""

from collections.abc import Sequence

from upgradepilot.models.decision import HumanDecision
from upgradepilot.models.enums import ValidationCheckId
from upgradepilot.models.evidence import (
    BreakingChange,
    DocEvidence,
    EvidenceRef,
    RepoEvidence,
)
from upgradepilot.models.inputs import UserConstraints
from upgradepilot.models.plan import (
    MigrationPlan,
    ValidationOutcome,
    ValidationReport,
)
from upgradepilot.models.repo import RepoAnalysis
from upgradepilot.models.risk import RISK_ORDER, RiskAnalysis
from upgradepilot.services.knowledge.store import KnowledgeStore
from upgradepilot.services.risk.aggregate import BASE_CONFIDENCE, clamp_floor


def _outcome(
    check: ValidationCheckId, offenders: Sequence[str], *, ok_detail: str, bad_detail: str
) -> ValidationOutcome:
    """One outcome, phrased for whichever way it went.

    Two detail strings rather than one, because a passing check and a failing
    check are read for different things: the first is scanned, the second is
    acted on. "0 unresolvable citations" is a sentence nobody needs.
    """
    if offenders:
        return ValidationOutcome(
            check_id=check,
            passed=False,
            detail=bad_detail,
            offenders=tuple(sorted(set(offenders))),
        )
    return ValidationOutcome(check_id=check, passed=True, detail=ok_detail)


def _all_evidence(
    risk: RiskAnalysis | None, plan: MigrationPlan | None
) -> list[tuple[str, EvidenceRef]]:
    """Every evidence ref anywhere in the output, tagged with where it lives.

    Tagged, because an offender that says only `src/app/models.py:9` leaves
    the reader hunting for which of forty citations that was.
    """
    found: list[tuple[str, EvidenceRef]] = []
    if risk is not None:
        for factor in risk.factors:
            found.extend((f"risk factor {factor.id!r}", ref) for ref in factor.evidence)
    if plan is not None:
        for step in plan.steps:
            found.extend((f"plan step {step.order}", ref) for ref in step.rationale_evidence)
    return found


def validate_plan(
    *,
    attempt: int,
    plan: MigrationPlan | None,
    analysis: RepoAnalysis | None,
    risk: RiskAnalysis | None,
    breaking_changes: Sequence[BreakingChange],
    human_decisions: Sequence[HumanDecision],
    constraints: UserConstraints,
    store: KnowledgeStore | None,
) -> ValidationReport:
    """Run all ten checks and report every one of them.

    **Every check is always reported**, passing or failing, and that is a
    deliberate cost: a report listing only failures is indistinguishable from
    a report where the checks did not run. The UI renders ten rows and the
    reader can see that ten things were looked at.

    `store=None` is a real state, not a test convenience: the knowledge base
    may be unreachable, and spec 7.3 already degrades the run rather than
    failing it. Check 1 then reports that it could not resolve anything --
    truthfully, as a *failure*, because an unverifiable citation is exactly
    what this check exists to catch and "we could not check" must not read as
    "we checked and it was fine".
    """
    outcomes: list[ValidationOutcome] = [
        _check_sources_resolve(plan, risk, breaking_changes, store),
        _check_repo_evidence_resolves(plan, risk, analysis),
        _check_step_files_exist(plan, analysis),
        _check_risk_factors_cite_evidence(risk),
        _check_risk_clamp_holds(risk, analysis, breaking_changes),
        _check_confidence_ceilings_hold(risk),
        _check_plan_is_ordered(plan),
        _check_affected_files_addressed(plan, analysis),
        _check_decisions_applied(plan, human_decisions),
        _check_zero_downtime_respected(plan, constraints),
    ]
    return ValidationReport(attempt=attempt, outcomes=tuple(outcomes))


# -- 1 ----------------------------------------------------------------------


def _check_sources_resolve(
    plan: MigrationPlan | None,
    risk: RiskAnalysis | None,
    breaking_changes: Sequence[BreakingChange],
    store: KnowledgeStore | None,
) -> ValidationOutcome:
    """Every corpus citation still resolves to a chunk in the store.

    The failure this catches is quiet and real: a corpus re-ingest rewrites a
    document from three chunks to one, and a report generated before it now
    cites `#chunk-2`, which resolves to nothing. The citation still *looks*
    right, and the reader following it finds a real document with no such
    passage.
    """
    cited: set[str] = {change.source.chunk_id for change in breaking_changes}
    for _, ref in _all_evidence(risk, plan):
        if isinstance(ref, DocEvidence):
            cited.add(ref.chunk_id)

    if not cited:
        return _outcome(
            ValidationCheckId.SOURCES_RESOLVE,
            (),
            ok_detail="No corpus citation was made, so there is nothing to resolve.",
            bad_detail="",
        )
    if store is None:
        return _outcome(
            ValidationCheckId.SOURCES_RESOLVE,
            sorted(cited),
            ok_detail="",
            bad_detail=(
                f"The knowledge base was unreachable, so none of the {len(cited)} corpus "
                "citation(s) in this report could be verified. An unverified citation is "
                "reported as unverified, never as sound."
            ),
        )

    known = store.chunk_ids(sorted(cited))
    missing = sorted(cited - known)
    return _outcome(
        ValidationCheckId.SOURCES_RESOLVE,
        missing,
        ok_detail=f"All {len(cited)} corpus citation(s) resolve in the knowledge base.",
        bad_detail=(
            f"{len(missing)} corpus citation(s) name a chunk the knowledge base does not "
            "have; following them would land on a document that no longer contains the "
            "quoted passage."
        ),
    )


# -- 2 ----------------------------------------------------------------------


def _check_repo_evidence_resolves(
    plan: MigrationPlan | None,
    risk: RiskAnalysis | None,
    analysis: RepoAnalysis | None,
) -> ValidationOutcome:
    """Every `file:line` citation names a location this analysis actually read."""
    if analysis is None:
        return _outcome(
            ValidationCheckId.REPO_EVIDENCE_RESOLVES,
            (),
            ok_detail="No repository analysis was produced, so no file citation was made.",
            bad_detail="",
        )

    citable = analysis.citable_lines()
    offenders = [
        f"{where}: {ref.file}:{ref.line}"
        for where, ref in _all_evidence(risk, plan)
        if isinstance(ref, RepoEvidence) and (ref.file, ref.line) not in citable
    ]
    return _outcome(
        ValidationCheckId.REPO_EVIDENCE_RESOLVES,
        offenders,
        ok_detail="Every file-and-line citation names a location this analysis read.",
        bad_detail=(
            f"{len(offenders)} citation(s) name a file and line this analysis never read, "
            "so nothing in this run can vouch for what is there."
        ),
    )


# -- 3 ----------------------------------------------------------------------


def _check_step_files_exist(
    plan: MigrationPlan | None, analysis: RepoAnalysis | None
) -> ValidationOutcome:
    """Every file a step tells someone to edit is one that exists."""
    if plan is None or analysis is None:
        return _outcome(
            ValidationCheckId.STEP_FILES_EXIST,
            (),
            ok_detail="No plan was generated, so no step names a file.",
            bad_detail="",
        )

    citable = analysis.citable_paths()
    offenders = [
        f"step {step.order}: {path}"
        for step in plan.steps
        for path in step.files
        if path not in citable
    ]
    return _outcome(
        ValidationCheckId.STEP_FILES_EXIST,
        offenders,
        ok_detail="Every file named by a step exists in the analysed repository.",
        bad_detail=(
            f"{len(offenders)} step file(s) name a path this repository does not have; "
            "following the plan would mean editing a file that is not there."
        ),
    )


# -- 4 ----------------------------------------------------------------------


def _check_risk_factors_cite_evidence(risk: RiskAnalysis | None) -> ValidationOutcome:
    """Every risk factor carries at least one resolving evidence ref.

    `RiskFactor.evidence` is already `min_length=1`, so this can only fail on
    a model that came back from a checkpoint as a plain dict -- which is the
    exact degradation `graph/checkpointer.py` exists to prevent, and the
    reason to check it here rather than trust the type: a resumed run is where
    the constraint would have been lost, and this runs on the resumed run.
    """
    if risk is None:
        return _outcome(
            ValidationCheckId.RISK_FACTORS_CITE_EVIDENCE,
            (),
            ok_detail="No risk analysis was produced, so there is no factor to check.",
            bad_detail="",
        )

    offenders = [factor.id for factor in risk.factors if not factor.evidence]
    return _outcome(
        ValidationCheckId.RISK_FACTORS_CITE_EVIDENCE,
        offenders,
        ok_detail=f"All {len(risk.factors)} risk factor(s) cite evidence.",
        bad_detail=f"{len(offenders)} risk factor(s) cite nothing at all.",
    )


# -- 5 ----------------------------------------------------------------------


def _check_risk_clamp_holds(
    risk: RiskAnalysis | None,
    analysis: RepoAnalysis | None,
    breaking_changes: Sequence[BreakingChange],
) -> ValidationOutcome:
    """`overall_risk` is at least the worst confirmed documented severity.

    Recomputed from the evidence rather than read from `risk.clamp_floor`.
    Reading the stored floor would only check that the object agrees with
    itself, which its own validator already guarantees; recomputing checks
    that the floor it stored is the floor the evidence supports.
    """
    if risk is None or analysis is None:
        return _outcome(
            ValidationCheckId.RISK_CLAMP_HOLDS,
            (),
            ok_detail="No risk analysis was produced, so there is no clamp to check.",
            bad_detail="",
        )

    floor = clamp_floor(analysis, breaking_changes)
    if floor is None:
        return _outcome(
            ValidationCheckId.RISK_CLAMP_HOLDS,
            (),
            ok_detail=(
                "No documented breaking change affects a symbol this repository "
                "certainly uses, so no floor applies."
            ),
            bad_detail="",
        )

    holds = RISK_ORDER[risk.overall_risk] >= RISK_ORDER[floor]
    return _outcome(
        ValidationCheckId.RISK_CLAMP_HOLDS,
        () if holds else [f"overall_risk={risk.overall_risk.value} < floor={floor.value}"],
        ok_detail=(
            f"Overall risk ({risk.overall_risk.value}) is at least the worst confirmed "
            f"documented severity ({floor.value})."
        ),
        bad_detail=(
            f"Overall risk is reported as {risk.overall_risk.value} while a documented "
            f"{floor.value}-severity change affects a symbol this repository certainly "
            "uses."
        ),
    )


# -- 6 ----------------------------------------------------------------------


def _check_confidence_ceilings_hold(risk: RiskAnalysis | None) -> ValidationOutcome:
    """`confidence` respects every ceiling recorded beside it, and the base.

    The base is checked too. `RiskAnalysis`'s own validator bounds confidence
    by the recorded ceilings but knows nothing about `BASE_CONFIDENCE`, so a
    verdict of 1.0 with no ceilings would construct cleanly -- and claim a
    completeness a method that never executes the code cannot have.
    """
    if risk is None:
        return _outcome(
            ValidationCheckId.CONFIDENCE_CEILINGS_HOLD,
            (),
            ok_detail="No risk analysis was produced, so there is no confidence to bound.",
            bad_detail="",
        )

    offenders = [
        f"{ceiling.ceiling} exceeded by {risk.confidence}"
        for ceiling in risk.confidence_ceilings
        if risk.confidence > ceiling.ceiling
    ]
    if risk.confidence > BASE_CONFIDENCE:
        offenders.append(f"base {BASE_CONFIDENCE} exceeded by {risk.confidence}")

    return _outcome(
        ValidationCheckId.CONFIDENCE_CEILINGS_HOLD,
        offenders,
        ok_detail=(
            f"Confidence ({risk.confidence:.2f}) respects the base and all "
            f"{len(risk.confidence_ceilings)} applicable ceiling(s)."
        ),
        bad_detail="Confidence is reported above a limit that applies to it.",
    )


# -- 7 ----------------------------------------------------------------------


def _check_plan_is_ordered(plan: MigrationPlan | None) -> ValidationOutcome:
    """The plan is non-empty and its steps are numbered 1..n."""
    if plan is None:
        return _outcome(
            ValidationCheckId.PLAN_IS_ORDERED,
            ["no plan was generated"],
            ok_detail="",
            bad_detail="No migration plan was generated, so there is nothing to follow.",
        )

    orders = [step.order for step in plan.steps]
    expected = list(range(1, len(orders) + 1))
    offenders: list[str] = []
    if not plan.steps:
        offenders.append("the plan has no steps")
    elif orders != expected:
        offenders.append(f"step order {orders} is not {expected}")

    return _outcome(
        ValidationCheckId.PLAN_IS_ORDERED,
        offenders,
        ok_detail=f"The plan has {len(plan.steps)} step(s), numbered 1 to {len(plan.steps)}.",
        bad_detail="The plan's steps cannot be followed in order.",
    )


# -- 8 ----------------------------------------------------------------------


def _check_affected_files_addressed(
    plan: MigrationPlan | None,
    analysis: RepoAnalysis | None,
) -> ValidationOutcome:
    """Every high-confidence affected file is changed by a step, or explained.

    "Or explained" is what stops this being a demand that the plan cover
    everything: a file whose symbols nothing documents genuinely cannot be
    planned for, and saying so is the honest output. What it refuses is
    *silence* -- a file that is neither addressed nor mentioned, which is how
    a partial plan reads as a complete one.
    """
    if plan is None or analysis is None:
        return _outcome(
            ValidationCheckId.AFFECTED_FILES_ADDRESSED,
            (),
            ok_detail="No plan was generated, so there is nothing to compare against.",
            bad_detail="",
        )

    high_confidence = set(analysis.symbol_inventory.high_confidence_symbols())
    must_cover = {
        file.path
        for file in analysis.affected_files
        if not file.is_test and high_confidence.intersection(file.symbols)
    }
    covered = plan.addressed_paths() | {entry.path for entry in plan.unaddressed_with_reason}
    offenders = sorted(must_cover - covered)
    return _outcome(
        ValidationCheckId.AFFECTED_FILES_ADDRESSED,
        offenders,
        ok_detail=(
            f"All {len(must_cover)} file(s) with high-confidence usage are either "
            "addressed by a step or listed with a reason."
        ),
        bad_detail=(
            f"{len(offenders)} file(s) with high-confidence usage are neither addressed "
            "by a step nor explained, so the plan reads as complete while leaving them "
            "out."
        ),
    )


# -- 9 ----------------------------------------------------------------------


def _check_decisions_applied(
    plan: MigrationPlan | None, human_decisions: Sequence[HumanDecision]
) -> ValidationOutcome:
    """A decision that was made must be recorded as having changed the plan.

    Spec 8.3 makes the human's influence structural: a plan carrying a
    decision has to say what the decision *did*. Without this check, "the
    human's answer affected the output" is an assertion nobody can falsify.
    """
    if plan is None:
        # A decision that was answered and then produced no plan is a failure
        # of this check rather than a silence: the person answered a question
        # and nothing came of it, which they should be told.
        return _outcome(
            ValidationCheckId.DECISIONS_APPLIED,
            [decision.question_id for decision in human_decisions],
            ok_detail="No decision was taken and no plan was generated.",
            bad_detail=(
                f"{len(human_decisions)} decision(s) were answered but no plan was "
                "produced to apply them to."
            ),
        )

    applied = {entry.decision_id for entry in plan.human_decisions_applied}
    offenders = [
        decision.question_id for decision in human_decisions if decision.question_id not in applied
    ]
    return _outcome(
        ValidationCheckId.DECISIONS_APPLIED,
        offenders,
        ok_detail=(
            f"All {len(human_decisions)} decision(s) are recorded with what they changed."
            if human_decisions
            else "No decision was taken, so there is nothing to apply."
        ),
        bad_detail=(
            f"{len(offenders)} decision(s) were answered but the plan does not say what "
            "they changed."
        ),
    )


# -- 10 ---------------------------------------------------------------------


def _check_zero_downtime_respected(
    plan: MigrationPlan | None, constraints: UserConstraints
) -> ValidationOutcome:
    """A zero-downtime constraint forbids a step that needs a cutover."""
    if plan is None or not constraints.zero_downtime:
        return _outcome(
            ValidationCheckId.ZERO_DOWNTIME_RESPECTED,
            (),
            ok_detail=(
                "No zero-downtime constraint was stated."
                if not constraints.zero_downtime
                else "No plan was generated, so no step can require downtime."
            ),
            bad_detail="",
        )

    offenders = [
        f"step {step.order}: {step.title}" for step in plan.steps if step.requires_downtime
    ]
    return _outcome(
        ValidationCheckId.ZERO_DOWNTIME_RESPECTED,
        offenders,
        ok_detail="No step requires downtime, as the stated constraint requires.",
        bad_detail=(
            f"{len(offenders)} step(s) require a coordinated cutover, which the stated "
            "zero-downtime constraint forbids."
        ),
    )


def repair_brief(report: ValidationReport) -> str:
    """The failures, phrased as instructions for the one retry spec 8.4 allows.

    Kept beside the checks rather than in the node, so that a check added
    above is described to the repair attempt by the same code that decided it
    failed -- a second, hand-written description of what went wrong is one
    that goes stale the first time a check's meaning is refined.
    """
    lines = ["The previous plan failed validation on:"]
    for outcome in report.failures:
        lines.append(f"- {outcome.check_id.value}: {outcome.detail}")
        lines.extend(f"    {offender}" for offender in outcome.offenders[:5])
    return "\n".join(lines)
