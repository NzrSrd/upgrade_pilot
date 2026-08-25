"""Spec 8.2 end to end: the pause, the resume, and the refusal to proceed on garbage.

Phase 7's exit criterion has two halves that pull in opposite directions: "a
genuine tradeoff pauses the graph with enough context to decide; a settled
question does not pause it at all." Both are asserted here against the real
graph and a real checkpointer, because the behaviour the design leans on -- a
node re-executing from the top on resume, a replayed `interrupt()` returning
its earlier value -- is LangGraph's, and a fake of it would let us assume
exactly what needs proving.

Those behaviours were measured before they were relied on:
`backend/probes/probe_interrupt.py`.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from langgraph.types import Command

from tests.graph.graph_fixtures import (
    COMPLETE_CORPUS,
    a_config,
    a_grade_response,
    a_graph_environment,
    a_narrative_response,
    a_plan_response,
    a_state,
    answer_all,
    run_to_completion,
)
from upgradepilot.graph.build import compile_graph
from upgradepilot.graph.checkpointer import open_checkpointer
from upgradepilot.graph.inspect import is_awaiting_human, pending_payload
from upgradepilot.models.decision import InterruptPayload, unanswered
from upgradepilot.models.enums import (
    DecisionKind,
    RiskLevel,
    StrategyId,
    TraceEventKind,
)
from upgradepilot.models.inputs import UserConstraints

FIXTURE_HIGH_CONFIDENCE = ("BaseModel", "Config", "Optional", "validator")

SCRIPT = [
    a_plan_response(("everything about this upgrade", FIXTURE_HIGH_CONFIDENCE)),
    a_grade_response(sufficient=True),
    a_narrative_response("The upgrade touches validators and model config."),
]
"""One retrieval round plus the risk narrative -- and nothing for
`human_review`, because it makes no model call at all. The scripted model
raises when it runs out, so if `human_review` ever started calling one, these
tests fail rather than quietly billing for it on every resume."""


@asynccontextmanager
async def a_paused_run(
    tmp_path: Path,
    *,
    constraints: UserConstraints | None = None,
    thread: str = "t-1",
) -> AsyncIterator[tuple[Any, Any, Any]]:
    """Start a run, leave it wherever it stops, and yield `(graph, config, result)`.

    A context manager rather than a plain call because the checkpointer owns a
    live SQLite connection. Returning the graph from an ordinary function
    leaves that connection to be closed by whatever runs next, and the first
    resume then fails with "Connection closed" -- a test-harness bug wearing
    the costume of a checkpointer bug.
    """
    deps, repo_root, _ = a_graph_environment(tmp_path, responses=SCRIPT, documents=COMPLETE_CORPUS)
    async with open_checkpointer(tmp_path / "c.db") as saver:
        graph = compile_graph(deps=deps, checkpointer=saver)
        state = a_state(repo_root, thread)
        if constraints is not None:
            state["constraints"] = constraints
        config = a_config(thread)
        result = await graph.ainvoke(state, config)
        yield graph, config, result


def interrupts_of(result: Any) -> list[InterruptPayload]:
    return [entry.value for entry in (result.get("__interrupt__") or [])]


# -- the interrupt fires ----------------------------------------------------


async def test_a_genuine_tradeoff_pauses_the_graph(tmp_path: Path) -> None:
    async with a_paused_run(tmp_path) as (graph, config, result):
        assert interrupts_of(result), "the run did not stop for a decision"
        snapshot = await graph.aget_state(config)

    assert is_awaiting_human(snapshot)
    assert pending_payload(snapshot) is not None


async def test_the_paused_run_carries_enough_context_to_decide(tmp_path: Path) -> None:
    """The person who answers is usually not the person who started the run,
    and they arrive at a paused thread with no memory of it. A question that
    assumes context is a question that gets answered by whoever is least
    equipped to answer it."""
    async with a_paused_run(tmp_path) as (_, _, result):
        payload = interrupts_of(result)[0]

    assert payload.reason and payload.question
    assert payload.consequences_if_unanswered
    assert len(payload.options) >= 2
    assert payload.recommendation_id in {option.id for option in payload.options}
    assert payload.evidence, "the question cites nothing"
    for option in payload.options:
        assert option.consequences
        assert option.supporting_evidence


async def test_the_first_question_asked_is_the_strategy_choice(tmp_path: Path) -> None:
    """Order is fixed and not arbitrary: a version discrepancy would come
    first because it changes what is being upgraded, then the strategy, whose
    answer scopes everything after it. The fixture repository's manifest
    agrees with the request, so the strategy question leads."""
    async with a_paused_run(tmp_path) as (_, _, result):
        assert interrupts_of(result)[0].kind is DecisionKind.STRATEGY_CHOICE


async def test_no_model_call_happens_while_the_run_is_paused(tmp_path: Path) -> None:
    """ADR-001's rule, asserted where it can be violated. A node that
    interrupts re-executes from the top on every resume -- four times for a
    two-question node, measured -- so a model call above the interrupt is
    billed once per resume while one usage record survives. `human_review`
    makes none, which is why `assess_risk` owns payload construction.
    """
    deps, repo_root, model = a_graph_environment(
        tmp_path, responses=SCRIPT, documents=COMPLETE_CORPUS
    )
    async with open_checkpointer(tmp_path / "c.db") as saver:
        graph = compile_graph(deps=deps, checkpointer=saver)
        config = a_config()
        await graph.ainvoke(a_state(repo_root), config)
        calls_at_pause = len(model.prompts)

        await graph.ainvoke(Command(resume="not-an-option"), config)
        await graph.ainvoke(Command(resume="direct_migration"), config)

    assert calls_at_pause == 3
    assert len(model.prompts) == 3, "a resume re-invoked the model"


# -- the checkpoint, and resuming the same thread ---------------------------


async def test_the_pending_question_survives_a_reopened_checkpointer(
    tmp_path: Path,
) -> None:
    """Not an in-memory graph re-reading itself: a second connection over the
    same file, after the first has closed, must still hold the question."""
    deps, repo_root, _ = a_graph_environment(tmp_path, responses=SCRIPT, documents=COMPLETE_CORPUS)
    config = a_config()
    db = tmp_path / "c.db"

    async with open_checkpointer(db) as saver:
        graph = compile_graph(deps=deps, checkpointer=saver)
        await graph.ainvoke(a_state(repo_root), config)

    async with open_checkpointer(db) as reopened:
        graph = compile_graph(deps=deps, checkpointer=reopened)
        snapshot = await graph.aget_state(config)

    assert is_awaiting_human(snapshot)
    pending = snapshot.values["pending_decisions"]
    assert pending and isinstance(pending[0], InterruptPayload), (
        "the payload came back as a plain dict: the checkpoint allowlist lost it"
    )
    assert unanswered(pending, snapshot.values["human_decisions"])


async def test_an_answer_is_committed_before_the_next_question_is_asked(
    tmp_path: Path,
) -> None:
    """Why `human_review` answers one question per execution.

    A node that interrupts produces no state update until it finishes, so a
    node asking every question in one execution leaves the earlier answers in
    LangGraph's resume store and never writes them to `human_decisions`.
    Everything downstream derives from that channel -- the router's "what is
    still unanswered" and the API's "which question is the user looking at" --
    so a partially-answered run kept showing the question it had already
    answered. Measured: `human_decisions` came back empty from a two-question
    run that had answered one.
    """
    async with a_paused_run(tmp_path) as (graph, config, first):
        assert len(first["pending_decisions"]) >= 2, (
            "this repository shape raises one question, so this proves nothing"
        )
        after_one = await graph.ainvoke(Command(resume="staged_rollout"), config)
        snapshot = await graph.aget_state(config)

    assert is_awaiting_human(snapshot), "the run did not pause for the second question"
    assert [d.selected_option_id for d in after_one["human_decisions"]] == ["staged_rollout"]


async def test_resuming_continues_the_same_thread_to_the_end(tmp_path: Path) -> None:
    async with a_paused_run(tmp_path) as (graph, config, result):
        final = await answer_all(graph, config, result, answers=["staged_rollout"])
        snapshot = await graph.aget_state(config)

    assert not is_awaiting_human(snapshot)
    assert final["human_decisions"][0].selected_option_id == "staged_rollout"
    assert final["thread_id"] == "t-1"


async def test_the_answer_is_recorded_in_the_trace_as_applied(tmp_path: Path) -> None:
    async with a_paused_run(tmp_path) as (graph, config, _):
        final = await graph.ainvoke(Command(resume="staged_rollout"), config)

    applied = [
        event for event in final["agent_trace"] if event.kind is TraceEventKind.DECISION_APPLIED
    ]
    assert applied and "Migrate in stages" in applied[0].summary


# -- no interrupt when the constraints decide -------------------------------


async def test_settled_constraints_do_not_pause_the_run_at_all(tmp_path: Path) -> None:
    """Spec 8.2's negative case, which is the whole reason the conditional
    edge exists. A dialog everyone clicks through launders a default into an
    apparent decision.

    These constraints settle every axis the viable strategies differ on, so
    the strategy question is never raised and a trace event says why.
    """
    settled = UserConstraints(
        zero_downtime=True, minimize_effort=True, risk_tolerance=RiskLevel.LOW
    )
    async with a_paused_run(tmp_path, constraints=settled) as (_, _, result):
        strategy_questions = [
            payload
            for payload in result["pending_decisions"]
            if payload.kind is DecisionKind.STRATEGY_CHOICE
        ]
        resolved = [
            event.summary
            for event in result["agent_trace"]
            if event.kind is TraceEventKind.AGENT_DECISION and event.node == "assess_risk"
        ]

    assert strategy_questions == [], "the constraints settled it and it asked anyway"
    assert any("resolved by the stated constraints" in summary for summary in resolved), (
        "a skipped question that leaves no trace is indistinguishable from a "
        "question that never came up"
    )


# -- an invalid decision is refused, not absorbed ---------------------------


async def test_an_unknown_option_re_interrupts_rather_than_proceeding(
    tmp_path: Path,
) -> None:
    """`interrupt()` returns whatever HTTP handed it. Proceeding with garbage
    would produce a plan attributed to a decision nobody made."""
    async with a_paused_run(tmp_path) as (graph, config, _):
        again = await graph.ainvoke(Command(resume="not-an-option"), config)

    payloads = interrupts_of(again)
    assert payloads, "the run accepted an option that does not exist"
    assert payloads[0].validation_error is not None
    assert "not one of the options offered" in payloads[0].validation_error


async def test_the_re_asked_question_names_what_was_wrong_and_what_is_offered(
    tmp_path: Path,
) -> None:
    """The person who has to fix it is looking at a form, not a stack trace."""
    async with a_paused_run(tmp_path) as (graph, config, _):
        again = await graph.ainvoke(Command(resume="not-an-option"), config)

    error = interrupts_of(again)[0].validation_error or ""
    assert "'not-an-option'" in error
    assert StrategyId.DIRECT_MIGRATION.value in error


async def test_a_valid_answer_after_an_invalid_one_completes_the_run(
    tmp_path: Path,
) -> None:
    """The loop terminates. On resume LangGraph replays each `interrupt()` in
    order and returns the value it was given before, pausing only at the
    newest -- measured in `probes/probe_interrupt.py` -- so each pass consumes
    one more already-supplied resume value."""
    async with a_paused_run(tmp_path) as (graph, config, _):
        await graph.ainvoke(Command(resume="not-an-option"), config)
        accepted = await graph.ainvoke(Command(resume="compatibility_layer"), config)

    assert [d.selected_option_id for d in accepted["human_decisions"]] == ["compatibility_layer"]


async def test_an_answer_for_a_different_question_is_refused(tmp_path: Path) -> None:
    async with a_paused_run(tmp_path) as (graph, config, _):
        again = await graph.ainvoke(
            Command(
                resume={
                    "question_id": "some-other-question",
                    "selected_option_id": "direct_migration",
                }
            ),
            config,
        )

    error = interrupts_of(again)[0].validation_error or ""
    assert "for a different question" in error


@pytest.mark.parametrize("garbage", [42, ["direct_migration"]])
async def test_an_answer_of_the_wrong_shape_entirely_is_refused(
    tmp_path: Path, garbage: object
) -> None:
    async with a_paused_run(tmp_path) as (graph, config, _):
        again = await graph.ainvoke(Command(resume=garbage), config)

    error = interrupts_of(again)[0].validation_error or ""
    assert "could not be read" in error


async def test_a_full_decision_object_carries_its_rationale_through(
    tmp_path: Path,
) -> None:
    async with a_paused_run(tmp_path) as (graph, config, _):
        final = await graph.ainvoke(
            Command(
                resume={
                    "question_id": "strategy-choice",
                    "selected_option_id": "staged_rollout",
                    "rationale": "We release weekly and want each stage reviewable.",
                }
            ),
            config,
        )

    decision = final["human_decisions"][0]
    assert decision.rationale == "We release weekly and want each stage reviewable."
    assert decision.decided_at.tzinfo is not None


# -- several questions in sequence ------------------------------------------


async def test_two_questions_are_asked_one_after_the_other(tmp_path: Path) -> None:
    """`human_decisions` being an append channel is what makes this work
    naturally: each resume answers the next outstanding question, and the node
    re-executes replaying the answers already given.

    The fixture repository raises two: the strategy choice, and a risk
    acceptance question because the verdict is high risk at exactly the
    thin-evidence confidence line.
    """
    async with a_paused_run(tmp_path) as (graph, config, first):
        asked_first = interrupts_of(first)[0]
        second = await graph.ainvoke(Command(resume=asked_first.options[0].id), config)
        asked_second = interrupts_of(second)
        assert asked_second, (
            "only one question was raised, so this test proves nothing about sequential interrupts"
        )

        final = await graph.ainvoke(Command(resume=asked_second[0].options[0].id), config)
        snapshot = await graph.aget_state(config)

    assert not is_awaiting_human(snapshot)
    answered = [d.question_id for d in final["human_decisions"]]
    assert answered == [asked_first.question_id, asked_second[0].question_id]
    assert len(set(answered)) == 2


async def test_the_helper_that_answers_every_question_reaches_the_end(
    tmp_path: Path,
) -> None:
    """The shape a caller -- and Phase 9's API -- actually uses."""
    deps, repo_root, _ = a_graph_environment(tmp_path, responses=SCRIPT, documents=COMPLETE_CORPUS)
    async with open_checkpointer(tmp_path / "c.db") as saver:
        graph = compile_graph(deps=deps, checkpointer=saver)
        config = a_config()

        result = await run_to_completion(graph, a_state(repo_root), config)
        snapshot = await graph.aget_state(config)

    assert not is_awaiting_human(snapshot)
    assert result["human_decisions"]


# -- what "awaiting a human" actually means in a checkpoint -----------------


async def test_a_re_asked_question_still_reads_as_awaiting_a_human(
    tmp_path: Path,
) -> None:
    """The measurement `graph/inspect.py` exists for.

    `StateSnapshot.next` reports `()` for a run that is genuinely paused,
    whenever the pause came from a *second* `interrupt()` call inside one node
    execution -- which is exactly what re-asking after an unusable answer
    does. A status ladder reading `next` would report that run as COMPLETED:
    the client stops polling, the question is never answered, and a partial
    report is presented as final.
    """
    async with a_paused_run(tmp_path) as (graph, config, _):
        await graph.ainvoke(Command(resume="not-an-option"), config)
        snapshot = await graph.aget_state(config)

    assert snapshot.next == (), (
        "langgraph changed: `next` now reports the paused node after a re-ask, "
        "and graph/inspect.py's reason for existing needs re-measuring"
    )
    assert is_awaiting_human(snapshot), "a paused run read as finished"
    payload = pending_payload(snapshot)
    assert payload is not None
    assert payload.validation_error is not None


async def test_a_finished_run_is_not_awaiting_anyone(tmp_path: Path) -> None:
    """The complement, without which the test above passes on an
    `is_awaiting_human` that always returns True."""
    async with a_paused_run(tmp_path) as (graph, config, result):
        await answer_all(graph, config, result)
        snapshot = await graph.aget_state(config)

    assert not is_awaiting_human(snapshot)
    assert pending_payload(snapshot) is None
