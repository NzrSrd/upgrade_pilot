"""Plan generation through the graph: the flip, the repair, and rule 19.

Three properties live here that the unit tests cannot reach.

**The decision flip.** Spec 8.3: "A test asserts that resuming the same
checkpoint with the opposite option yields a different `strategy_id`." That is
how "the human decision affects downstream generation" gets *verified* rather
than claimed, and it needs two real runs over one real checkpointed thread.

**The bounded repair.** One retry, then finish and say so. A validator that
can be retried indefinitely is one the generator learns to satisfy by
attrition.

**Rule 19 at the point it could be broken.** A model asked for a file path
produces a plausible one, and a plausible path in a migration plan is an
instruction to edit a file that does not exist. The schema has no file field,
and this asserts the consequence: every path in the finished plan came from
the analyzer.
"""

from pathlib import Path
from typing import Any

from langgraph.types import Command

from tests.graph.graph_fixtures import (
    COMPLETE_CORPUS,
    a_config,
    a_grade_response,
    a_graph_environment,
    a_narrative_response,
    a_plan_response,
    a_plan_response_draft,
    a_state,
)
from upgradepilot.graph.build import compile_graph
from upgradepilot.graph.checkpointer import open_checkpointer
from upgradepilot.graph.inspect import is_awaiting_human
from upgradepilot.models.enums import (
    StrategyId,
    TraceEventKind,
    ValidationCheckId,
)

FIXTURE_HIGH_CONFIDENCE = ("BaseModel", "Config", "Optional", "validator")

REAL_PLAN = a_plan_response_draft(
    ("Replace @validator with @field_validator", ("validator",)),
    ("Replace class Config with model_config", ("Config",)),
    ("Update renamed BaseModel methods", ("BaseModel", "dict", "copy", "parse_obj", "schema")),
    ("Give Optional fields explicit defaults", ("Optional",)),
)

SCRIPT = [
    a_plan_response(("everything about this upgrade", FIXTURE_HIGH_CONFIDENCE)),
    a_grade_response(sufficient=True),
    a_narrative_response("Validators and model config both change."),
    REAL_PLAN,
]


async def run_answering(
    tmp_path: Path,
    *,
    answers: list[str],
    responses: list[Any] | None = None,
) -> Any:
    """One complete run, answering each question with a named option."""
    deps, repo_root, _ = a_graph_environment(
        tmp_path, responses=responses or list(SCRIPT), documents=COMPLETE_CORPUS
    )
    async with open_checkpointer(tmp_path / "c.db") as saver:
        graph = compile_graph(deps=deps, checkpointer=saver)
        config = a_config()
        result = await graph.ainvoke(a_state(repo_root), config)
        for answer in answers:
            result = await graph.ainvoke(Command(resume=answer), config)
        return result


# -- the decision flip ------------------------------------------------------


async def test_the_opposite_answer_yields_a_different_strategy(tmp_path: Path) -> None:
    """Spec 8.3's decision-flip test, and the reason `StrategyId` is an enum
    rather than a free string: comparing two free-form strings would pass on a
    typo."""
    direct = await run_answering(
        tmp_path / "a", answers=["direct_migration", "proceed-with-mitigation"]
    )
    layered = await run_answering(
        tmp_path / "b", answers=["compatibility_layer", "proceed-with-mitigation"]
    )

    chosen = {direct["migration_plan"].strategy_id, layered["migration_plan"].strategy_id}

    assert chosen == {StrategyId.DIRECT_MIGRATION, StrategyId.COMPATIBILITY_LAYER}
    assert len(chosen) == 2, "the opposite answer produced the same strategy"


async def test_the_plan_records_what_the_decision_changed(tmp_path: Path) -> None:
    """Not a claim in a README: check 9 refuses a plan where a decision exists
    and this list is empty."""
    result = await run_answering(tmp_path, answers=["staged_rollout", "proceed-with-mitigation"])

    plan = result["migration_plan"]
    applied = {entry.decision_id for entry in plan.human_decisions_applied}
    answered = {decision.question_id for decision in result["human_decisions"]}
    assert answered <= applied
    strategy_entry = next(
        entry for entry in plan.human_decisions_applied if entry.decision_id == "strategy-choice"
    )
    assert "migrate in stages" in strategy_entry.how_it_changed_the_plan.lower()


async def test_the_chosen_strategy_decides_whether_a_step_needs_downtime(
    tmp_path: Path,
) -> None:
    """Set from the strategy, never from prose. Check 10 refuses a downtime
    step under a zero-downtime constraint, and a flag the model could set
    would make that check a negotiation."""
    direct = await run_answering(
        tmp_path / "a", answers=["direct_migration", "proceed-with-mitigation"]
    )
    layered = await run_answering(
        tmp_path / "b", answers=["compatibility_layer", "proceed-with-mitigation"]
    )

    assert any(step.requires_downtime for step in direct["migration_plan"].steps)
    assert not any(step.requires_downtime for step in layered["migration_plan"].steps)


# -- rule 19 ----------------------------------------------------------------


async def test_every_file_in_the_plan_came_from_the_analyzer(tmp_path: Path) -> None:
    result = await run_answering(tmp_path, answers=["staged_rollout", "proceed-with-mitigation"])

    analysis = result["repo_analysis"]
    plan = result["migration_plan"]
    assert plan.steps, "no step was produced, so this proves nothing"
    for step in plan.steps:
        assert step.files, f"step {step.order} names no file"
        for path in step.files:
            assert path in analysis.citable_paths()


async def test_a_step_naming_only_invented_symbols_is_dropped(tmp_path: Path) -> None:
    """A symbol the model invented resolves to no file and cites nothing, so
    the step has nothing behind it. Dropping it is the difference between a
    plan with one fewer step and a node that crashes on a vague answer."""
    script = [
        a_plan_response(("everything", FIXTURE_HIGH_CONFIDENCE)),
        a_grade_response(sufficient=True),
        a_narrative_response(),
        a_plan_response_draft(
            ("Deal with validators", ("validator",)),
            ("Review the remaining usages", ("MadeUpSymbol",)),
        ),
    ]

    result = await run_answering(
        tmp_path, answers=["staged_rollout", "proceed-with-mitigation"], responses=script
    )

    titles = [step.title for step in result["migration_plan"].steps]
    assert titles == ["Deal with validators"]
    assert [step.order for step in result["migration_plan"].steps] == [1]


# -- the bounded repair -----------------------------------------------------


EMPTY_DRAFT = a_plan_response_draft(("Review the remaining usages", ("MadeUpSymbol",)))
"""A draft whose only step names a symbol that does not exist.

The step resolves to no file and cites nothing, so it is dropped, so the plan
has no steps at all -- which fails check 7 unambiguously. Chosen over a draft
that under-covers because check 8 is satisfied by the fixture repository more
easily than it looks: only one of its three affected files uses a
high-confidence symbol, so a plan touching that file alone is genuinely
complete.
"""


async def test_a_failing_plan_is_regenerated_exactly_once(tmp_path: Path) -> None:
    """The first draft produces no usable step, which fails check 7; the
    second is a real plan."""
    script = [
        a_plan_response(("everything", FIXTURE_HIGH_CONFIDENCE)),
        a_grade_response(sufficient=True),
        a_narrative_response(),
        EMPTY_DRAFT,
        REAL_PLAN,
    ]

    result = await run_answering(
        tmp_path, answers=["staged_rollout", "proceed-with-mitigation"], responses=script
    )

    started = [
        event
        for event in result["agent_trace"]
        if event.node == "generate_plan" and event.kind is TraceEventKind.NODE_STARTED
    ]
    assert len(started) == 2, "the plan was not regenerated exactly once"
    assert result["validation"].attempt == 2
    assert result["validation"].passed


async def test_the_repair_attempt_is_told_what_failed(tmp_path: Path) -> None:
    """Generated from the failing checks themselves, so a check whose meaning
    is refined describes itself to the retry."""
    script = [
        a_plan_response(("everything", FIXTURE_HIGH_CONFIDENCE)),
        a_grade_response(sufficient=True),
        a_narrative_response(),
        EMPTY_DRAFT,
        REAL_PLAN,
    ]
    deps, repo_root, model = a_graph_environment(
        tmp_path, responses=script, documents=COMPLETE_CORPUS
    )
    async with open_checkpointer(tmp_path / "c.db") as saver:
        graph = compile_graph(deps=deps, checkpointer=saver)
        config = a_config()
        result = await graph.ainvoke(a_state(repo_root), config)
        for answer in ("staged_rollout", "proceed-with-mitigation"):
            result = await graph.ainvoke(Command(resume=answer), config)

    repair_prompt = model.prompts[-1]
    assert ValidationCheckId.PLAN_IS_ORDERED.value in repair_prompt
    assert "Rewrite the plan" in repair_prompt


async def test_a_plan_that_still_fails_completes_with_warnings(tmp_path: Path) -> None:
    """Never silently passes; never loops forever. The run terminates with the
    failures shown, because a run that loops is worse for the user than a run
    that says what is wrong with its own output."""
    script = [
        a_plan_response(("everything", FIXTURE_HIGH_CONFIDENCE)),
        a_grade_response(sufficient=True),
        a_narrative_response(),
        EMPTY_DRAFT,
        EMPTY_DRAFT,
    ]

    result = await run_answering(
        tmp_path, answers=["staged_rollout", "proceed-with-mitigation"], responses=script
    )

    report = result["final_report"]
    assert report is not None
    assert report.completed_with_warnings
    assert not result["validation"].passed
    assert result["validation"].attempt == 2
    failed = {outcome.check_id for outcome in result["validation"].failures}
    assert ValidationCheckId.PLAN_IS_ORDERED in failed


# -- finalize ---------------------------------------------------------------


async def test_the_final_report_is_assembled_from_the_run_alone(
    tmp_path: Path,
) -> None:
    """`finalize` is a pure function over state, because the API may build this
    from a checkpoint long after the run ended."""
    result = await run_answering(tmp_path, answers=["staged_rollout", "proceed-with-mitigation"])

    report = result["final_report"]
    assert report.thread_id == "t-1"
    assert report.commit_sha == result["repo_analysis"].commit_sha
    assert report.usage.calls == len({call.call_id for call in result["llm_calls"]})
    assert report.migration_plan is result["migration_plan"]
    assert report.risk_analysis is result["risk_analysis"]
    assert not report.completed_with_warnings


async def test_the_report_carries_user_facing_messages_not_technical_detail(
    tmp_path: Path,
) -> None:
    """CLAUDE.md rule 27: `detail` is for logs correlated by thread_id, and
    putting it in the report would leak provider responses and internal
    exception text into a document people share."""
    from upgradepilot.models.plan import FinalReport

    assert FinalReport.model_fields["errors"].annotation is not None
    result = await run_answering(tmp_path, answers=["staged_rollout", "proceed-with-mitigation"])

    assert all(isinstance(message, str) for message in result["final_report"].errors)


async def test_the_run_reaches_the_end_and_is_not_awaiting_anyone(
    tmp_path: Path,
) -> None:
    deps, repo_root, _ = a_graph_environment(
        tmp_path, responses=list(SCRIPT), documents=COMPLETE_CORPUS
    )
    async with open_checkpointer(tmp_path / "c.db") as saver:
        graph = compile_graph(deps=deps, checkpointer=saver)
        config = a_config()
        result = await graph.ainvoke(a_state(repo_root), config)
        for answer in ("staged_rollout", "proceed-with-mitigation"):
            result = await graph.ainvoke(Command(resume=answer), config)
        snapshot = await graph.aget_state(config)

    assert not is_awaiting_human(snapshot)
    assert snapshot.next == ()
    assert result["final_report"] is not None
