"""A fully wired graph over real components, for the graph-path tests.

Every dependency here is the production class, not a mock, with exactly two
substitutions -- and both are the ones spec 11 names:

- the chat model is `ScriptedChatModel`, so the paths under test (refinement,
  the gate's override, interrupt and resume) run the same way every time;
- the embedding function is the offline `LexicalEmbedding`, so unit tests
  touch no network.

Everything else is real: a real `AsyncSqliteSaver`, a real Chroma collection
holding real corpus documents, a real `WorkspaceManager` over a real git
repository with real commits, and the real analyzer parsing it. A graph test
that stubbed those would prove the wiring and nothing about whether the wiring
carries anything.
"""

from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.types import Command

from tests.fixtures.repo_builder import build_sample_repo
from tests.knowledge.fake_embedding import fake_embedding_function
from tests.llm.fake_chat_model import ScriptedChatModel, ScriptedResponse
from upgradepilot.config import ModelPrice, Settings
from upgradepilot.graph.deps import GraphDeps
from upgradepilot.graph.nodes.judgment import RiskNarrative
from upgradepilot.graph.rag.nodes import CoverageGrade, PlannedQuery, RetrievalPlan
from upgradepilot.models.enums import Severity, SourceType
from upgradepilot.models.inputs import (
    DependencySpec,
    LocalRepoRef,
    RepoRef,
    UserConstraints,
)
from upgradepilot.models.knowledge import CorpusDocument
from upgradepilot.models.state import MigrationState, initial_state
from upgradepilot.services.knowledge.store import KnowledgeStore
from upgradepilot.services.llm.tracked import TrackedLLM
from upgradepilot.services.repo.manager import WorkspaceManager

MODEL_NAME = "scripted-model"
PRICING = {MODEL_NAME: ModelPrice(input_per_1m=1.0, output_per_1m=2.0)}


def a_document(
    source_id: str,
    *,
    title: str,
    body: str,
    symbols: tuple[str, ...],
    severity: Severity = Severity.HIGH,
    source_type: SourceType = SourceType.MIGRATION_GUIDE,
) -> CorpusDocument:
    return CorpusDocument(
        source_id=source_id,
        title=title,
        source_type=source_type,
        dependency="pydantic",
        from_version="1.x",
        to_version="2.0",
        to_version_major=2,
        affected_symbols=symbols,
        severity=severity,
        url_or_reference=f"https://example.invalid/{source_id}",
        created_at=date(2026, 8, 25),
        body=body,
        path=f"{source_id}.md",
    )


VALIDATOR_DOCUMENT = a_document(
    "pydantic-v2#validator",
    title="@validator is superseded by @field_validator",
    body=(
        "The validator decorator is replaced by field_validator. The V1 "
        "spelling still imports but is deprecated, and the signature changed."
    ),
    symbols=("validator", "field_validator"),
)

CONFIG_DOCUMENT = a_document(
    "pydantic-v2#config-class",
    title="class Config is replaced by model_config",
    body=(
        "A nested class Config is replaced by a model_config ConfigDict "
        "assignment. Unknown config keys now raise."
    ),
    symbols=("Config", "ConfigDict"),
)

OPTIONAL_DOCUMENT = a_document(
    "pydantic-v2#optional-no-default",
    title="Optional no longer implies a default of None",
    body=(
        "In V1 an Optional annotation made the field default to None. In V2 "
        "an Optional field with no default is required."
    ),
    symbols=("Optional",),
    severity=Severity.MEDIUM,
)

GRAPH_CORPUS: tuple[CorpusDocument, ...] = (
    VALIDATOR_DOCUMENT,
    CONFIG_DOCUMENT,
    OPTIONAL_DOCUMENT,
)
"""Three documents covering three of the fixture repository's high-confidence
symbols, and deliberately not the fourth (`BaseModel`).

That gap is load-bearing rather than an oversight: it is what makes the
deterministic sufficiency gate observable in a graph test. With every symbol
documented the gate passes on the first round whatever it does, so a test over
this corpus would be unable to tell a working gate from a deleted one.
"""


def a_knowledge_store(tmp_path: Path, documents: Sequence[CorpusDocument] = ()) -> KnowledgeStore:
    """A real Chroma collection under `tmp_path`, ingested and ready."""
    store = KnowledgeStore.open(tmp_path / "chroma", embedding_function=fake_embedding_function())
    store.ingest(tuple(documents) if documents else GRAPH_CORPUS)
    return store


def a_workspace_manager(repo_root: Path) -> WorkspaceManager:
    """A manager allowed to open exactly the fixture repository's parent.

    `allowed_local_roots` is the security boundary, so the fixture sets it
    rather than disabling it: a test that opened a repository the allowlist
    forbids would be exercising a configuration nothing ships.
    """
    return WorkspaceManager(Settings(_env_file=None, allowed_local_roots=(repo_root.parent,)))


def a_scripted_model(responses: Sequence[ScriptedResponse] = ()) -> ScriptedChatModel:
    return ScriptedChatModel(responses=list(responses))


def a_tracked_llm(model: ScriptedChatModel) -> TrackedLLM:
    return TrackedLLM(model, model_name=MODEL_NAME, pricing=PRICING)


def a_graph_environment(
    tmp_path: Path,
    *,
    responses: Sequence[ScriptedResponse] = (),
    documents: Sequence[CorpusDocument] = (),
    max_rag_iterations: int = 3,
) -> tuple[GraphDeps, Path, ScriptedChatModel]:
    """Build the deps, the repository, and the model the test scripts.

    Returns the model as well as the deps because half of what a graph test
    needs to assert is *what the graph asked for* -- that a refinement round
    issued a different query rather than repeating the first -- and that lives
    on `ScriptedChatModel.prompts`.
    """
    repo_root = build_sample_repo(tmp_path)
    model = a_scripted_model(responses)
    deps = GraphDeps(
        llm=a_tracked_llm(model),
        store=a_knowledge_store(tmp_path, documents),
        workspaces=a_workspace_manager(repo_root),
        max_rag_iterations=max_rag_iterations,
    )
    return deps, repo_root, model


def a_state(repo_root: Path, thread_id: str = "t-1") -> MigrationState:
    ref: RepoRef = LocalRepoRef(path=str(repo_root))
    return initial_state(
        thread_id=thread_id,
        repo_ref=ref,
        dependency=DependencySpec(
            name="pydantic", current_version="1.10.13", target_version="2.9.0"
        ),
        constraints=UserConstraints(),
    )


def a_config(thread_id: str = "t-1") -> RunnableConfig:
    return {"configurable": {"thread_id": thread_id}}


BASEMODEL_DOCUMENT = a_document(
    "pydantic-v2#basemodel-methods",
    title="BaseModel gains model_ prefixed methods",
    body=(
        "BaseModel's dict, copy, schema and parse_obj methods are renamed to "
        "model_dump, model_copy, model_json_schema and model_validate."
    ),
    symbols=("BaseModel", "dict", "copy", "schema", "parse_obj"),
)
"""The document `GRAPH_CORPUS` deliberately leaves out.

Adding it closes the fixture repository's only high-confidence coverage gap,
which is what a test needs in order to reach the *sufficient* branch of the
loop. Kept separate so the default corpus keeps the gap: a fixture where the
gate always passes cannot show the gate working.
"""

COMPLETE_CORPUS: tuple[CorpusDocument, ...] = (*GRAPH_CORPUS, BASEMODEL_DOCUMENT)


def a_plan_response(
    *queries: tuple[str, tuple[str, ...]],
    **overrides: object,
) -> ScriptedResponse:
    """One scripted answer for `plan_retrieval`.

    Each query is `(text, symbols)`. Passing no query is a real case rather
    than a degenerate one: it is how a test drives the deterministic fallback
    that runs when the model proposes nothing usable.
    """
    plan = RetrievalPlan(
        queries=[
            PlannedQuery(text=text, symbols=list(symbols), rationale=f"looking for {text}")
            for text, symbols in queries
        ]
    )
    return ScriptedResponse(parsed=plan, text="planned", **overrides)


def a_grade_response(
    *, sufficient: bool, missing: tuple[str, ...] = (), **overrides: object
) -> ScriptedResponse:
    """One scripted answer for `evaluate_retrieval`."""
    grade = CoverageGrade(
        sufficient=sufficient,
        missing_topics=list(missing),
        notes="scripted grade",
    )
    return ScriptedResponse(parsed=grade, text="graded", **overrides)


def a_narrative_response(
    summary: str = "A scripted risk narrative.",
    notes: tuple[str, ...] = (),
    **overrides: object,
) -> ScriptedResponse:
    """One scripted answer for `assess_risk`'s narrative.

    The schema carries prose only -- no level, no confidence -- which is the
    guarantee the node relies on: a field the model cannot fill in is a field
    it cannot get wrong.
    """
    return ScriptedResponse(parsed=RiskNarrative(summary=summary, notes=list(notes)), **overrides)


def a_full_run_script(
    *, rounds: int = 3, model_says_sufficient: bool = False
) -> list[ScriptedResponse]:
    """Responses for one complete run, in the order the graph asks for them.

    The order is the topology: each retrieval round is a plan followed by a
    grade, and `assess_risk`'s narrative comes last. Scripting exactly the
    expected number matters -- `ScriptedChatModel` raises rather than looping
    when it runs out, so a graph that made an unplanned extra call fails the
    test loudly instead of silently reusing an answer meant for another node.
    """
    script: list[ScriptedResponse] = []
    for _ in range(rounds):
        script.append(
            a_plan_response(
                ("breaking changes in this upgrade", ("validator", "Config", "BaseModel"))
            )
        )
        script.append(
            a_grade_response(sufficient=model_says_sufficient, missing=("BaseModel changes",))
        )
    script.append(a_narrative_response())
    return script


async def answer_all(
    graph: Any,
    config: RunnableConfig,
    result: Any,
    *,
    answers: Sequence[object] = (),
    max_resumes: int = 8,
) -> Any:
    """Resume an already-started run until it stops asking.

    Split from `run_to_completion` because a test that starts the run itself
    -- to assert something about the first pause -- still needs to finish it,
    and re-invoking with the initial state would start a second run rather
    than continue this one.
    """
    queued = list(answers)
    for _ in range(max_resumes):
        interrupts = result.get("__interrupt__") if isinstance(result, dict) else None
        if not interrupts:
            return result
        payload = interrupts[0].value
        answer: object = (
            queued.pop(0) if queued else (payload.recommendation_id or payload.options[0].id)
        )
        result = await graph.ainvoke(Command(resume=answer), config)

    raise AssertionError(f"the graph was still asking questions after {max_resumes} resumes")


async def run_to_completion(
    graph: Any,
    state: MigrationState,
    config: RunnableConfig,
    *,
    answers: Sequence[object] = (),
    max_resumes: int = 8,
) -> Any:
    """Invoke the graph and answer every question it stops on.

    The fixture repository plus the fixture corpus produces a genuine
    tradeoff, so a straight `ainvoke` pauses rather than finishing -- which is
    the product working, and a nuisance for the tests that are about
    something else. This helper is the shape a caller (and Phase 9's API)
    actually uses: invoke, and while the run reports an interrupt, resume with
    an answer.

    `answers` supplies scripted resume values in order; once they run out,
    each remaining question is answered with its own `recommendation_id`. A
    test that cares *which* option was chosen passes them; a test that only
    needs the run to finish does not.

    `max_resumes` is a test-side guard, not a product limit: a bug that made
    the graph re-ask the same question forever would otherwise hang the suite
    rather than fail it.
    """
    result = await graph.ainvoke(state, config)
    return await answer_all(graph, config, result, answers=answers, max_resumes=max_resumes)
