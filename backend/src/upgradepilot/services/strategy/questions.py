"""Building the four kinds of question, each behind a deterministic trigger.

Spec 8.2 fixes the vocabulary: `STRATEGY_CHOICE`, `RISK_ACCEPTANCE`,
`SCOPE_TRADEOFF`, `DISCREPANCY_RESOLUTION`. What this module adds is the
condition under which each one is *worth asking*, expressed as arithmetic over
the evidence rather than as a judgement, so that a run either has a real
question or has none.

Every payload built here is complete on its own -- reason, evidence, options
with consequences, a recommendation and what happens if nobody answers -- for
a reason that only shows up in practice: the person who answers is usually not
the person who started the run, and they arrive at a paused thread with no
memory of it. A question that assumes context is a question that gets answered
by whoever is least equipped to answer it.

The order the questions are raised in is fixed and is not arbitrary. A version
discrepancy comes first, because it changes what is being upgraded at all; the
strategy choice next, because everything after it is scoped by the answer;
then risk acceptance, then scope. Asking them in evidence order instead would
put "do you accept this risk?" before "which migration are we even talking
about?".
"""

from collections.abc import Sequence
from datetime import date

from upgradepilot.models.decision import DecisionOption, InterruptPayload
from upgradepilot.models.enums import (
    DecisionKind,
    DependencyRole,
    EffortLevel,
    RiskLevel,
    Severity,
)
from upgradepilot.models.evidence import (
    BreakingChange,
    ConstraintEvidence,
    DocEvidence,
    EvidenceRef,
    RepoEvidence,
)
from upgradepilot.models.inputs import DependencySpec, UserConstraints
from upgradepilot.models.repo import RepoAnalysis
from upgradepilot.models.risk import RiskAnalysis
from upgradepilot.services.strategy.catalog import (
    DEADLINE_PRESSURE_DAYS,
    Strategy,
    needs_human_choice,
    ranked,
    recommended,
)

THIN_EVIDENCE_CONFIDENCE = 0.5
"""Confidence at or below which a high-risk verdict is "on thin evidence".

Spec 8.2's `RISK_ACCEPTANCE` is for a high-severity finding the system is not
sure about, and `RiskAnalysis.confidence` is exactly that measurement -- it is
already capped by the ceilings that describe what the run could not see. Using
it here means the trigger is the same number the report prints, rather than a
second notion of thinness that could disagree with it.
"""

SCOPE_PRESSURE_FILES = 5
"""Affected files above which a deadline turns into a scope question.

Below this a full migration is a day's work whatever the deadline, and asking
whether to cut scope would be asking a question with an obvious answer.
"""


def _strategy_option(strategy: Strategy, evidence: Sequence[EvidenceRef]) -> DecisionOption:
    return DecisionOption(
        id=strategy.id.value,
        label=strategy.label,
        summary=strategy.summary,
        risk_level=strategy.risk,
        effort=strategy.effort,
        downtime=strategy.downtime,
        consequences=tuple(strategy.consequences),
        supporting_evidence=tuple(evidence),
    )


def _shared_evidence(
    analysis: RepoAnalysis, risk: RiskAnalysis, breaking_changes: Sequence[BreakingChange]
) -> tuple[EvidenceRef, ...]:
    """The evidence every strategy option rests on: what has to change, and why.

    Drawn from the risk factors rather than re-derived, so the options cite
    exactly what the report cites. A second derivation here would be a second
    chance to disagree with the report the reader is holding.
    """
    evidence: list[EvidenceRef] = []
    for change in breaking_changes[:2]:
        evidence.append(
            DocEvidence(
                source_id=change.source.source_id,
                chunk_id=change.source.chunk_id,
                relevance=change.source.relevance,
            )
        )
    for affected in analysis.affected_files[:2]:
        site = affected.usage_sites[0]
        evidence.append(RepoEvidence(file=site.file, line=site.line, snippet=site.snippet))
    if not evidence and risk.factors:
        evidence.extend(risk.factors[0].evidence)
    return tuple(evidence)


def strategy_choice(
    *,
    analysis: RepoAnalysis,
    risk: RiskAnalysis,
    breaking_changes: Sequence[BreakingChange],
    constraints: UserConstraints,
    today: date,
) -> InterruptPayload | None:
    """Spec 8.2's central question, asked only when it is genuinely open.

    Returns `None` when the constraints already settle every axis the viable
    strategies differ on. That negative case is the point of the whole
    mechanism -- see `catalog.needs_human_choice` -- and the caller records it
    as a trace event so a run that did not stop can still say why.
    """
    if not needs_human_choice(constraints, today=today):
        return None

    evidence = _shared_evidence(analysis, risk, breaking_changes)
    if not evidence:
        # No evidence means no options can be constructed -- `DecisionOption`
        # requires at least one supporting ref. A question with unsupported
        # options is exactly what this system must not ask, so it asks
        # nothing and the deterministic recommendation stands.
        return None

    options = tuple(
        _strategy_option(strategy, evidence) for strategy in ranked(constraints, today=today)
    )
    best = recommended(constraints, today=today)
    return InterruptPayload(
        question_id="strategy-choice",
        kind=DecisionKind.STRATEGY_CHOICE,
        reason=(
            f"{len(analysis.affected_files)} file(s) use this dependency and "
            f"{len(breaking_changes)} documented breaking change(s) apply to them. "
            "More than one migration approach remains open, and the constraints "
            "given do not decide between them."
        ),
        question="How should this migration be carried out?",
        evidence=evidence,
        options=options,
        recommendation_id=best.id.value,
        consequences_if_unanswered=(
            "The run stops here. No migration plan is generated until an approach is chosen."
        ),
    )


def risk_acceptance(
    *,
    risk: RiskAnalysis,
    breaking_changes: Sequence[BreakingChange],
) -> InterruptPayload | None:
    """A high-severity finding the run is not confident about.

    The trigger is both halves together. A high-risk verdict at high
    confidence is not a question -- it is a finding, and asking whether to
    accept it would be asking someone to overrule evidence the system is sure
    of. A low-risk verdict at low confidence is not this question either; it
    is a coverage problem, and it is already reported as one. What needs a
    human is the combination: something serious, on evidence the run itself
    says is thin.
    """
    if risk.overall_risk is not RiskLevel.HIGH:
        return None
    if risk.confidence > THIN_EVIDENCE_CONFIDENCE:
        return None

    severe = [change for change in breaking_changes if change.severity is Severity.HIGH]
    evidence: list[EvidenceRef] = [
        DocEvidence(
            source_id=change.source.source_id,
            chunk_id=change.source.chunk_id,
            relevance=change.source.relevance,
        )
        for change in severe[:3]
    ]
    for factor in risk.factors:
        if not evidence:
            evidence.extend(factor.evidence)
    if not evidence:
        return None

    caps = "; ".join(ceiling.reason for ceiling in risk.confidence_ceilings)
    return InterruptPayload(
        question_id="risk-acceptance",
        kind=DecisionKind.RISK_ACCEPTANCE,
        reason=(
            f"This upgrade is assessed high risk, but only at {risk.confidence:.0%} "
            f"confidence. {caps}"
        ),
        question="Proceed with mitigation, or stop until the gaps are closed?",
        evidence=tuple(evidence),
        options=(
            DecisionOption(
                id="proceed-with-mitigation",
                label="Proceed, with mitigation steps",
                summary=(
                    "Generate a plan that carries explicit mitigation for the parts "
                    "this analysis could not see."
                ),
                risk_level=RiskLevel.HIGH,
                effort=EffortLevel.MEDIUM,
                downtime=False,
                consequences=(
                    "The plan proceeds over evidence this run has already flagged as incomplete.",
                    "Mitigation steps are added for each gap, which cost work that a "
                    "complete analysis would not have needed.",
                ),
                supporting_evidence=tuple(evidence),
            ),
            DecisionOption(
                id="block-until-evidence",
                label="Stop until the gaps are closed",
                summary=(
                    "Treat the missing evidence as blocking and report what would "
                    "need to be established first."
                ),
                risk_level=RiskLevel.LOW,
                effort=EffortLevel.LOW,
                downtime=False,
                consequences=(
                    "No migration plan is produced in this run.",
                    "The report names exactly which gaps would have to be closed for "
                    "one to be worth generating.",
                ),
                supporting_evidence=tuple(evidence),
            ),
        ),
        recommendation_id="block-until-evidence",
        consequences_if_unanswered=(
            "The run stops here. Neither a plan nor a blocking report is produced "
            "until this is answered."
        ),
    )


def scope_tradeoff(
    *,
    analysis: RepoAnalysis,
    constraints: UserConstraints,
    breaking_changes: Sequence[BreakingChange],
    today: date,
) -> InterruptPayload | None:
    """A deadline that a full migration may not fit inside.

    Triggered only when both a near deadline and enough affected files exist:
    below `SCOPE_PRESSURE_FILES` the whole migration is a day's work whatever
    the deadline, and asking whether to cut scope would be asking a question
    with an obvious answer.
    """
    if constraints.deadline is None:
        return None
    days = (constraints.deadline - today).days
    if days > DEADLINE_PRESSURE_DAYS:
        return None
    affected = [file for file in analysis.affected_files if not file.is_test]
    if len(affected) < SCOPE_PRESSURE_FILES:
        return None

    evidence: list[EvidenceRef] = [
        ConstraintEvidence(field="deadline", value=constraints.deadline.isoformat())
    ]
    for file in affected[:3]:
        site = file.usage_sites[0]
        evidence.append(RepoEvidence(file=site.file, line=site.line, snippet=site.snippet))

    severe = sum(1 for change in breaking_changes if change.severity is Severity.HIGH)
    return InterruptPayload(
        question_id="scope-tradeoff",
        kind=DecisionKind.SCOPE_TRADEOFF,
        reason=(
            f"The stated deadline is {days} day(s) away and {len(affected)} file(s) "
            f"need changing, {severe} of them against high-severity documented "
            "changes."
        ),
        question="Migrate everything, or the high-severity usage first?",
        evidence=tuple(evidence),
        options=(
            DecisionOption(
                id="full-migration",
                label="Migrate everything before the deadline",
                summary="Plan for every affected file, in one piece of work.",
                risk_level=RiskLevel.MEDIUM,
                effort=EffortLevel.HIGH,
                downtime=False,
                consequences=(
                    "Nothing is left on the old API, so there is no second migration to schedule.",
                    "The work may not fit the deadline, and a partly-finished "
                    "whole-codebase change is worse than a finished partial one.",
                ),
                supporting_evidence=tuple(evidence),
            ),
            DecisionOption(
                id="high-severity-first",
                label="High-severity usage first",
                summary=(
                    "Plan only the files whose usage is covered by a high-severity "
                    "documented change, and schedule the rest."
                ),
                risk_level=RiskLevel.MEDIUM,
                effort=EffortLevel.MEDIUM,
                downtime=False,
                consequences=(
                    "The changes most likely to break land first and are fully finished.",
                    "The codebase stays on a mixed API until the remaining work is "
                    "scheduled, so both styles must keep working.",
                ),
                supporting_evidence=tuple(evidence),
            ),
        ),
        recommendation_id="high-severity-first",
        consequences_if_unanswered=(
            "The run stops here, and the deadline continues to approach while it waits."
        ),
    )


def discrepancy_resolution(
    *,
    analysis: RepoAnalysis,
    dependency: DependencySpec,
) -> InterruptPayload | None:
    """The repository and the request disagree about what is installed.

    Two shapes, both spec 8.2's: a detected version that differs from the
    stated one, and a dependency present only transitively. Neither is
    resolved silently in either direction -- preferring the manifest would
    override a user who knows their deployment, and preferring the stated
    version would plan an upgrade from a version this repository does not
    have.
    """
    detected = analysis.detected_version
    if detected is None:
        return None

    manifest_evidence: tuple[EvidenceRef, ...] = (
        RepoEvidence(file=detected.source_manifest.path, line=1),
    )
    discrepancy = analysis.version_discrepancy(dependency.current_version)

    if discrepancy is not None:
        stated, found = discrepancy
        return InterruptPayload(
            question_id="version-discrepancy",
            kind=DecisionKind.DISCREPANCY_RESOLUTION,
            reason=(
                f"The run was started for {dependency.name} {stated}, but "
                f"{detected.source_manifest.path} declares {found} "
                f"({detected.confidence.value} confidence)."
            ),
            question="Which version should the plan treat as the starting point?",
            evidence=manifest_evidence,
            options=(
                DecisionOption(
                    id="use-detected",
                    label=f"Use the declared version ({found})",
                    summary="Plan the upgrade from what this repository's manifest says.",
                    risk_level=RiskLevel.LOW,
                    effort=EffortLevel.LOW,
                    downtime=False,
                    consequences=(
                        f"The plan is written against {found}, which is what a fresh "
                        "install of this repository would get.",
                        "If the deployed environment differs from the manifest, the "
                        "plan will not match it.",
                    ),
                    supporting_evidence=manifest_evidence,
                ),
                DecisionOption(
                    id="use-stated",
                    label=f"Use the stated version ({stated})",
                    summary="Plan the upgrade from the version supplied with the request.",
                    risk_level=RiskLevel.MEDIUM,
                    effort=EffortLevel.LOW,
                    downtime=False,
                    consequences=(
                        f"The plan is written against {stated}, on the assumption that "
                        "the deployed version differs from the manifest.",
                        "The manifest still says something else, so it will need "
                        "updating as part of the work.",
                    ),
                    supporting_evidence=manifest_evidence,
                ),
            ),
            recommendation_id="use-detected",
            consequences_if_unanswered=(
                "The run stops here. No plan is generated while it is unclear which "
                "version is being upgraded from."
            ),
        )

    if detected.role is DependencyRole.TRANSITIVE_ONLY:
        return InterruptPayload(
            question_id="transitive-only",
            kind=DecisionKind.DISCREPANCY_RESOLUTION,
            reason=(
                f"{dependency.name} is used by this repository's code but no manifest "
                "declares it: it arrives as a transitive dependency of something else, "
                "so upgrading it is not wholly under this repository's control."
            ),
            question="Declare it directly, or upgrade the package that brings it in?",
            evidence=manifest_evidence,
            options=(
                DecisionOption(
                    id="declare-directly",
                    label="Declare it as a direct dependency first",
                    summary=("Add an explicit pin, then perform the upgrade against that pin."),
                    risk_level=RiskLevel.LOW,
                    effort=EffortLevel.LOW,
                    downtime=False,
                    consequences=(
                        "This repository controls the version from then on.",
                        "The pin can conflict with what the parent package requires, "
                        "which surfaces at install time.",
                    ),
                    supporting_evidence=manifest_evidence,
                ),
                DecisionOption(
                    id="upgrade-the-parent",
                    label="Upgrade the package that brings it in",
                    summary=(
                        "Leave it transitive and move the parent package to a version "
                        "that requires the target."
                    ),
                    risk_level=RiskLevel.MEDIUM,
                    effort=EffortLevel.MEDIUM,
                    downtime=False,
                    consequences=(
                        "No new pin is added, so nothing new to maintain.",
                        "The parent's own upgrade brings changes of its own, outside "
                        "the scope this run analysed.",
                    ),
                    supporting_evidence=manifest_evidence,
                ),
            ),
            recommendation_id="declare-directly",
            consequences_if_unanswered=(
                "The run stops here. No plan is generated while the dependency is not "
                "one this repository controls."
            ),
        )

    return None


def pending_decisions(
    *,
    analysis: RepoAnalysis,
    risk: RiskAnalysis,
    breaking_changes: Sequence[BreakingChange],
    dependency: DependencySpec,
    constraints: UserConstraints,
    today: date,
) -> tuple[InterruptPayload, ...]:
    """Every question this run has to ask, in the order it should ask them.

    See the module docstring for why the order is fixed. An empty tuple is the
    ordinary outcome for a run whose constraints decide everything, and the
    conditional edge in `graph/build.py` reads it directly: no questions, no
    interrupt, no pause.
    """
    candidates = (
        discrepancy_resolution(analysis=analysis, dependency=dependency),
        strategy_choice(
            analysis=analysis,
            risk=risk,
            breaking_changes=breaking_changes,
            constraints=constraints,
            today=today,
        ),
        risk_acceptance(risk=risk, breaking_changes=breaking_changes),
        scope_tradeoff(
            analysis=analysis,
            constraints=constraints,
            breaking_changes=breaking_changes,
            today=today,
        ),
    )
    return tuple(payload for payload in candidates if payload is not None)
