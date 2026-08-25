"""`assess_risk` in the graph: what the model is given, and what it cannot touch.

The unit tests in `tests/unit/test_risk_*.py` prove the arithmetic. These
prove the *wiring*: that the node feeds real repository evidence into it, that
the model receives a finished factor set rather than a question, and that a
provider outage costs the narrative and not the verdict.
"""

from pathlib import Path
from typing import Any, cast

from tests.graph.graph_fixtures import (
    COMPLETE_CORPUS,
    OPTIONAL_DOCUMENT,
    a_config,
    a_grade_response,
    a_graph_environment,
    a_narrative_response,
    a_plan_response,
    a_state,
)
from tests.llm.fake_chat_model import ScriptedResponse
from upgradepilot.graph.build import compile_graph
from upgradepilot.graph.checkpointer import open_checkpointer
from upgradepilot.models.enums import RiskLevel, TraceEventKind
from upgradepilot.models.errors import ErrorCode
from upgradepilot.models.risk import RiskAnalysis
from upgradepilot.services.risk.aggregate import NO_EVIDENCE_CEILING

FIXTURE_HIGH_CONFIDENCE = ("BaseModel", "Config", "Optional", "validator")
"""The fixture repository's high-confidence symbols, as the analyzer reports
them. Named here because a query that omits one leaves the gate unsatisfied
and the loop runs its full budget -- which is correct behaviour and a
different test than these."""

COVERED_SCRIPT = [
    a_plan_response(("everything about this upgrade", FIXTURE_HIGH_CONFIDENCE)),
    a_grade_response(sufficient=True),
    a_narrative_response("The upgrade touches validators and model config."),
]
"""One retrieval round that covers the fixture repository, then the narrative.

Scripted responses are consumed in **call order**, so the length of this list
is an assertion in itself: `ScriptedChatModel` raises when it runs out, and a
graph that made an unplanned extra call fails loudly rather than silently
handing one node's answer to another.
"""


async def run(tmp_path: Path, **kwargs: Any) -> RiskAnalysis | None:
    deps, repo_root, _ = a_graph_environment(tmp_path, **kwargs)
    async with open_checkpointer(tmp_path / "c.db") as saver:
        graph = compile_graph(deps=deps, checkpointer=saver)
        result = await graph.ainvoke(a_state(repo_root), a_config())
    # `ainvoke` is typed `dict[str, Any] | Any` on this overload, so the cast
    # states what the graph's own output schema already guarantees.
    return cast(RiskAnalysis | None, result["risk_analysis"])


async def test_the_verdict_is_built_from_real_repository_evidence(tmp_path: Path) -> None:
    """Every factor cites something that exists in the fixture repository, and
    the citations are the analyzer's own file-and-line records rather than
    anything a model produced (CLAUDE.md rule 19)."""
    risk = await run(tmp_path, responses=COVERED_SCRIPT, documents=COMPLETE_CORPUS)

    assert risk is not None
    assert risk.factors, "no factor had evidence, so this proves nothing"
    for factor in risk.factors:
        assert factor.evidence
    repo_files = {
        evidence.file
        for factor in risk.factors
        for evidence in factor.evidence
        if evidence.kind == "repo"
    }
    assert "src/app/models.py" in repo_files


async def test_a_documented_high_severity_break_in_use_clamps_the_verdict(
    tmp_path: Path,
) -> None:
    """The fixture repository uses `@validator`, and the fixture corpus
    documents its removal at high severity. Whatever the weighted factors
    come to, the verdict may not be below that."""
    risk = await run(tmp_path, responses=COVERED_SCRIPT, documents=COMPLETE_CORPUS)

    assert risk is not None
    assert risk.clamp_floor is RiskLevel.HIGH
    assert risk.overall_risk is RiskLevel.HIGH


async def test_the_clamp_is_reported_in_the_trace_when_it_binds(tmp_path: Path) -> None:
    """A verdict raised above what its factors said is a thing the reader
    should be told, not left to infer from two numbers that disagree."""
    deps, repo_root, _ = a_graph_environment(
        tmp_path, responses=COVERED_SCRIPT, documents=COMPLETE_CORPUS
    )
    async with open_checkpointer(tmp_path / "c.db") as saver:
        graph = compile_graph(deps=deps, checkpointer=saver)
        result = await graph.ainvoke(a_state(repo_root), a_config())

    risk = result["risk_analysis"]
    decisions = [
        event.summary
        for event in result["agent_trace"]
        if event.kind is TraceEventKind.AGENT_DECISION and event.node == "assess_risk"
    ]
    if risk.overall_risk is not risk.aggregate_risk:
        assert any("raised to" in summary for summary in decisions)
    assert any("Confidence capped" in summary for summary in decisions) or (
        risk.confidence_ceilings == ()
    )


async def test_the_model_never_sees_a_field_it_could_grade(tmp_path: Path) -> None:
    """`RiskNarrative` carries `summary` and `notes` and nothing else. A field
    the model cannot fill in is a field it cannot get wrong, and the schema is
    the only place that guarantee can be structural."""
    from upgradepilot.graph.nodes.judgment import RiskNarrative

    assert set(RiskNarrative.model_fields) == {"summary", "notes"}


async def test_an_unreachable_model_costs_the_narrative_and_not_the_verdict(
    tmp_path: Path,
) -> None:
    """The analysis is built before the call, so a provider outage degrades
    the prose rather than deleting the risk assessment. Every number is still
    real; only the sentence is machine-assembled, and it says so.
    """
    script = [
        a_plan_response(("everything", FIXTURE_HIGH_CONFIDENCE)),
        a_grade_response(sufficient=True),
        # A response with neither a parsed value nor an error is exactly how a
        # provider returns something the structured-output path cannot use.
        ScriptedResponse(parsed=None, parsing_error="not valid for the schema"),
    ]
    deps, repo_root, _ = a_graph_environment(tmp_path, responses=script, documents=COMPLETE_CORPUS)
    async with open_checkpointer(tmp_path / "c.db") as saver:
        graph = compile_graph(deps=deps, checkpointer=saver)
        result = await graph.ainvoke(a_state(repo_root), a_config())

    risk = result["risk_analysis"]
    assert risk is not None, "the verdict was lost with the narrative"
    assert risk.factors
    assert risk.overall_risk is RiskLevel.HIGH
    assert "No narrative was generated" in risk.summary
    assert [error.code for error in result["errors"]] == [ErrorCode.LLM_UNAVAILABLE]
    assert any(
        event.kind is TraceEventKind.ERROR_RECORDED and event.node == "assess_risk"
        for event in result["agent_trace"]
    )


async def test_a_run_with_no_retrieved_evidence_cannot_be_confident(
    tmp_path: Path,
) -> None:
    """Spec 8.1's hard ceiling, through the graph. The corpus is empty, so the
    retrieval loop runs its budget and finds nothing, and the verdict that
    follows says how little it knows."""
    # A corpus holding one document that names none of this repository's
    # symbols. The symbol filter matches nothing, so every round retrieves
    # zero chunks -- and `evaluate_retrieval` makes no model call at all when
    # there is nothing to grade, which is why the script is three plans and a
    # narrative rather than three plan/grade pairs.
    deps, repo_root, _ = a_graph_environment(
        tmp_path,
        responses=[
            a_plan_response(("anything", ("validator",))),
            a_plan_response(("anything else", ("Config",))),
            a_plan_response(("one more", ("BaseModel",))),
            a_narrative_response(),
        ],
        documents=(OPTIONAL_DOCUMENT,),
        max_rag_iterations=3,
    )
    async with open_checkpointer(tmp_path / "c.db") as saver:
        graph = compile_graph(deps=deps, checkpointer=saver)
        result = await graph.ainvoke(a_state(repo_root), a_config())

    risk = result["risk_analysis"]
    assert risk is not None
    assert risk.confidence <= NO_EVIDENCE_CEILING or any(
        "no documented change" in ceiling.reason for ceiling in risk.confidence_ceilings
    )
    assert risk.confidence < 0.85
