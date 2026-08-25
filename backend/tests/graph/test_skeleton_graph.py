"""The graph's foundation: channels, checkpoints, usage, and rule 20.

Phase 4's exit criterion -- "a graph executes start to finish with checkpointed
state, and usage aggregation is proven idempotent" -- lives here. The
properties are the ones no later phase would think to re-test, which is why
they have their own file: whether a resumed run costs what a straight-through
run cost is not a question about retrieval or risk, and it stops being asked
the moment it is folded into a test about either.

**What changed with Phase 5.** These tests originally ran over stub node
bodies. They now run over the real graph -- a real repository parsed by the
real analyzer, a real Chroma collection, the real retrieval loop -- with the
chat model scripted and the embedding function offline, exactly as spec 11
prescribes. Nothing about the properties changed; what changed is that they
are now asserted over a graph that does something, so a foundation that only
worked for nodes returning `{}` no longer passes.
"""

from pathlib import Path
from typing import Any

import pytest

from tests.graph.graph_fixtures import (
    a_config,
    a_full_run_script,
    a_graph_environment,
    a_state,
)
from upgradepilot.graph.build import NODE_SEQUENCE, compile_graph
from upgradepilot.graph.checkpointer import open_checkpointer
from upgradepilot.models.enums import TraceEventKind
from upgradepilot.models.errors import ErrorCode, RepoUnavailableError
from upgradepilot.models.usage import UsageSummary

EXPECTED_CALLS_PER_RUN = 7
"""Three retrieval rounds (a plan and a grade each) plus one risk narrative.

Written down rather than derived, because the number is the point: a change
that makes the graph call the model more often should have to come here and
say so. Three rounds is what the default fixture corpus forces -- it documents
three of the fixture repository's four high-confidence symbols, so the
deterministic gate refuses every round and the loop runs its full budget.
"""


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    return str(tmp_path / "checkpoints.db")


# -- start to finish --------------------------------------------------------


async def test_the_graph_runs_from_start_to_end(tmp_path: Path) -> None:
    deps, repo_root, _ = a_graph_environment(tmp_path, responses=a_full_run_script())
    async with open_checkpointer(tmp_path / "c.db") as saver:
        graph = compile_graph(deps=deps, checkpointer=saver)
        config = a_config()

        result = await graph.ainvoke(a_state(repo_root), config)

        state = await graph.aget_state(config)
        assert state.next == (), "the graph did not reach END"
        assert result["errors"] == []


async def test_the_run_produces_cited_evidence_rather_than_only_finishing(
    tmp_path: Path,
) -> None:
    """Reaching END is not the property that matters. A graph that ran every
    node and produced no evidence would pass the test above and be useless --
    so this one asserts the run's actual output: breaking changes that name
    the chunk they were built from."""
    deps, repo_root, _ = a_graph_environment(tmp_path, responses=a_full_run_script())
    async with open_checkpointer(tmp_path / "c.db") as saver:
        graph = compile_graph(deps=deps, checkpointer=saver)

        result = await graph.ainvoke(a_state(repo_root), a_config())

    assert result["repo_analysis"] is not None
    assert result["affected_files"], "no file was reported as using the dependency"
    assert result["breaking_changes"], "no documented breaking change was found"
    for change in result["breaking_changes"]:
        assert change.source.chunk_id
        assert change.source.source_id == change.id
    assert result["rag_context"] is not None
    assert result["rag_context"].evidence_available is True


async def test_every_node_reports_itself_starting_and_finishing(tmp_path: Path) -> None:
    """The trace is the run's only observable record for a reader. A node
    that ran without tracing is a gap nothing else can reveal -- the run
    still completes, and the panel simply shows one fewer step."""
    deps, repo_root, _ = a_graph_environment(tmp_path, responses=a_full_run_script())
    async with open_checkpointer(tmp_path / "c.db") as saver:
        graph = compile_graph(deps=deps, checkpointer=saver)

        result = await graph.ainvoke(a_state(repo_root), a_config())

    def parent_nodes(kind: TraceEventKind) -> list[str]:
        return [
            event.node
            for event in result["agent_trace"]
            if event.kind is kind and event.node in NODE_SEQUENCE
        ]

    assert parent_nodes(TraceEventKind.NODE_STARTED) == list(NODE_SEQUENCE)
    assert parent_nodes(TraceEventKind.NODE_COMPLETED) == list(NODE_SEQUENCE)


async def test_the_trace_never_carries_a_prompt(tmp_path: Path) -> None:
    """CLAUDE.md rule 26 at the level where it can actually be violated.

    `TraceEvent` has no field a prompt fits in, but a node could still paste
    one into `summary` or `detail`. The scripted prompts are known, so this
    asserts none of them appear in the user-facing channel.
    """
    deps, repo_root, model = a_graph_environment(tmp_path, responses=a_full_run_script())
    async with open_checkpointer(tmp_path / "c.db") as saver:
        graph = compile_graph(deps=deps, checkpointer=saver)

        result = await graph.ainvoke(a_state(repo_root), a_config())

    assert model.prompts, "no prompt was issued, so this test proves nothing"
    rendered = " ".join(f"{e.summary} {e.detail or ''}" for e in result["agent_trace"])
    for prompt in model.prompts:
        assert prompt not in rendered


# -- checkpointing ----------------------------------------------------------


async def test_state_outlives_the_connection_that_wrote_it(tmp_path: Path) -> None:
    """Disk durability, not an in-memory graph object re-reading itself. A
    second `AsyncSqliteSaver` opened over the same file after the first has
    closed must see the completed run."""
    deps, repo_root, _ = a_graph_environment(tmp_path, responses=a_full_run_script())
    config = a_config()
    db = tmp_path / "c.db"

    async with open_checkpointer(db) as saver:
        graph = compile_graph(deps=deps, checkpointer=saver)
        await graph.ainvoke(a_state(repo_root), config)

    async with open_checkpointer(db) as reopened:
        graph = compile_graph(deps=deps, checkpointer=reopened)
        state = await graph.aget_state(config)

    assert state.values["thread_id"] == "t-1"
    assert state.values["llm_calls"], "the usage records did not survive the checkpoint"
    assert state.values["breaking_changes"], "the evidence did not survive the checkpoint"


async def test_the_evidence_survives_a_reopen_as_models_not_dictionaries(
    tmp_path: Path,
) -> None:
    """The defect Phase 4's checkpointer work found, re-asserted over types
    that did not exist then.

    An unregistered type comes back from a checkpoint as a plain `dict` rather
    than raising, so every honesty invariant -- `BreakingChange.source` being
    required, `RagEvaluation`'s gate agreeing with its evidence -- would be
    absent from a resumed run with nothing raised at the point of loss. The
    allowlist is derived by walking `upgradepilot.models`, so the types Phase 5
    added are registered by existing; this is what proves that claim rather
    than restating it.
    """
    deps, repo_root, _ = a_graph_environment(tmp_path, responses=a_full_run_script())
    config = a_config()
    db = tmp_path / "c.db"

    async with open_checkpointer(db) as saver:
        graph = compile_graph(deps=deps, checkpointer=saver)
        await graph.ainvoke(a_state(repo_root), config)

    async with open_checkpointer(db) as reopened:
        graph = compile_graph(deps=deps, checkpointer=reopened)
        values = (await graph.aget_state(config)).values

    context = values["rag_context"]
    assert context.stop_reason.value == "iteration_limit", "the enum degraded to a string"
    assert context.evidence_available is True, "a derived property survived only as data"
    assert values["rag_evaluations"][0].sufficient is False
    assert values["breaking_changes"][0].source.chunk_id


async def test_two_threads_do_not_share_state(tmp_path: Path) -> None:
    """Spec 9.2 runs several threads in one process. State bleeding between
    them would attribute one user's evidence -- and one user's cost -- to
    another."""
    deps, repo_root, _ = a_graph_environment(
        tmp_path, responses=[*a_full_run_script(), *a_full_run_script()]
    )
    async with open_checkpointer(tmp_path / "c.db") as saver:
        graph = compile_graph(deps=deps, checkpointer=saver)

        first = await graph.ainvoke(a_state(repo_root, "t-1"), a_config("t-1"))
        second = await graph.ainvoke(a_state(repo_root, "t-2"), a_config("t-2"))

    assert first["thread_id"] == "t-1"
    assert second["thread_id"] == "t-2"
    assert {c.call_id for c in first["llm_calls"]} & {
        c.call_id for c in second["llm_calls"]
    } == set()


# -- THE Phase 4 exit property: usage across a real resume ------------------


async def test_a_duplicated_call_record_does_not_change_the_totals(
    tmp_path: Path,
) -> None:
    """THE Phase 4 property, at graph level: the same record reaching the
    channel twice must not move any number the product prints.

    This is applied through `aupdate_state`, which appends through the real
    `operator.add` channel, because that is the failure mode itself -- one
    record, written twice. A running counter reports the calls twice here and
    the figure stays entirely plausible.

    The raw channel is asserted to *have* grown, so the test cannot pass by
    the duplicate never arriving.
    """
    deps, repo_root, _ = a_graph_environment(tmp_path, responses=a_full_run_script())
    config = a_config()

    async with open_checkpointer(tmp_path / "c.db") as saver:
        graph = compile_graph(deps=deps, checkpointer=saver)
        finished = await graph.ainvoke(a_state(repo_root), config)
        before = UsageSummary.from_calls(finished["llm_calls"])
        assert before.calls == EXPECTED_CALLS_PER_RUN

        await graph.aupdate_state(config, {"llm_calls": list(finished["llm_calls"])})
        state = await graph.aget_state(config)

    assert len(state.values["llm_calls"]) == 2 * EXPECTED_CALLS_PER_RUN, (
        "the duplicates never reached the channel"
    )
    assert UsageSummary.from_calls(state.values["llm_calls"]) == before


async def test_pausing_and_resuming_does_not_change_what_the_run_cost(
    tmp_path: Path,
) -> None:
    """What a checkpointer resume actually does, measured rather than assumed.

    An earlier version of this test claimed a resume would double-count
    without deduplication. That was wrong, and worth recording: measured
    against the pinned LangGraph, `interrupt_before` pauses *between* nodes
    and the completed node is not re-executed on resume -- the same
    invocations, the same records, before and after. There is no duplication
    for the dedup to remove, so an assertion that ids were unique proved
    nothing.

    The property that is genuinely available at this phase is the one
    asserted here: pausing changes *when* the work happens and nothing else.
    The re-execution case that really does bill twice needs `interrupt()`
    inside a node body -- ADR-001 records it, and the rule adopted in response
    (a node that interrupts performs no LLM call before interrupting) belongs
    to Phase 7, along with the test that holds it.
    """
    straight_deps, repo_root, _ = a_graph_environment(
        tmp_path / "straight", responses=a_full_run_script()
    )
    async with open_checkpointer(tmp_path / "a.db") as saver:
        graph = compile_graph(deps=straight_deps, checkpointer=saver)
        straight = await graph.ainvoke(a_state(repo_root, "straight"), a_config("straight"))

    paused_deps, paused_root, model = a_graph_environment(
        tmp_path / "paused", responses=a_full_run_script()
    )
    async with open_checkpointer(tmp_path / "b.db") as saver:
        graph = compile_graph(
            deps=paused_deps, checkpointer=saver, interrupt_before=["generate_plan"]
        )
        await graph.ainvoke(a_state(paused_root, "paused"), a_config("paused"))
        invocations_at_pause = len(model.prompts)
        resumed = await graph.ainvoke(None, a_config("paused"))

    assert invocations_at_pause == EXPECTED_CALLS_PER_RUN, (
        "the pause fell somewhere other than after the last model call"
    )
    assert len(model.prompts) == EXPECTED_CALLS_PER_RUN, "the resume re-invoked the model"

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
    straight_deps, straight_root, _ = a_graph_environment(
        tmp_path / "straight", responses=a_full_run_script()
    )
    async with open_checkpointer(tmp_path / "a.db") as saver:
        graph = compile_graph(deps=straight_deps, checkpointer=saver)
        straight = await graph.ainvoke(a_state(straight_root, "straight"), a_config("straight"))

    paused_deps, paused_root, _ = a_graph_environment(
        tmp_path / "paused", responses=a_full_run_script()
    )
    async with open_checkpointer(tmp_path / "b.db") as saver:
        graph = compile_graph(
            deps=paused_deps, checkpointer=saver, interrupt_before=["generate_plan"]
        )
        await graph.ainvoke(a_state(paused_root, "paused"), a_config("paused"))
        resumed = await graph.ainvoke(None, a_config("paused"))

    def nodes(result: Any) -> list[str]:
        """`ainvoke` is typed `dict[str, Any] | Any` on this overload, so the
        annotation here would be a claim mypy cannot check either way."""
        return [e.node for e in result["agent_trace"] if e.kind is TraceEventKind.NODE_COMPLETED]

    assert nodes(resumed) == nodes(straight)


# -- rule 20: a failing node records, never crashes -------------------------


async def test_a_failing_node_records_an_app_error_and_a_trace_event(
    tmp_path: Path,
) -> None:
    """CLAUDE.md rule 20: a caught exception produces an `AppError` in state
    *and* a trace event, always.

    Enforced once in the node wrapper rather than in each node body, because
    "remember to catch" is the kind of rule that holds for every node except
    the one written in a hurry -- and a node that dies unrecorded takes the
    whole run down with no explanation the user can act on.
    """
    deps, repo_root, _ = a_graph_environment(tmp_path, responses=a_full_run_script())
    async with open_checkpointer(tmp_path / "c.db") as saver:
        graph = compile_graph(
            deps=deps,
            checkpointer=saver,
            fail_in={"analyze_repo": RepoUnavailableError("The repository could not be read.")},
        )

        result = await graph.ainvoke(a_state(repo_root), a_config())

    assert [e.code for e in result["errors"]] == [ErrorCode.REPO_UNAVAILABLE]
    assert result["errors"][0].node == "analyze_repo"
    assert any(e.kind is TraceEventKind.ERROR_RECORDED for e in result["agent_trace"])


async def test_a_failed_analysis_does_not_make_retrieval_claim_a_clean_repository(
    tmp_path: Path,
) -> None:
    """The reason `agentic_rag` is an explicit wrapper rather than a bare
    compiled-graph node.

    An empty symbol inventory means two entirely different things -- "this
    repository does not use the dependency" and "the analysis failed" -- and
    only the parent can tell them apart. Handing the child an empty inventory
    would have it report the first for both, describing a failed run to the
    user as a clean repository.
    """
    deps, repo_root, model = a_graph_environment(tmp_path, responses=a_full_run_script())
    async with open_checkpointer(tmp_path / "c.db") as saver:
        graph = compile_graph(
            deps=deps,
            checkpointer=saver,
            fail_in={"analyze_repo": RepoUnavailableError("nope")},
        )

        result = await graph.ainvoke(a_state(repo_root), a_config())

    decisions = [
        e.summary
        for e in result["agent_trace"]
        if e.kind is TraceEventKind.AGENT_DECISION and e.node == "agentic_rag"
    ]
    assert decisions and "the repository analysis did not complete" in decisions[0]
    assert result["rag_context"] is None
    # Only `assess_risk`'s narrative: the retrieval loop never ran, so it
    # never planned or graded anything.
    assert len(model.prompts) == 1


async def test_a_failing_node_does_not_stop_the_trace_recording_the_rest(
    tmp_path: Path,
) -> None:
    """The run continues so that the report can say what *was* established
    alongside what failed. A run that aborts on the first error throws away
    evidence it had already gathered and paid for."""
    deps, repo_root, _ = a_graph_environment(tmp_path, responses=a_full_run_script())
    async with open_checkpointer(tmp_path / "c.db") as saver:
        graph = compile_graph(
            deps=deps,
            checkpointer=saver,
            fail_in={"analyze_repo": RepoUnavailableError("nope")},
        )

        result = await graph.ainvoke(a_state(repo_root), a_config())

    completed = [e.node for e in result["agent_trace"] if e.kind is TraceEventKind.NODE_COMPLETED]
    assert "finalize" in completed


async def test_an_unexpected_exception_is_recorded_as_internal_not_swallowed(
    tmp_path: Path,
) -> None:
    """A bug in a node body is not a domain error, and must not be reported
    as one. It still has to reach state rather than escaping -- rule 20 says
    nothing is swallowed, not that everything is a known condition."""
    deps, repo_root, _ = a_graph_environment(tmp_path, responses=a_full_run_script())
    async with open_checkpointer(tmp_path / "c.db") as saver:
        graph = compile_graph(
            deps=deps,
            checkpointer=saver,
            fail_in={"assess_risk": TypeError("a bug in the node body")},
        )

        result = await graph.ainvoke(a_state(repo_root), a_config())

    assert [e.code for e in result["errors"]] == [ErrorCode.INTERNAL]
    assert "TypeError" in (result["errors"][0].detail or "")
