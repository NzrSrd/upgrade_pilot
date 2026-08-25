"""The evidence layer's nodes: analyse, inspect, retrieve.

Spec 8.5's first three nodes. Between them they turn a repository reference
and a dependency name into the two things every later judgement is built on --
a `RepoAnalysis` produced by parsing real source, and a set of
`BreakingChange`s each carrying the corpus chunk it came from.

No model is called in `analyze_repo` or `inspect_dependency` at all. Both are
`ast` and arithmetic over files, which is CLAUDE.md rule 19 made structural:
the file paths, line numbers and version facts this system prints are produced
by a parser, and there is no point in the pipeline where an LLM could have
supplied one.
"""

import asyncio

from upgradepilot.graph.nodes.base import NodeBody, StateUpdate
from upgradepilot.graph.rag.build import compile_rag_graph
from upgradepilot.graph.rag.state import initial_rag_state
from upgradepilot.models.enums import DependencyRole, TraceEventKind
from upgradepilot.models.repo import RepoAnalysis
from upgradepilot.models.state import MigrationState
from upgradepilot.models.trace import trace_event
from upgradepilot.services.analysis.analyzer import analyze_repository
from upgradepilot.services.knowledge.store import DEFAULT_LIMIT, KnowledgeStore
from upgradepilot.services.llm.tracked import TrackedLLM
from upgradepilot.services.repo.manager import WorkspaceManager


def make_analyze_repo(manager: WorkspaceManager) -> NodeBody[MigrationState]:
    """Resolve the repository reference and parse it.

    The workspace is opened and closed inside this one node, which is a
    decision with a consequence worth stating: **no later node can read the
    repository.** That is not a limitation to work around, it is what makes
    the run resumable. A run pauses at `human_review` and may be resumed
    minutes or days later, quite possibly by a different process after a
    restart; a workspace handle cannot survive that, and a remote clone
    re-opened on resume is a *different* checkout of a branch that may have
    moved. Every file, line and version fact the report prints is captured
    here, into state, where the checkpoint preserves it exactly as it was
    read.

    `asyncio.to_thread`, because `analyze_repository` is synchronous and
    walks, reads and `ast.parse`s an entire repository. Called directly it
    would block the event loop for the duration -- fine for one run, and
    exactly wrong for the concurrent runs Phase 9's semaphore permits, where
    one large repository would stall every other run's status poll.
    """

    async def body(state: MigrationState) -> StateUpdate:
        dependency = state["dependency"]

        def analyse() -> RepoAnalysis:
            with manager.open(state["repo_ref"]) as workspace:
                return analyze_repository(workspace, dependency)

        analysis = await asyncio.to_thread(analyse)

        symbols = analysis.symbol_inventory.entries
        summary = (
            f"Analysed {analysis.analyzed_files} of {analysis.total_python_files} Python "
            f"file(s); {len(analysis.affected_files)} use {dependency.name}, across "
            f"{len(symbols)} distinct symbol(s)."
        )
        return {
            "repo_analysis": analysis,
            "affected_files": list(analysis.affected_files),
            "symbol_inventory": analysis.symbol_inventory,
            "summary": summary,
        }

    return body


def make_inspect_dependency() -> NodeBody[MigrationState]:
    """Report what the manifests say about the dependency, and where they disagree.

    Everything here is already in `RepoAnalysis`; what this node adds is
    *visibility*. Three facts decide later behaviour and each is invisible
    unless something says it out loud:

    - the detected version and how firmly it is pinned, which spec 7.1 grades
      `exact` or `range`;
    - a disagreement between the version the user stated and the version the
      repository actually declares, which is spec 8.2's
      `DISCREPANCY_RESOLUTION` decision;
    - a `TRANSITIVE_ONLY` role, which is a hard confidence ceiling in spec 8.1
      -- the repository does not declare this dependency itself, so an upgrade
      is not entirely in its hands.

    None of it is recomputed into a new channel. Deriving `repo_analysis`
    twice is how the trace and the report end up disagreeing about the same
    fact (CLAUDE.md rule 21); this node reads and reports.
    """

    async def body(state: MigrationState) -> StateUpdate:
        analysis = state["repo_analysis"]
        dependency = state["dependency"]

        if analysis is None:
            return {
                "summary": (
                    "Skipped dependency inspection: the repository analysis did not "
                    "complete, so there are no manifests to read."
                )
            }

        events = []
        detected = analysis.detected_version
        if detected is None:
            summary = (
                f"No current version of {dependency.name} could be determined from this "
                f"repository's manifests; the analysis proceeds from the stated version "
                f"{dependency.current_version} alone."
            )
        else:
            summary = (
                f"{dependency.name} is declared in {detected.source_manifest.path} as "
                f"{detected.value} ({detected.confidence.value} confidence, "
                f"{detected.role.value})."
            )
            if detected.role is DependencyRole.TRANSITIVE_ONLY:
                events.append(
                    trace_event(
                        TraceEventKind.AGENT_DECISION,
                        node="inspect_dependency",
                        summary=(
                            f"{dependency.name} is only present transitively: no manifest "
                            "in this repository declares it directly, so upgrading it is "
                            "not wholly under this repository's control."
                        ),
                    )
                )

        discrepancy = analysis.version_discrepancy(dependency.current_version)
        if discrepancy is not None:
            stated, found = discrepancy
            events.append(
                trace_event(
                    TraceEventKind.AGENT_DECISION,
                    node="inspect_dependency",
                    summary=(
                        f"The stated current version ({stated}) does not match the version "
                        f"this repository declares ({found}). Both are reported; neither "
                        "is silently preferred."
                    ),
                )
            )

        for reducer in analysis.confidence_reducers:
            events.append(
                trace_event(
                    TraceEventKind.AGENT_DECISION,
                    node="inspect_dependency",
                    summary=reducer,
                )
            )

        return {"agent_trace": events, "summary": summary}

    return body


def make_agentic_rag(
    *,
    llm: TrackedLLM,
    store: KnowledgeStore,
    max_iterations: int,
    limit: int = DEFAULT_LIMIT,
) -> NodeBody[MigrationState]:
    """Spec 6.4's explicit wrapper around the retrieval subgraph.

    A bare compiled-graph node would work and is refused for two reasons that
    only show up later:

    1. **The channel mapping has to be written down.** The child's shared
       channels start empty and its accumulated values become the parent's
       *delta* -- see `graph/rag/state.py`. Handing the parent's state
       straight to the child would seed the child's `agent_trace` with every
       event so far and return it through the parent's `operator.add`,
       duplicating the whole trace with the run still completing normally.
    2. **The child's inputs need a guard the child cannot express.** An empty
       symbol inventory means two entirely different things -- "this
       repository does not use the dependency" and "the analysis failed" --
       and only the parent can tell them apart. The subgraph would report the
       first for both, so a failed analysis would be described to the user as
       a clean repository.

    Subgraph *failure* is converted to an `AppError` rather than killing the
    run, as spec 6.4 requires, but not by a second try/except here: every
    child node wears `traced`, so a failing node body is already recorded and
    the loop continues degraded, and the `traced` wrapper on this node catches
    anything left. Re-implementing that here would add a path that swallows
    the child's accumulated trace and usage records -- including calls the
    provider has already billed -- which is the one outcome rule 20 exists to
    prevent.
    """
    subgraph = compile_rag_graph(llm=llm, store=store, limit=limit)

    async def body(state: MigrationState) -> StateUpdate:
        if state["repo_analysis"] is None:
            return {
                "agent_trace": [
                    trace_event(
                        TraceEventKind.AGENT_DECISION,
                        node="agentic_rag",
                        summary=(
                            "Skipped knowledge-base retrieval: the repository analysis "
                            "did not complete, so there is no symbol inventory to search "
                            "the corpus with."
                        ),
                    )
                ],
                "summary": "Retrieval skipped: no repository analysis to work from.",
            }

        result = await subgraph.ainvoke(
            initial_rag_state(
                dependency=state["dependency"],
                symbol_inventory=state["symbol_inventory"],
                max_iterations=max_iterations,
            )
        )

        context = result["rag_context"]
        changes = result["breaking_changes"]
        if context is None:  # pragma: no cover - build_context always sets it
            summary = "Retrieval finished without producing a context."
        else:
            summary = (
                f"Retrieval ran {context.iterations} round(s) over "
                f"{context.sources_considered} chunk(s) and stopped because it was "
                f"{context.stop_reason.value.replace('_', ' ')}: "
                f"{len(changes)} documented breaking change(s) apply here, "
                f"{len(context.unknowns)} symbol(s) remain undocumented."
            )

        return {
            "rag_queries": result["rag_queries"],
            "rag_evaluations": result["rag_evaluations"],
            "retrieved_sources": result["retrieved_sources"],
            "llm_calls": result["llm_calls"],
            "agent_trace": result["agent_trace"],
            "errors": result["errors"],
            "breaking_changes": changes,
            "rag_context": context,
            "summary": summary,
        }

    return body
