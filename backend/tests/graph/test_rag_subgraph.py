"""The agentic RAG subgraph: the loop, the gate, and the degradations.

Phase 5's exit criterion is "the agent performs multiple retrieval iterations
when the first result set is insufficient, and can name which sources informed
the outcome." Both halves are asserted here against the real subgraph, a real
Chroma collection and a scripted model -- never against a mock of the store,
because the properties under test (the `$contains` symbol join, dedup by
chunk id, the ordering the merge preserves) are properties of the store and a
mock would let us assume them wrongly.

The fixture corpus documents three of the fixture repository's four
high-confidence symbols and deliberately omits `BaseModel`. That gap is what
makes the deterministic gate observable: with every symbol documented the gate
passes on the first round whatever it does, and a test over such a corpus
cannot tell a working gate from a deleted one.
"""

from pathlib import Path
from typing import Any, cast

import pytest

from tests.graph.graph_fixtures import (
    COMPLETE_CORPUS,
    GRAPH_CORPUS,
    OPTIONAL_DOCUMENT,
    a_grade_response,
    a_knowledge_store,
    a_plan_response,
    a_scripted_model,
    a_tracked_llm,
)
from tests.knowledge.fake_embedding import fake_embedding_function
from tests.llm.fake_chat_model import ScriptedChatModel
from upgradepilot.graph.rag.build import compile_rag_graph, route_after_evaluate
from upgradepilot.graph.rag.state import RAGState, initial_rag_state
from upgradepilot.models.enums import (
    Confidence,
    QueryOrigin,
    RagStopReason,
    TraceEventKind,
)
from upgradepilot.models.inputs import DependencySpec
from upgradepilot.models.knowledge import RagContext
from upgradepilot.models.repo import SymbolInventory, SymbolStat
from upgradepilot.services.knowledge.store import KnowledgeStore

DEPENDENCY = DependencySpec(name="pydantic", current_version="1.10.13", target_version="2.9.0")


def an_inventory(*entries: tuple[str, Confidence]) -> SymbolInventory:
    return SymbolInventory(
        entries=tuple(
            SymbolStat(symbol=symbol, count=1, files=("src/app/models.py",), confidence=confidence)
            for symbol, confidence in entries
        )
    )


FIXTURE_INVENTORY = an_inventory(
    ("BaseModel", Confidence.HIGH),
    ("Config", Confidence.HIGH),
    ("validator", Confidence.HIGH),
)
"""Three high-confidence symbols, one of which (`BaseModel`) the default
corpus does not document."""

COVERED_INVENTORY = an_inventory(
    ("Config", Confidence.HIGH),
    ("validator", Confidence.HIGH),
)
"""Only symbols the default corpus documents, so the gate can pass."""


async def run_loop(
    store: KnowledgeStore,
    responses: list[Any],
    *,
    inventory: SymbolInventory | None,
    max_iterations: int = 3,
) -> tuple[RAGState, ScriptedChatModel]:
    model = a_scripted_model(responses)
    graph = compile_rag_graph(llm=a_tracked_llm(model), store=store)
    result = await graph.ainvoke(
        initial_rag_state(
            dependency=DEPENDENCY,
            symbol_inventory=inventory,
            max_iterations=max_iterations,
        )
    )
    # `ainvoke` is typed `dict[str, Any] | Any` on this overload, so the cast
    # states what the graph's own output schema already guarantees rather than
    # asserting something mypy could check either way.
    return cast(RAGState, result), model


def context_of(result: RAGState) -> RagContext:
    """The loop's context, with the "it ran at all" check spelled out once.

    `build_context` always sets it, so a `None` here is not a nullable field
    to tiptoe around -- it means the terminal node never ran, and a test that
    silently skipped its assertions on that would report a green run over a
    loop that fell over.
    """
    context = result["rag_context"]
    assert context is not None, "build_context did not run"
    return context


@pytest.fixture
def store(tmp_path: Path) -> KnowledgeStore:
    return a_knowledge_store(tmp_path, GRAPH_CORPUS)


@pytest.fixture
def complete_store(tmp_path: Path) -> KnowledgeStore:
    return a_knowledge_store(tmp_path, COMPLETE_CORPUS)


# -- the refinement path: two rounds, and a genuinely different second query --


async def test_an_insufficient_first_round_produces_a_second_refined_query(
    store: KnowledgeStore,
) -> None:
    """Phase 5's headline behaviour, asserted on what was actually *asked*.

    A loop that iterates while issuing the same query twice has learned
    nothing from the first round; the property worth proving is that the
    second round's prompt carries the first round's missing topics, which is
    the only mechanism by which the second query can differ.
    """
    responses = [
        a_plan_response(("validator decorator rename", ("validator",))),
        a_grade_response(sufficient=False, missing=("BaseModel method renames",)),
        a_plan_response(("BaseModel method renames", ("BaseModel",))),
        a_grade_response(sufficient=True),
    ]

    result, model = await run_loop(store, responses, inventory=COVERED_INVENTORY)

    issued = [query.text for query in result["rag_queries"]]
    assert issued == ["validator decorator rename", "BaseModel method renames"]
    assert [query.iteration for query in result["rag_queries"]] == [1, 2]
    # The refinement is driven by the previous evaluation's missing topics, so
    # the second planning prompt must contain them. Without this the loop
    # could iterate while asking the model exactly the same question.
    assert "BaseModel method renames" in model.prompts[2]


async def test_the_second_round_keeps_the_first_rounds_evidence(
    store: KnowledgeStore,
) -> None:
    """Candidates accumulate across rounds rather than being replaced.

    Without a reducer this channel is last-value, so round two's results
    delete round one's -- and the coverage gate, which grades everything
    retrieved so far, would judge the final round in isolation. A loop that
    found `validator` in round one and `Config` in round two would then
    conclude, correctly for its inputs and wrongly for the run, that
    `validator` was never covered.
    """
    responses = [
        a_plan_response(("validator rename", ("validator",))),
        a_grade_response(sufficient=False, missing=("config",)),
        a_plan_response(("config class replacement", ("Config",))),
        a_grade_response(sufficient=True),
    ]

    result, _ = await run_loop(store, responses, inventory=COVERED_INVENTORY)

    covered = {chunk.source_id for chunk in result["candidates"]}
    assert covered == {"pydantic-v2#validator", "pydantic-v2#config-class"}
    assert result["rag_evaluations"][-1].candidates_considered == 2


# -- the gate overriding a falsely-sufficient model --------------------------


async def test_the_gate_overrides_a_model_that_declared_victory(
    store: KnowledgeStore,
) -> None:
    """Spec 7.3's deterministic override, end to end.

    The model says `sufficient=True` on every round. `BaseModel` is a
    high-confidence symbol that nothing in the corpus documents, so the gate
    refuses -- and the loop runs its full budget instead of stopping at round
    one.
    """
    responses = []
    for _ in range(3):
        responses.append(a_plan_response(("anything at all", ("BaseModel", "Config", "validator"))))
        responses.append(a_grade_response(sufficient=True))

    result, _ = await run_loop(store, responses, inventory=FIXTURE_INVENTORY)

    evaluations = result["rag_evaluations"]
    assert len(evaluations) == 3, "the gate did not force further rounds"
    assert all(evaluation.model_sufficient for evaluation in evaluations)
    assert not any(evaluation.gate_sufficient for evaluation in evaluations)
    assert not any(evaluation.sufficient for evaluation in evaluations)
    assert evaluations[-1].uncovered_high_confidence == ("BaseModel",)


async def test_the_override_is_visible_in_the_trace(store: KnowledgeStore) -> None:
    """A gate that silently disagreed with the model would be indistinguishable
    from a slow loop. The trace has to say which of the two stopped it."""
    responses = [
        a_plan_response(("anything", ("BaseModel", "Config", "validator"))),
        a_grade_response(sufficient=True),
    ]

    result, _ = await run_loop(store, responses, inventory=FIXTURE_INVENTORY, max_iterations=1)

    evaluated = [
        event for event in result["agent_trace"] if event.kind is TraceEventKind.RETRIEVAL_EVALUATED
    ]
    assert len(evaluated) == 1
    assert "the coverage grade said sufficient" in evaluated[0].summary
    assert evaluated[0].detail == "BaseModel"


async def test_a_covered_inventory_lets_the_loop_stop_at_one_round(
    complete_store: KnowledgeStore,
) -> None:
    """The complement, and the test that stops the one above passing
    vacuously: with every high-confidence symbol documented and the model
    agreeing, one round is enough."""
    responses = [
        a_plan_response(("everything about this upgrade", ("BaseModel", "Config", "validator"))),
        a_grade_response(sufficient=True),
    ]

    result, _ = await run_loop(complete_store, responses, inventory=FIXTURE_INVENTORY)

    assert len(result["rag_evaluations"]) == 1
    assert result["rag_evaluations"][0].sufficient is True
    assert context_of(result).stop_reason is RagStopReason.SUFFICIENT


# -- the iteration cutoff ---------------------------------------------------


async def test_the_loop_stops_at_the_configured_iteration_bound(
    store: KnowledgeStore,
) -> None:
    responses = []
    for _ in range(2):
        responses.append(a_plan_response(("anything", ("validator",))))
        responses.append(a_grade_response(sufficient=False))

    result, model = await run_loop(store, responses, inventory=FIXTURE_INVENTORY, max_iterations=2)

    assert result["iteration"] == 2
    assert context_of(result).stop_reason is RagStopReason.ITERATION_LIMIT
    assert context_of(result).sufficient is False
    # The scripted model raises when it runs out, so an unbounded loop fails
    # loudly here rather than hanging.
    assert model.responses == []


def test_the_bound_is_a_floor_as_well_as_a_ceiling() -> None:
    """`>=`, not `==`. `max_iterations` arrives from configuration, and a
    zero must stop the loop rather than run it forever looking for an
    equality it will never reach."""
    state = initial_rag_state(
        dependency=DEPENDENCY, symbol_inventory=FIXTURE_INVENTORY, max_iterations=0
    )
    state["iteration"] = 1

    assert route_after_evaluate(state) == "build_context"


# -- retrieval that was never necessary -------------------------------------


async def test_an_empty_inventory_skips_retrieval_without_calling_the_model(
    store: KnowledgeStore,
) -> None:
    """Spec 7.3: zero usage sites means skip, "recorded as an explicit
    decision rather than a silent no-op".

    The model is scripted with **no** responses, so any call at all raises.
    That is the assertion that matters: the skip must be arithmetic, not a
    question put to an LLM that could answer either way.
    """
    result, model = await run_loop(store, [], inventory=SymbolInventory())

    assert model.prompts == [], "the model was consulted about whether to search"
    context = context_of(result)
    assert context.stop_reason is RagStopReason.NOT_NECESSARY
    assert context.iterations == 0
    assert context.evidence_available is False
    decisions = [
        event for event in result["agent_trace"] if event.kind is TraceEventKind.AGENT_DECISION
    ]
    assert len(decisions) == 1
    assert "no detected use of pydantic" in decisions[0].summary


# -- KB_UNAVAILABLE degradation ---------------------------------------------


async def test_an_unreachable_store_degrades_instead_of_killing_the_run(
    tmp_path: Path,
) -> None:
    """Spec 7.3: Chroma unreachable becomes `AppError(KB_UNAVAILABLE)` and an
    empty context flagged `evidence_available: False`, which spec 8.1 turns
    into a hard confidence ceiling.

    The collection is dropped after the store is opened, which is the shape a
    real outage takes from the caller's side: the handle is valid and the
    query fails.
    """
    store = KnowledgeStore.open(tmp_path / "chroma", embedding_function=fake_embedding_function())
    store.ingest(GRAPH_CORPUS)
    store.drop()

    responses = [a_plan_response(("anything", ("validator",)))]
    result, model = await run_loop(store, responses, inventory=FIXTURE_INVENTORY)

    assert [error.code.value for error in result["errors"]] == ["kb_unavailable"]
    assert any(event.kind is TraceEventKind.ERROR_RECORDED for event in result["agent_trace"]), (
        "rule 20: an error in state without a trace event is half a record"
    )
    context = context_of(result)
    assert context.stop_reason is RagStopReason.KB_UNAVAILABLE
    assert context.evidence_available is False
    assert context.sufficient is False
    assert result["breaking_changes"] == []
    # One planning call, and no second round: a knowledge base that is down
    # for query one is down for query two.
    assert len(model.prompts) == 1


# -- what the model is not allowed to do ------------------------------------


async def test_a_symbol_the_model_invented_never_reaches_the_store(
    store: KnowledgeStore,
) -> None:
    """`$contains` is exact-element, so an invented symbol matches no
    document -- and an `$or` filter of one real symbol and three invented ones
    narrows to the real one while looking like a four-symbol search. The
    recorded query is what a reader checks the retrieval against, so it must
    show what was actually sent."""
    responses = [
        a_plan_response(("validator rename", ("validator", "make_believe", "AlsoFake"))),
        a_grade_response(sufficient=True),
    ]

    result, _ = await run_loop(store, responses, inventory=COVERED_INVENTORY, max_iterations=1)

    assert result["rag_queries"][0].symbols == ("validator",)


async def test_a_model_that_proposes_nothing_usable_falls_back_to_a_real_query(
    store: KnowledgeStore,
) -> None:
    """A round that retrieves nothing produces an empty evidence set, and an
    empty evidence set is indistinguishable in the final report from a corpus
    that documents nothing. The fallback is what keeps those two apart."""
    responses = [a_plan_response(), a_grade_response(sufficient=True)]

    result, _ = await run_loop(store, responses, inventory=COVERED_INVENTORY)

    query = result["rag_queries"][0]
    assert query.origin is QueryOrigin.FALLBACK
    assert "pydantic" in query.text
    assert set(query.symbols) == {"Config", "validator"}
    assert result["candidates"], "the fallback query retrieved nothing"


async def test_a_blank_query_is_dropped_rather_than_embedded(
    store: KnowledgeStore,
) -> None:
    """An empty query embeds to a meaningless vector and returns the
    collection's arbitrary nearest neighbours, which then enter the evidence
    set as retrieved documents."""
    responses = [
        a_plan_response(("   ", ("validator",)), ("config replacement", ("Config",))),
        a_grade_response(sufficient=True),
    ]

    result, _ = await run_loop(store, responses, inventory=COVERED_INVENTORY, max_iterations=1)

    assert [query.text for query in result["rag_queries"]] == ["config replacement"]


# -- build_context: nothing is paraphrased ----------------------------------


async def test_every_breaking_change_quotes_the_chunk_it_cites(
    complete_store: KnowledgeStore,
) -> None:
    """CLAUDE.md rule 1 made checkable. The description is the retrieved
    chunk's own text and the `SourceRef` names that exact chunk, so following
    a citation lands on the passage the change was built from."""
    responses = [
        a_plan_response(("everything", ("BaseModel", "Config", "validator"))),
        a_grade_response(sufficient=True),
    ]

    result, _ = await run_loop(complete_store, responses, inventory=FIXTURE_INVENTORY)

    by_chunk = {chunk.chunk_id: chunk for chunk in result["candidates"]}
    assert result["breaking_changes"], "nothing was built, so this proves nothing"
    for change in result["breaking_changes"]:
        chunk = by_chunk[change.source.chunk_id]
        assert change.description == chunk.text
        assert change.source.source_id == change.id
        assert change.old_form is None and change.new_form is None


async def test_a_document_naming_no_symbol_this_repository_uses_is_not_a_breaking_change(
    tmp_path: Path,
) -> None:
    """It was consulted, and the trace says so -- but it is not asserted as a
    change affecting this codebase. A report that lists a documented break for
    an API the repository never touches is a false finding wearing a real
    citation."""
    store = a_knowledge_store(tmp_path, (OPTIONAL_DOCUMENT,))
    responses = [
        a_plan_response(("optional fields no longer default", ())),
        a_grade_response(sufficient=True),
    ]

    result, _ = await run_loop(store, responses, inventory=COVERED_INVENTORY, max_iterations=1)

    assert result["candidates"], "nothing was retrieved, so this proves nothing"
    assert {chunk.source_id for chunk in result["candidates"]} == {
        "pydantic-v2#optional-no-default"
    }
    assert result["breaking_changes"] == []
    assert [source.source_id for source in result["retrieved_sources"]] == [
        "pydantic-v2#optional-no-default"
    ]


async def test_uncovered_symbols_become_unknowns_not_breaking_changes(
    store: KnowledgeStore,
) -> None:
    responses = [
        a_plan_response(("validator rename", ("validator",))),
        a_grade_response(sufficient=False),
    ]

    result, _ = await run_loop(store, responses, inventory=FIXTURE_INVENTORY, max_iterations=1)

    context = context_of(result)
    assert "BaseModel" in context.unknowns
    assert "BaseModel" not in {
        symbol for change in result["breaking_changes"] for symbol in change.affected_symbols
    }
