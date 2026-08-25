"""The skeleton graph: Phase 4's exit criteria, executed rather than argued.

"A graph executes start to finish over stubs with checkpointed state, and
usage aggregation is proven idempotent." The node bodies are stubs -- each
real body arrives with the phase that owns it -- but everything *around* them
is the real thing: the real state channels and reducers, the real
`AsyncSqliteSaver`, the real `TrackedLLM` over a scripted model, and the real
`UsageSummary` derivation.

That split is deliberate. The foundation is what has to be right before any
node body can be trusted, and it is also what no later phase will think to
re-test.
"""

from pathlib import Path
from typing import Any

import pytest
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel

from tests.llm.fake_chat_model import ScriptedChatModel, ScriptedResponse
from upgradepilot.config import ModelPrice
from upgradepilot.graph.build import NODE_SEQUENCE, compile_graph
from upgradepilot.graph.checkpointer import open_checkpointer
from upgradepilot.models.enums import TraceEventKind
from upgradepilot.models.errors import ErrorCode, RepoUnavailableError
from upgradepilot.models.inputs import DependencySpec, LocalRepoRef, UserConstraints
from upgradepilot.models.state import MigrationState, initial_state
from upgradepilot.models.usage import UsageSummary
from upgradepilot.services.llm.tracked import TrackedLLM


class Narrative(BaseModel):
    summary: str


PRICING = {"scripted-model": ModelPrice(input_per_1m=1.0, output_per_1m=2.0)}


def a_tracked_llm(responses: int = 8) -> TrackedLLM:
    """Enough scripted answers for any single run in this file.

    The scripted model raises rather than looping when it runs out, so an
    over-generous queue cannot mask a node calling the model more often than
    intended -- the count is asserted directly where it matters.
    """
    return TrackedLLM(
        ScriptedChatModel(
            responses=[
                ScriptedResponse(parsed=Narrative(summary=f"stub narrative {i}"))
                for i in range(responses)
            ]
        ),
        model_name="scripted-model",
        pricing=PRICING,
    )


def a_state(thread_id: str = "t-1") -> MigrationState:
    return initial_state(
        thread_id=thread_id,
        repo_ref=LocalRepoRef(path="/tmp/repo"),
        dependency=DependencySpec(
            name="pydantic", current_version="1.10.13", target_version="2.9.0"
        ),
        constraints=UserConstraints(),
    )


def a_config(thread_id: str = "t-1") -> RunnableConfig:
    return {"configurable": {"thread_id": thread_id}}


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    return str(tmp_path / "checkpoints.db")


# -- start to finish --------------------------------------------------------


async def test_the_graph_runs_from_start_to_end_over_stubs(db_path: str) -> None:
    async with open_checkpointer(db_path) as saver:
        graph = compile_graph(llm=a_tracked_llm(), checkpointer=saver)
        config = a_config()

        result = await graph.ainvoke(a_state(), config)

        state = await graph.aget_state(config)
        assert state.next == (), "the graph did not reach END"
        assert result["errors"] == []


async def test_every_node_reports_itself_starting_and_finishing(db_path: str) -> None:
    """The trace is the run's only observable record for a reader. A node
    that ran without tracing is a gap nothing else can reveal -- the run
    still completes, and the panel simply shows one fewer step."""
    async with open_checkpointer(db_path) as saver:
        graph = compile_graph(llm=a_tracked_llm(), checkpointer=saver)

        result = await graph.ainvoke(a_state(), a_config("t-1"))

    started = [e.node for e in result["agent_trace"] if e.kind is TraceEventKind.NODE_STARTED]
    completed = [e.node for e in result["agent_trace"] if e.kind is TraceEventKind.NODE_COMPLETED]

    assert started == list(NODE_SEQUENCE)
    assert completed == list(NODE_SEQUENCE)


async def test_the_trace_never_carries_a_prompt(db_path: str) -> None:
    """CLAUDE.md rule 26 at the level where it can actually be violated.

    `TraceEvent` has no field a prompt fits in, but a node could still paste
    one into `summary` or `detail`. The scripted prompts are known, so this
    asserts none of them appear in the user-facing channel.
    """
    async with open_checkpointer(db_path) as saver:
        model = ScriptedChatModel(
            responses=[ScriptedResponse(parsed=Narrative(summary="stub")) for _ in range(8)]
        )
        llm = TrackedLLM(model, model_name="scripted-model", pricing=PRICING)
        graph = compile_graph(llm=llm, checkpointer=saver)

        result = await graph.ainvoke(a_state(), a_config("t-1"))

        assert model.prompts, "no prompt was issued, so this test proves nothing"
        rendered = " ".join(f"{e.summary} {e.detail or ''}" for e in result["agent_trace"])
        for prompt in model.prompts:
            assert prompt not in rendered


# -- checkpointing ----------------------------------------------------------


async def test_state_outlives_the_connection_that_wrote_it(db_path: str) -> None:
    """Disk durability, not an in-memory graph object re-reading itself. A
    second `AsyncSqliteSaver` opened over the same file after the first has
    closed must see the completed run."""
    config = a_config()
    async with open_checkpointer(db_path) as saver:
        graph = compile_graph(llm=a_tracked_llm(), checkpointer=saver)
        await graph.ainvoke(a_state(), config)

    async with open_checkpointer(db_path) as reopened:
        graph = compile_graph(llm=a_tracked_llm(), checkpointer=reopened)
        state = await graph.aget_state(config)

    assert state.values["thread_id"] == "t-1"
    assert state.values["llm_calls"], "the usage records did not survive the checkpoint"


async def test_two_threads_do_not_share_state(db_path: str) -> None:
    """Spec §9.2 runs several threads in one process. State bleeding between
    them would attribute one user's evidence -- and one user's cost -- to
    another."""
    async with open_checkpointer(db_path) as saver:
        graph = compile_graph(llm=a_tracked_llm(16), checkpointer=saver)

        first = await graph.ainvoke(a_state("t-1"), a_config("t-1"))
        second = await graph.ainvoke(a_state("t-2"), a_config("t-2"))

    assert first["thread_id"] == "t-1"
    assert second["thread_id"] == "t-2"
    assert {c.call_id for c in first["llm_calls"]} & {
        c.call_id for c in second["llm_calls"]
    } == set()


# -- THE Phase 4 exit property: usage across a real resume ------------------


async def test_a_duplicated_call_record_does_not_change_the_totals(db_path: str) -> None:
    """THE Phase 4 property, at graph level: the same record reaching the
    channel twice must not move any number the product prints.

    This is applied through `aupdate_state`, which appends through the real
    `operator.add` channel, because that is the failure mode itself -- one
    record, written twice. A running counter reports the call twice here and
    the figure stays entirely plausible.

    The raw channel is asserted to *have* grown, so the test cannot pass by
    the duplicate never arriving.
    """
    config = a_config()
    async with open_checkpointer(db_path) as saver:
        graph = compile_graph(llm=a_tracked_llm(), checkpointer=saver)
        finished = await graph.ainvoke(a_state(), config)
        before = UsageSummary.from_calls(finished["llm_calls"])
        assert before.calls == 1, "the skeleton should make exactly one model call"

        await graph.aupdate_state(config, {"llm_calls": list(finished["llm_calls"])})
        state = await graph.aget_state(config)

    assert len(state.values["llm_calls"]) == 2, "the duplicate never reached the channel"
    assert UsageSummary.from_calls(state.values["llm_calls"]) == before


async def test_pausing_and_resuming_does_not_change_what_the_run_cost(
    tmp_path: Path,
) -> None:
    """What a checkpointer resume actually does, measured rather than assumed.

    An earlier version of this test claimed a resume would double-count
    without deduplication. That was wrong, and worth recording: measured
    against the pinned LangGraph, `interrupt_before` pauses *between* nodes
    and the completed node is not re-executed on resume -- one model
    invocation, one record, before and after. There is no duplication for the
    dedup to remove, so an assertion that ids were unique proved nothing.

    The property that is genuinely available at this phase is the one
    asserted here: pausing changes *when* the work happens and nothing else.
    The re-execution case that really does bill twice needs `interrupt()`
    inside a node body -- ADR-001 records it, and the rule adopted in response
    (a node that interrupts performs no LLM call before interrupting) belongs
    to Phase 7, along with the test that holds it.
    """
    straight_config = a_config("straight")
    paused_config = a_config("paused")

    async with open_checkpointer(tmp_path / "a.db") as saver:
        graph = compile_graph(llm=a_tracked_llm(), checkpointer=saver)
        straight = await graph.ainvoke(a_state("straight"), straight_config)

    async with open_checkpointer(tmp_path / "b.db") as saver:
        model = ScriptedChatModel(
            responses=[ScriptedResponse(parsed=Narrative(summary="stub")) for _ in range(8)]
        )
        graph = compile_graph(
            llm=TrackedLLM(model, model_name="scripted-model", pricing=PRICING),
            checkpointer=saver,
            interrupt_before=["generate_plan"],
        )
        await graph.ainvoke(a_state("paused"), paused_config)
        invocations_at_pause = len(model.prompts)
        resumed = await graph.ainvoke(None, paused_config)

    assert invocations_at_pause == 1, "the pause fell before the model call, not after it"
    assert len(model.prompts) == 1, "the resume re-invoked the model"

    straight_usage = UsageSummary.from_calls(straight["llm_calls"])
    resumed_usage = UsageSummary.from_calls(resumed["llm_calls"])
    assert resumed_usage.calls == straight_usage.calls
    assert resumed_usage.total_tokens == straight_usage.total_tokens


async def test_a_resumed_run_reaches_the_same_end_as_an_uninterrupted_one(
    tmp_path: Path,
) -> None:
    """Pausing must change *when* the work happens and nothing else. A resume
    that skipped or repeated a node would still finish, and only a comparison
    against the straight-through run would show it."""
    straight_config = a_config("straight")
    paused_config = a_config("paused")

    async with open_checkpointer(tmp_path / "a.db") as saver:
        graph = compile_graph(llm=a_tracked_llm(), checkpointer=saver)
        straight = await graph.ainvoke(a_state("straight"), straight_config)

    async with open_checkpointer(tmp_path / "b.db") as saver:
        graph = compile_graph(
            llm=a_tracked_llm(), checkpointer=saver, interrupt_before=["generate_plan"]
        )
        await graph.ainvoke(a_state("paused"), paused_config)
        resumed = await graph.ainvoke(None, paused_config)

    def nodes(result: Any) -> list[str]:
        """`ainvoke` is typed `dict[str, Any] | Any` on this overload, so the
        annotation here would be a claim mypy cannot check either way."""
        return [e.node for e in result["agent_trace"] if e.kind is TraceEventKind.NODE_COMPLETED]

    assert nodes(resumed) == nodes(straight)


# -- rule 20: a failing node records, never crashes -------------------------


async def test_a_failing_node_records_an_app_error_and_a_trace_event(db_path: str) -> None:
    """CLAUDE.md rule 20: a caught exception produces an `AppError` in state
    *and* a trace event, always.

    Enforced once in the node wrapper rather than in each node body, because
    "remember to catch" is the kind of rule that holds for every node except
    the one written in a hurry -- and a node that dies unrecorded takes the
    whole run down with no explanation the user can act on.
    """
    async with open_checkpointer(db_path) as saver:
        graph = compile_graph(
            llm=a_tracked_llm(),
            checkpointer=saver,
            fail_in={"analyze_repo": RepoUnavailableError("The repository could not be read.")},
        )

        result = await graph.ainvoke(a_state(), a_config("t-1"))

    assert [e.code for e in result["errors"]] == [ErrorCode.REPO_UNAVAILABLE]
    assert result["errors"][0].node == "analyze_repo"
    assert any(e.kind is TraceEventKind.ERROR_RECORDED for e in result["agent_trace"])


async def test_a_failing_node_does_not_stop_the_trace_recording_the_rest(db_path: str) -> None:
    """The run continues so that the report can say what *was* established
    alongside what failed. A run that aborts on the first error throws away
    evidence it had already gathered and paid for."""
    async with open_checkpointer(db_path) as saver:
        graph = compile_graph(
            llm=a_tracked_llm(),
            checkpointer=saver,
            fail_in={"analyze_repo": RepoUnavailableError("nope")},
        )

        result = await graph.ainvoke(a_state(), a_config("t-1"))

    completed = [e.node for e in result["agent_trace"] if e.kind is TraceEventKind.NODE_COMPLETED]
    assert "finalize" in completed


async def test_an_unexpected_exception_is_recorded_as_internal_not_swallowed(
    db_path: str,
) -> None:
    """A bug in a node body is not a domain error, and must not be reported
    as one. It still has to reach state rather than escaping -- rule 20 says
    nothing is swallowed, not that everything is a known condition."""
    async with open_checkpointer(db_path) as saver:
        graph = compile_graph(
            llm=a_tracked_llm(),
            checkpointer=saver,
            fail_in={"assess_risk": TypeError("a bug in the node body")},
        )

        result = await graph.ainvoke(a_state(), a_config("t-1"))

    assert [e.code for e in result["errors"]] == [ErrorCode.INTERNAL]
    assert "TypeError" in (result["errors"][0].detail or "")
