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

from datetime import UTC, date, datetime

from langgraph.types import interrupt
from pydantic import BaseModel, Field, ValidationError

from upgradepilot.graph.nodes.base import NodeBody, StateUpdate
from upgradepilot.models.decision import (
    HumanDecision,
    InterruptPayload,
    unanswered,
)
from upgradepilot.models.enums import DecisionKind, TraceEventKind
from upgradepilot.models.errors import UpgradePilotError
from upgradepilot.models.evidence import RiskFactor
from upgradepilot.models.risk import RiskAnalysis
from upgradepilot.models.state import MigrationState
from upgradepilot.models.trace import TraceEvent, trace_event
from upgradepilot.services.llm.tracked import TrackedLLM
from upgradepilot.services.risk.aggregate import build_risk_analysis
from upgradepilot.services.risk.factors import FactorInputs, extract_factors
from upgradepilot.services.strategy.catalog import recommended
from upgradepilot.services.strategy.questions import pending_decisions

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


def _decision_events(
    state: MigrationState,
    decisions: tuple[InterruptPayload, ...],
    *,
    today: date,
) -> list[TraceEvent]:
    """Say what was asked -- and, when nothing was, say that too.

    The no-interrupt case is the one that needs an event. A run that sails
    past `human_review` looks identical in a timeline to a run where the
    question never came up, and spec 8.2's whole argument for the conditional
    edge is that the constraints *decided* it. Recording "resolved by
    constraints, no human input required" alongside the strategy that won is
    what turns a silent skip into a visible decision.
    """
    events = [
        trace_event(
            TraceEventKind.DECISION_REQUIRED,
            node="assess_risk",
            summary=f"{payload.kind.value.replace('_', ' ').capitalize()}: {payload.question}",
            detail=payload.reason,
        )
        for payload in decisions
    ]
    if not any(payload.kind is DecisionKind.STRATEGY_CHOICE for payload in decisions):
        constraints = state["constraints"]
        best = recommended(constraints, today=today)
        events.append(
            trace_event(
                TraceEventKind.AGENT_DECISION,
                node="assess_risk",
                summary=(
                    f"Migration approach resolved by the stated constraints, so no "
                    f"human input was required: {best.label.lower()}."
                ),
                detail=best.summary,
            )
        )
    return events


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

        # Spec 8.2 puts strategy enumeration and payload construction HERE,
        # not in `human_review`, and the reason is billing rather than tidiness:
        # a node that calls `interrupt()` re-executes from the top on every
        # resume -- measured at four executions for a two-question node in
        # `probes/probe_interrupt.py` -- so any work placed before its
        # interrupt happens once per resume. Model calls would be billed each
        # time while only one usage record survives, and recorded cost would
        # understate real spend. ADR-001 records the rule; this is where it is
        # obeyed.
        decisions = pending_decisions(
            analysis=analysis,
            risk=risk,
            breaking_changes=inputs.breaking_changes,
            dependency=state["dependency"],
            constraints=state["constraints"],
            today=inputs.today,
        )
        events.extend(_decision_events(state, decisions, today=inputs.today))

        update["risk_analysis"] = risk
        update["pending_decisions"] = list(decisions)
        update["agent_trace"] = events
        update["summary"] = (
            f"Risk {risk.overall_risk.value} at {risk.confidence:.0%} confidence, "
            f"from {len(risk.factors)} measured factor(s)."
            + (f" {len(decisions)} question(s) need a human." if decisions else "")
        )
        return update

    return body


def _as_decision(raw: object, payload: InterruptPayload) -> HumanDecision | str:
    """Validate one resume value, or say in one sentence what is wrong with it.

    `interrupt()` returns whatever the HTTP layer handed it, unvalidated and
    of any shape at all -- spec 8.2 calls it untrusted and it is. Four things
    can be wrong with it and each gets its own message, because the person who
    has to fix it is looking at a form, not a stack trace.

    Returns the string rather than raising, deliberately. A raised error here
    would be caught by `traced`, recorded as a failure and the run would
    continue *past* the question with no answer -- turning "you sent something
    unusable" into "nobody was asked". The string travels back onto the
    payload and the node interrupts again.
    """
    if isinstance(raw, HumanDecision):
        decision = raw
    elif isinstance(raw, str):
        # The convenient shorthand: a bare option id. Accepted because it is
        # unambiguous -- there is exactly one question in flight -- and
        # refusing it would make the API harder to use for nothing.
        decision = HumanDecision(
            question_id=payload.question_id,
            selected_option_id=raw.strip(),
            decided_at=datetime.now(UTC),
        )
    elif isinstance(raw, dict):
        try:
            decision = HumanDecision.model_validate(
                {
                    "question_id": raw.get("question_id", payload.question_id),
                    "selected_option_id": raw.get("selected_option_id", ""),
                    "rationale": raw.get("rationale"),
                    "decided_at": raw.get("decided_at", datetime.now(UTC)),
                }
            )
        except ValidationError as exc:
            return f"That answer could not be read: {exc.error_count()} field(s) are invalid."
    else:
        return (
            f"That answer could not be read: expected an option id or a decision "
            f"object, got {type(raw).__name__}."
        )

    if decision.question_id != payload.question_id:
        return (
            f"That answer is for a different question "
            f"({decision.question_id!r}, not {payload.question_id!r})."
        )
    if payload.option(decision.selected_option_id) is None:
        offered = ", ".join(option.id for option in payload.options)
        return (
            f"{decision.selected_option_id!r} is not one of the options offered. "
            f"Choose one of: {offered}."
        )
    return decision


def make_human_review() -> NodeBody[MigrationState]:
    """Spec 8.2's interrupt node: read, pause, validate, and nothing else.

    **No model is called here, and that is a correctness requirement rather
    than a style choice.** A node containing `interrupt()` re-executes from
    the top on every resume -- measured at four executions for a two-question
    node in `probes/probe_interrupt.py` -- so a model call placed above the
    interrupt is billed once per resume while only one usage record survives.
    Recorded cost would understate real spend, and the shortfall would grow
    with the number of times a person changed their mind. Everything expensive
    -- strategy enumeration, scoring, payload construction -- happens in
    `assess_risk`, once. ADR-001 records the rule.

    Re-execution also explains the shape of the loop below. On resume,
    LangGraph replays each `interrupt()` in order and returns the value it was
    given before, pausing only at the newest one; measured, not assumed. So a
    rejected answer is re-asked by simply calling `interrupt()` again, and the
    loop terminates because each pass consumes one more already-supplied
    resume value.
    """

    async def body(state: MigrationState) -> StateUpdate:
        outstanding = unanswered(state["pending_decisions"], state["human_decisions"])
        if not outstanding:
            return {"summary": "No question was outstanding, so the run did not pause."}

        # **One question per execution, and the router sends the run back here
        # for the next one.** Asking all of them in a single execution looks
        # tidier and is wrong: a node that interrupts produces no state update
        # until it finishes, so the answers to questions one and two would sit
        # in LangGraph's resume store and never reach `human_decisions`.
        # Everything downstream derives from that channel -- the router's
        # "what is still unanswered", and the API's "which question is the
        # user looking at" -- so a partially-answered run would keep showing
        # the question it had already answered. Measured, not reasoned:
        # `human_decisions` came back empty from a two-question run that had
        # answered one.
        payload = outstanding[0]
        asking = payload
        while True:
            raw = interrupt(asking)
            result = _as_decision(raw, payload)
            if isinstance(result, HumanDecision):
                break
            # Re-ask the same question carrying the complaint. The payload is
            # rebuilt rather than mutated because it is frozen, and
            # `validation_error` travels on the payload rather than as a
            # raised error so the person answering sees it instead of a log
            # line. The loop terminates because each pass consumes one more
            # already-supplied resume value -- measured in
            # `probes/probe_interrupt.py`.
            asking = payload.model_copy(update={"validation_error": result})

        chosen = payload.option(result.selected_option_id)
        assert chosen is not None  # narrowed by _as_decision
        remaining = len(outstanding) - 1
        return {
            "human_decisions": [result],
            "agent_trace": [
                trace_event(
                    TraceEventKind.DECISION_APPLIED,
                    node="human_review",
                    summary=f"{payload.question} Answered: {chosen.label}.",
                    detail=result.rationale,
                )
            ],
            "summary": (
                f"Answered {payload.question_id!r} with {chosen.label!r}."
                + (f" {remaining} question(s) still outstanding." if remaining else "")
            ),
        }

    return body
