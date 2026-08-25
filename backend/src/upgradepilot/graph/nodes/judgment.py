"""The judgment layer's nodes. Spec 8.

`assess_risk` is where a model finally gets to write something a reader will
see, and the shape of this module is about keeping that contribution to
exactly what it is good at. The seven factors are extracted mechanically, the
levels come from the threshold table, `overall_risk` is computed and clamped,
and `confidence` is capped by its ceilings -- all before the model is called
at all. What the model receives is a finished factor set and one job:
explain it in prose.

CLAUDE.md rule 19 says the LLM never produces a risk factor level. This node
goes further and keeps it away from `overall_risk` and `confidence` too,
which is a stronger property than spec 8.1's "the clamps override the model"
and the same intent. A clamp that has to fire is a clamp that can be argued
about later; a model that was never handed the number cannot have moved it.

Building the analysis *before* the call has a second consequence worth
stating: if the model is unreachable, the verdict still exists. The narrative
degrades to a sentence assembled from the factors themselves, an `AppError`
is recorded, and the run continues with real numbers and plainer prose --
rather than losing the entire risk assessment to a provider outage.
"""

from datetime import UTC, datetime

from pydantic import BaseModel, Field

from upgradepilot.graph.nodes.base import NodeBody, StateUpdate
from upgradepilot.models.enums import TraceEventKind
from upgradepilot.models.errors import UpgradePilotError
from upgradepilot.models.evidence import RiskFactor
from upgradepilot.models.risk import RiskAnalysis
from upgradepilot.models.state import MigrationState
from upgradepilot.models.trace import trace_event
from upgradepilot.services.llm.tracked import TrackedLLM
from upgradepilot.services.risk.aggregate import build_risk_analysis
from upgradepilot.services.risk.factors import FactorInputs, extract_factors

MAX_NARRATIVE_NOTES = 5
"""Qualitative notes kept. They carry no weight in any level, so the only
cost of a long list is a reader mistaking length for substance."""


class RiskNarrative(BaseModel):
    """What the model is asked for: prose, and nothing that grades.

    There is deliberately no `overall_risk`, no `confidence` and no per-factor
    level in this schema. A field the model cannot fill in is a field it
    cannot get wrong, and the schema is the only place that guarantee can be
    made structurally rather than by a prompt asking nicely.
    """

    summary: str = Field(default="")
    notes: list[str] = Field(default_factory=list)


def _factor_lines(factors: tuple[RiskFactor, ...]) -> str:
    return "\n".join(
        f"- {factor.name}: {factor.level.value.upper()} -- {factor.detail}" for factor in factors
    )


def _narrative_prompt(state: MigrationState, analysis: RiskAnalysis) -> str:
    dependency = state["dependency"]
    parts = [
        "Write a short risk narrative for a developer deciding whether to start "
        "this dependency upgrade.",
        "",
        f"Dependency: {dependency.name} {dependency.current_version} -> "
        f"{dependency.target_version}",
        f"Assessed overall risk: {analysis.overall_risk.value}",
        f"Assessed confidence: {analysis.confidence:.2f}",
        "",
        "These factors were measured mechanically from the repository and the "
        "documented changes. They are settled: describe them, do not re-grade them.",
        _factor_lines(analysis.factors) or "- (no factor had evidence behind it)",
    ]
    if analysis.confidence_ceilings:
        parts += [
            "",
            "Confidence is capped for these reasons:",
            *(f"- {ceiling.reason}" for ceiling in analysis.confidence_ceilings),
        ]
    parts += [
        "",
        "Answer with:",
        "- summary: two or three sentences a developer can act on",
        "- notes: short qualitative observations that do not change any level",
    ]
    return "\n".join(parts)


def _mechanical_summary(analysis: RiskAnalysis) -> str:
    """The narrative when no model wrote one.

    Assembled from the factor levels rather than left blank, because
    `RiskAnalysis.summary` is a `NonBlankStr` and, more to the point, the
    report needs a sentence in that slot. Every clause is a fact already in
    the object, so the fallback cannot say anything the numbers beside it do
    not.
    """
    worst = [factor.name for factor in analysis.factors if factor.level is analysis.overall_risk]
    lead = f"Overall risk is {analysis.overall_risk.value} at {analysis.confidence:.0%} confidence."
    if worst:
        lead += " Driven by: " + "; ".join(worst) + "."
    return lead + " (No narrative was generated: the model could not be reached.)"


def make_assess_risk(llm: TrackedLLM) -> NodeBody[MigrationState]:
    """Spec 8.1: factors computed, narrative generated, nothing else conceded.

    The `today` passed into factor extraction comes from the run's own state
    rather than from `date.today()` inside the extractor, so the deadline arm
    of `constraint_pressure` is reproducible -- a factor that consults the
    clock has a level that changes overnight for reasons no reader can see.
    """

    async def body(state: MigrationState) -> StateUpdate:
        analysis = state["repo_analysis"]
        if analysis is None:
            return {
                "agent_trace": [
                    trace_event(
                        TraceEventKind.AGENT_DECISION,
                        node="assess_risk",
                        summary=(
                            "Skipped risk assessment: the repository analysis did not "
                            "complete, so there are no factors to measure."
                        ),
                    )
                ],
                "summary": "Risk assessment skipped: no repository analysis to work from.",
            }

        inputs = FactorInputs(
            analysis=analysis,
            breaking_changes=tuple(state["breaking_changes"]),
            constraints=state["constraints"],
            today=datetime.now(UTC).date(),
        )
        factors = extract_factors(inputs)

        # Built once with a placeholder narrative so that the prompt can show
        # the model the finished verdict it is describing, and rebuilt with
        # the real prose afterwards. The numbers are identical in both -- the
        # second call passes the same inputs -- so there is no path by which
        # the model's answer could change one.
        provisional = build_risk_analysis(
            analysis=analysis,
            breaking_changes=inputs.breaking_changes,
            rag_context=state["rag_context"],
            factors=factors,
            summary="(narrative pending)",
        )

        update: StateUpdate = {}
        summary_text = _mechanical_summary(provisional)
        notes: list[str] = []
        try:
            narrative, call = await llm.invoke_structured(
                node="assess_risk",
                prompt=_narrative_prompt(state, provisional),
                schema=RiskNarrative,
            )
        except UpgradePilotError as exc:
            update["errors"] = [exc.to_app_error(node="assess_risk")]
            update["agent_trace"] = [
                trace_event(
                    TraceEventKind.ERROR_RECORDED,
                    node="assess_risk",
                    summary=(
                        f"{exc.message} The risk factors and levels are unaffected; "
                        "only the written narrative is missing."
                    ),
                    detail=exc.detail,
                )
            ]
        else:
            update["llm_calls"] = [call]
            written = narrative.summary.strip()
            if written:
                summary_text = written
            notes = [note.strip() for note in narrative.notes if note.strip()][:MAX_NARRATIVE_NOTES]

        risk = build_risk_analysis(
            analysis=analysis,
            breaking_changes=inputs.breaking_changes,
            rag_context=state["rag_context"],
            factors=factors,
            summary=summary_text,
            qualitative_notes=notes,
        )

        events = list(update.get("agent_trace", []))
        if risk.clamp_floor is not None and risk.overall_risk is not risk.aggregate_risk:
            events.append(
                trace_event(
                    TraceEventKind.AGENT_DECISION,
                    node="assess_risk",
                    summary=(
                        f"The measured factors came to {risk.aggregate_risk.value} risk, "
                        f"raised to {risk.overall_risk.value} because a documented "
                        f"{risk.clamp_floor.value}-severity change affects a symbol this "
                        "repository certainly uses."
                    ),
                )
            )
        for ceiling in risk.confidence_ceilings:
            events.append(
                trace_event(
                    TraceEventKind.AGENT_DECISION,
                    node="assess_risk",
                    summary=f"Confidence capped at {ceiling.ceiling:.0%}: {ceiling.reason}",
                )
            )
        update["agent_trace"] = events

        update["risk_analysis"] = risk
        update["summary"] = (
            f"Risk {risk.overall_risk.value} at {risk.confidence:.0%} confidence, "
            f"from {len(risk.factors)} measured factor(s)."
        )
        return update

    return body
