"""Generating the plan, validating it, and assembling the report.

Spec 8.3 and 8.4. The division of labour between the model and this module is
the same one the risk layer draws, and for the same reason.

**The model writes prose and chooses what to tackle; it never produces a file
path.** CLAUDE.md rule 19. A model asked for a file path produces a plausible
one, and a plausible path in a migration plan is an instruction to edit a file
that does not exist. So the schema asks for the *symbols* and *documented
changes* a step addresses, and the files come from the analysis record by
lookup. The same goes for `requires_downtime`, which is set from the chosen
strategy: check 10 refuses a plan with a downtime step under a zero-downtime
constraint, and a flag the model could set would make that check a
negotiation.

**The strategy comes from the human when there was one.** This is where spec
8.3's decision-flip property lives: `strategy_id` is read from the answered
`STRATEGY_CHOICE` decision, and falls back to the deterministic
recommendation only when nobody was asked. Resuming the same checkpoint with
the opposite option therefore yields a different plan, which is how "the human
decision affects downstream generation" gets *verified* rather than claimed.
"""

from collections.abc import Sequence
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from upgradepilot.graph.nodes.base import NodeBody, StateUpdate
from upgradepilot.models.decision import (
    DecisionApplication,
    HumanDecision,
    InterruptPayload,
)
from upgradepilot.models.enums import (
    DecisionKind,
    StrategyId,
    TraceEventKind,
)
from upgradepilot.models.errors import UpgradePilotError
from upgradepilot.models.evidence import (
    BreakingChange,
    DocEvidence,
    EvidenceRef,
    RepoEvidence,
)
from upgradepilot.models.plan import (
    FinalReport,
    MigrationPlan,
    MigrationStep,
    UnaddressedFile,
)
from upgradepilot.models.repo import RepoAnalysis
from upgradepilot.models.state import MigrationState
from upgradepilot.models.trace import trace_event
from upgradepilot.models.usage import UsageSummary
from upgradepilot.services.knowledge.store import KnowledgeStore
from upgradepilot.services.llm.tracked import TrackedLLM
from upgradepilot.services.plan.validate import repair_brief, validate_plan
from upgradepilot.services.strategy.catalog import CATALOG, recommended

MAX_PLAN_ATTEMPTS = 2
"""One generation and one repair, exactly as spec 8.4 allows.

Bounded rather than "retry until it passes", because a validator that can be
retried indefinitely is a validator the generator learns to satisfy by
attrition. Still failing after the repair is a real outcome the report shows
(`COMPLETED_WITH_WARNINGS`) rather than a loop.
"""

MAX_STEPS = 12
"""Steps kept from a draft. A plan nobody reads to the end is not a plan, and
an unbounded list from a model is unbounded cost in the rendering too."""


class PlannedStep(BaseModel):
    """One step as the model proposes it.

    There is deliberately no `files` field and no `requires_downtime`. A field
    the model cannot fill in is a field it cannot get wrong, and both of those
    are resolved from the analysis and the chosen strategy below.
    """

    title: str = Field(default="")
    description: str = Field(default="")
    symbols: list[str] = Field(default_factory=list)
    change_ids: list[str] = Field(default_factory=list)
    validation: str = Field(default="")


class PlanDraft(BaseModel):
    summary: str = Field(default="")
    steps: list[PlannedStep] = Field(default_factory=list)
    mitigations: list[str] = Field(default_factory=list)


def _strategy_decision(
    pending: Sequence[InterruptPayload], decisions: Sequence[HumanDecision]
) -> HumanDecision | None:
    """The answered strategy question, if one was asked and answered."""
    strategy_questions = {
        payload.question_id for payload in pending if payload.kind is DecisionKind.STRATEGY_CHOICE
    }
    return next(
        (decision for decision in decisions if decision.question_id in strategy_questions),
        None,
    )


def _chosen_strategy(state: MigrationState) -> tuple[StrategyId, DecisionApplication | None]:
    """The strategy this plan is built on, and the record of who chose it.

    Returns the `DecisionApplication` alongside, rather than letting the node
    write one afterwards, because the two must not be able to disagree: a plan
    whose `strategy_id` came from a human and whose
    `human_decisions_applied` is empty is exactly what check 9 refuses, and
    producing both here means there is no code path that sets one without the
    other.
    """
    decision = _strategy_decision(state["pending_decisions"], state["human_decisions"])
    if decision is None:
        return (
            recommended(state["constraints"], today=datetime.now(UTC).date()).id,
            None,
        )

    chosen = StrategyId(decision.selected_option_id)
    label = next(strategy.label for strategy in CATALOG if strategy.id is chosen)
    return chosen, DecisionApplication(
        decision_id=decision.question_id,
        how_it_changed_the_plan=(
            f"The migration approach was chosen by a human as {label.lower()!r}, which "
            "determined the shape and ordering of every step below."
        ),
    )


def _other_decisions_applied(
    state: MigrationState, strategy_decision_id: str | None
) -> list[DecisionApplication]:
    """Record what every *other* answered question changed.

    Generic sentences, deliberately: the specific effect of a risk-acceptance
    or scope answer is carried by the steps and mitigations the model wrote
    around it, and inventing a more specific claim here would be asserting a
    causal link nothing verified. What check 9 requires is that no decision is
    silently ignored, and that is what this provides.
    """
    by_id = {payload.question_id: payload for payload in state["pending_decisions"]}
    applications: list[DecisionApplication] = []
    for decision in state["human_decisions"]:
        if decision.question_id == strategy_decision_id:
            continue
        payload = by_id.get(decision.question_id)
        option = payload.option(decision.selected_option_id) if payload else None
        label = option.label if option else decision.selected_option_id
        applications.append(
            DecisionApplication(
                decision_id=decision.question_id,
                how_it_changed_the_plan=(
                    f"Answered {label!r}, which set the scope and mitigations the steps "
                    "below were written against."
                ),
            )
        )
    return applications


def _files_for(
    analysis: RepoAnalysis,
    symbols: Sequence[str],
    changes: Sequence[BreakingChange],
    change_ids: Sequence[str],
) -> tuple[list[str], list[EvidenceRef]]:
    """Resolve a step's symbols and change ids to real files and citations.

    This is where rule 19 is actually enforced. The model names symbols and
    documented changes -- both of which it was shown, and both of which are
    checkable against a closed set -- and the file paths come from the
    analyzer's own records. A symbol the model invented resolves to no file
    and contributes nothing; it cannot conjure a path.
    """
    wanted = {symbol.strip() for symbol in symbols if symbol.strip()}
    files = sorted(
        {file.path for file in analysis.affected_files if wanted.intersection(file.symbols)}
    )
    evidence: list[EvidenceRef] = []
    by_id = {change.id: change for change in changes}
    for change_id in change_ids:
        change = by_id.get(change_id.strip())
        if change is not None:
            evidence.append(
                DocEvidence(
                    source_id=change.source.source_id,
                    chunk_id=change.source.chunk_id,
                    relevance=change.source.relevance,
                )
            )
    if not files and not evidence:
        return [], []
    if not evidence:
        # A step naming files still needs something to cite when its own
        # `files` list is what justifies it -- the first usage site of the
        # first file is the smallest honest citation, and it is a line the
        # analyzer really read.
        for file in analysis.affected_files:
            if file.path == files[0]:
                site = file.usage_sites[0]
                evidence.append(RepoEvidence(file=site.file, line=site.line, snippet=site.snippet))
                break
    return files, evidence


def _plan_prompt(state: MigrationState, repair: str | None) -> str:
    analysis = state["repo_analysis"]
    assert analysis is not None
    dependency = state["dependency"]
    strategy_id, _ = _chosen_strategy(state)
    strategy = next(entry for entry in CATALOG if entry.id is strategy_id)

    parts = [
        "Write a migration plan a developer can work through.",
        "",
        f"Dependency: {dependency.name} {dependency.current_version} -> "
        f"{dependency.target_version}",
        f"Chosen approach: {strategy.label} -- {strategy.summary}",
        "",
        "Symbols this repository uses, from parsing its source:",
        *(
            f"- {stat.symbol} ({stat.count} use(s) in {len(stat.files)} file(s), "
            f"{stat.confidence.value} confidence)"
            for stat in analysis.symbol_inventory.entries
        ),
        "",
        "Documented breaking changes that apply:",
        *(
            [
                f"- {change.id} [{change.severity.value}] {change.title} "
                f"(symbols: {', '.join(change.affected_symbols)})"
                for change in state["breaking_changes"]
            ]
            or ["- (none were found)"]
        ),
        "",
        "For each step give:",
        "- title and description a developer can act on",
        "- symbols: which of the listed symbols the step deals with (exact "
        "spellings; anything else is discarded)",
        "- change_ids: which of the listed change ids it addresses",
        "- validation: one line on how to tell the step worked",
        "",
        "Do not name files or line numbers: they are filled in from the "
        "analysis, and a path you write would not be checked against anything.",
    ]
    if repair:
        parts += ["", repair, "", "Rewrite the plan so that those checks pass."]
    return "\n".join(parts)


def _unaddressed(
    analysis: RepoAnalysis, steps: Sequence[MigrationStep], state: MigrationState
) -> list[UnaddressedFile]:
    """Name the high-confidence files no step touches, when there is an honest
    reason.

    The reason has to be *true*, which is why only one is produced: a file
    whose symbols nothing in the corpus documents genuinely cannot be planned
    for, and the retrieval context already recorded exactly that set as
    `unknowns`. A file that a documented change *does* cover and that no step
    addresses gets no entry here -- it is a real gap in the plan, check 8
    fails, and the bounded repair gets a chance to fix it. Filling this list
    with a generic sentence would make check 8 vacuous, which is the failure
    mode of every "explain the gap" mechanism.
    """
    context = state["rag_context"]
    unknowns = set(context.unknowns) if context is not None else set()
    if not unknowns:
        return []

    addressed = {path for step in steps for path in step.files}
    high_confidence = set(analysis.symbol_inventory.high_confidence_symbols())
    entries: list[UnaddressedFile] = []
    for file in analysis.affected_files:
        if file.is_test or file.path in addressed:
            continue
        used = set(file.symbols)
        if not used & high_confidence:
            continue
        undocumented = sorted(used & unknowns)
        if undocumented and not (used & high_confidence) - unknowns:
            entries.append(
                UnaddressedFile(
                    path=file.path,
                    reason=(
                        "No step changes this file because nothing in the knowledge base "
                        f"documents a change to the symbol(s) it uses: "
                        f"{', '.join(undocumented)}."
                    ),
                )
            )
    return entries


def make_generate_plan(llm: TrackedLLM) -> NodeBody[MigrationState]:
    """Spec 8.3, including the bounded repair attempt.

    The repair is driven by `repair_brief`, which is generated from the
    failing checks themselves rather than hand-written here -- a second
    description of what went wrong is one that goes stale the first time a
    check's meaning is refined.
    """

    async def body(state: MigrationState) -> StateUpdate:
        analysis = state["repo_analysis"]
        if analysis is None:
            return {
                "agent_trace": [
                    trace_event(
                        TraceEventKind.AGENT_DECISION,
                        node="generate_plan",
                        summary=(
                            "Skipped plan generation: the repository analysis did not "
                            "complete, so there are no files to plan changes to."
                        ),
                    )
                ],
                "summary": "Plan generation skipped: no repository analysis.",
            }

        attempt = state["plan_attempts"] + 1
        previous = state["validation"]
        repair = repair_brief(previous) if previous is not None and attempt > 1 else None

        update: StateUpdate = {"plan_attempts": attempt}
        try:
            draft, call = await llm.invoke_structured(
                node="generate_plan",
                prompt=_plan_prompt(state, repair),
                schema=PlanDraft,
            )
        except UpgradePilotError as exc:
            return {
                **update,
                "errors": [exc.to_app_error(node="generate_plan")],
                "agent_trace": [
                    trace_event(
                        TraceEventKind.ERROR_RECORDED,
                        node="generate_plan",
                        summary=f"{exc.message} No migration plan was produced.",
                        detail=exc.detail,
                    )
                ],
                "summary": "No plan was produced: the model could not be reached.",
            }

        update["llm_calls"] = [call]
        strategy_id, strategy_application = _chosen_strategy(state)
        strategy = next(entry for entry in CATALOG if entry.id is strategy_id)
        changes = state["breaking_changes"]

        steps: list[MigrationStep] = []
        for planned in draft.steps[:MAX_STEPS]:
            title = planned.title.strip()
            description = planned.description.strip()
            if not title or not description:
                continue
            files, evidence = _files_for(analysis, planned.symbols, changes, planned.change_ids)
            if not files and not evidence:
                # Rule 19's other half: a step that resolves to no file and no
                # citation has nothing behind it. `MigrationStep` would refuse
                # it, so dropping it here is the difference between a plan
                # with one fewer step and a node that crashes on a vague
                # answer.
                continue
            steps.append(
                MigrationStep(
                    order=len(steps) + 1,
                    title=title,
                    description=description,
                    files=tuple(files),
                    rationale_evidence=tuple(evidence),
                    validation=planned.validation.strip() or None,
                    requires_downtime=strategy.downtime and len(steps) == 0,
                )
            )

        applications: list[DecisionApplication] = []
        if strategy_application is not None:
            applications.append(strategy_application)
        applications.extend(
            _other_decisions_applied(
                state,
                strategy_application.decision_id if strategy_application else None,
            )
        )

        plan = MigrationPlan(
            strategy_id=strategy_id,
            summary=draft.summary.strip()
            or f"Migrate {state['dependency'].name} using: {strategy.label.lower()}.",
            steps=tuple(steps),
            human_decisions_applied=tuple(applications),
            unaddressed_with_reason=tuple(_unaddressed(analysis, steps, state)),
            mitigations=tuple(note.strip() for note in draft.mitigations if note.strip()),
        )

        update["migration_plan"] = plan
        update["summary"] = (
            f"Attempt {attempt}: {len(plan.steps)} step(s) using {strategy.label.lower()}."
        )
        return update

    return body


def make_validate_plan(store: KnowledgeStore | None) -> NodeBody[MigrationState]:
    """Spec 8.4's ten checks, run over whatever the run has produced.

    The store is reached live because it is still there and a citation to a
    chunk that has since been re-ingested away is exactly the failure check 1
    exists for. Everything else resolves against the analysis record -- see
    `services/plan/validate.py` for why the workspace is gone by now.
    """

    async def body(state: MigrationState) -> StateUpdate:
        report = validate_plan(
            attempt=max(1, state["plan_attempts"]),
            plan=state["migration_plan"],
            analysis=state["repo_analysis"],
            risk=state["risk_analysis"],
            breaking_changes=state["breaking_changes"],
            human_decisions=state["human_decisions"],
            constraints=state["constraints"],
            store=store,
        )

        events = [
            trace_event(
                TraceEventKind.VALIDATION_OUTCOME,
                node="validate_plan",
                summary=(
                    f"{outcome.check_id.value}: "
                    f"{'passed' if outcome.passed else 'FAILED'} -- {outcome.detail}"
                ),
                detail=", ".join(outcome.offenders) or None,
            )
            for outcome in report.outcomes
        ]

        passed = len(report.outcomes) - len(report.failures)
        return {
            "validation": report,
            "agent_trace": events,
            "summary": (
                f"{passed} of {len(report.outcomes)} checks passed on attempt {report.attempt}."
            ),
        }

    return body


def make_finalize() -> NodeBody[MigrationState]:
    """Assemble the report. A pure function over state -- no model, no I/O.

    Pure matters here for a specific reason: the API may build this from a
    checkpoint long after the run ended, and a `finalize` that read anything
    outside state would produce a different report the second time. Every
    field below comes from a channel, and `UsageSummary` is derived from
    `llm_calls` rather than read from a stored total (spec 6.1).
    """

    async def body(state: MigrationState) -> StateUpdate:
        analysis = state["repo_analysis"]
        report = FinalReport(
            thread_id=state["thread_id"],
            repo_ref=state["repo_ref"],
            dependency=state["dependency"],
            constraints=state["constraints"],
            commit_sha=analysis.commit_sha if analysis is not None else None,
            completed_at=datetime.now(UTC),
            repo_analysis=analysis,
            affected_files=tuple(state["affected_files"]),
            breaking_changes=tuple(state["breaking_changes"]),
            rag_context=state["rag_context"],
            risk_analysis=state["risk_analysis"],
            migration_plan=state["migration_plan"],
            validation=state["validation"],
            human_decisions=tuple(state["human_decisions"]),
            usage=UsageSummary.from_calls(state["llm_calls"]),
            agent_trace=tuple(state["agent_trace"]),
            # `message`, never `detail`: the technical field is for logs
            # correlated by thread_id (CLAUDE.md rule 27), and putting it in
            # the report would leak provider responses and internal exception
            # text into a document people share.
            errors=tuple(error.message for error in state["errors"]),
        )

        warnings = report.completed_with_warnings
        return {
            "final_report": report,
            "summary": (
                "Run complete with validation warnings."
                if warnings
                else "Run complete; every validation check passed."
            ),
        }

    return body
