"""The four nodes of spec 7.3's retrieval loop.

    plan_retrieval -> retrieve -> evaluate_retrieval -> [sufficient?]
          ^                                              |  no
          +----------------------------------------------+
                                                         |  yes
                                                         v
                                                   build_context

Three principles run through all four, and they are what separate this from a
retrieval loop that produces confident prose:

1. **The model proposes; this module disposes.** `plan_retrieval` asks an LLM
   for query text and candidate symbols, then intersects those symbols with
   the repository's real inventory before sending them. A symbol the model
   invented never reaches Chroma and never appears in the recorded query.
2. **The gate is arithmetic.** `evaluate_retrieval` asks the model to grade
   coverage and then overrides it with `annotate_coverage`, which the model
   cannot argue with. Spec 7.3.
3. **Nothing is paraphrased.** `build_context` builds each `BreakingChange`
   out of the retrieved chunk's own text and metadata, with a `SourceRef`
   naming the exact chunk quoted. No model writes a description here, so
   there is nothing in a breaking change that a reader cannot check against
   the document it cites.

CLAUDE.md rule 20 is honoured in two different registers, deliberately. A
failure that ends the step -- an unexpected exception -- is caught by the
`traced` wrapper each node wears. A failure the loop can *continue past* -- the
model provider being down, the knowledge base being unreachable -- is caught
here in the body, recorded as an `AppError` plus a trace event, and the loop
carries on degraded. The second kind must not be allowed to abort the run:
the whole point of `evidence_available` and the confidence ceiling above it is
that a run with no evidence still produces an honest report saying so.
"""

import uuid

from pydantic import BaseModel, Field

from upgradepilot.graph.nodes.base import NodeBody, StateUpdate
from upgradepilot.graph.rag.state import RAGState
from upgradepilot.models.enums import (
    Confidence,
    QueryOrigin,
    RagStopReason,
    TraceEventKind,
)
from upgradepilot.models.errors import UpgradePilotError
from upgradepilot.models.evidence import BreakingChange
from upgradepilot.models.inputs import DependencySpec
from upgradepilot.models.knowledge import (
    RagContext,
    RagEvaluation,
    RagQuery,
    RetrievedChunk,
)
from upgradepilot.models.repo import SymbolInventory
from upgradepilot.models.trace import TraceEvent, trace_event
from upgradepilot.services.knowledge.coverage import annotate_coverage
from upgradepilot.services.knowledge.store import DEFAULT_LIMIT, KnowledgeStore
from upgradepilot.services.llm.tracked import TrackedLLM

MAX_QUERIES_PER_ITERATION = 3
"""How many queries one round may issue.

A bound rather than a preference: each query is a store round-trip that embeds
its own text, so an unbounded list from the model is an unbounded cost driven
by untrusted output. Three is enough for the shape the loop actually needs --
one broad query plus a couple aimed at specific symbols -- and a model that
wants more gets another iteration, which is bounded separately.
"""

MAX_FALLBACK_SYMBOLS = 8
"""Symbols named in a fallback query's `$contains` filter.

The filter is an `$or`, so every extra symbol widens the result set; past a
handful the query stops narrowing anything and simply costs more. Ordered
high-confidence-first, so the cut keeps the symbols the analyzer is surest
about.
"""


# -- what the model is asked for, and nothing more --------------------------
#
# These schemas live here rather than in `upgradepilot.models` on purpose.
# They are prompt contracts, not domain types: they never enter graph state,
# never get checkpointed, and nothing downstream may consume one directly.
# Every value that reaches state passes through the validation below first.


class PlannedQuery(BaseModel):
    """One query the model proposes. Not yet a `RagQuery`."""

    text: str = Field(default="")
    symbols: list[str] = Field(default_factory=list)
    rationale: str = Field(default="")


class RetrievalPlan(BaseModel):
    queries: list[PlannedQuery] = Field(default_factory=list)


class CoverageGrade(BaseModel):
    """The model's opinion of retrieval quality. Overridden by the gate."""

    sufficient: bool = False
    missing_topics: list[str] = Field(default_factory=list)
    notes: str = Field(default="")


# -- prompts ----------------------------------------------------------------


def _inventory_lines(inventory: SymbolInventory) -> str:
    return "\n".join(
        f"- {stat.symbol} (confidence: {stat.confidence.value}, "
        f"{stat.count} use(s) in {len(stat.files)} file(s))"
        for stat in inventory.entries
    )


def _plan_prompt(
    dependency: DependencySpec,
    inventory: SymbolInventory,
    *,
    iteration: int,
    missing_topics: tuple[str, ...],
) -> str:
    """Build the query-planning prompt.

    The symbol inventory handed over is the *real* one, produced by `ast`
    parsing of the repository -- spec 7.3 is explicit that the model sees the
    actual inventory rather than a description of one, because a model asked
    to guess which symbols a codebase uses will guess the dependency's most
    famous ones.
    """
    parts = [
        "You are planning searches against a knowledge base of documented breaking changes.",
        "",
        f"Dependency: {dependency.name}",
        f"Current version: {dependency.current_version}",
        f"Target version: {dependency.target_version}",
        "",
        "Symbols this repository actually uses, found by parsing its source:",
        _inventory_lines(inventory),
        "",
        f"This is retrieval round {iteration}.",
    ]
    if missing_topics:
        parts += [
            "",
            "The previous round graded these topics as still missing:",
            *(f"- {topic}" for topic in missing_topics),
            "",
            "Write queries that target those gaps rather than repeating the "
            "previous round's searches.",
        ]
    parts += [
        "",
        f"Propose at most {MAX_QUERIES_PER_ITERATION} search queries. For each, give:",
        "- text: what to search for, in natural language",
        "- symbols: which of the listed symbols the query is about (use the "
        "exact spellings above; any symbol not in the list is discarded)",
        "- rationale: one short sentence, written for a developer reading an "
        "activity log, saying why this search is worth running",
    ]
    return "\n".join(parts)


def _grade_prompt(
    dependency: DependencySpec,
    candidates: list[RetrievedChunk],
    *,
    uncovered: tuple[str, ...],
) -> str:
    documents = "\n".join(
        f"- {chunk.source_id} ({chunk.title}) covering "
        f"{', '.join(chunk.affected_symbols) or 'no named symbol'}"
        for chunk in candidates
    )
    parts = [
        f"Retrieval so far for the {dependency.name} "
        f"{dependency.current_version} -> {dependency.target_version} upgrade "
        "returned these documents:",
        documents,
        "",
        "Judge whether this is enough documented evidence to explain the "
        "upgrade for the symbols this repository uses.",
    ]
    if uncovered:
        parts += [
            "",
            "These symbols have no document naming them:",
            *(f"- {symbol}" for symbol in uncovered),
        ]
    parts += [
        "",
        "Answer with:",
        "- sufficient: true only if the evidence explains what a developer must change",
        "- missing_topics: short phrases naming what is still unexplained",
        "- notes: one sentence for the activity log",
    ]
    return "\n".join(parts)


# -- helpers ----------------------------------------------------------------


def _degraded(node: str, exc: UpgradePilotError, *, consequence: str) -> StateUpdate:
    """Record a recoverable failure without ending the run.

    Spec 9.3 splits every node failure into recoverable and fatal, and both
    halves of CLAUDE.md rule 20 apply to the recoverable half too: an
    `AppError` in state *and* a trace event, always. What makes this
    "degraded" rather than "handled" is `consequence` -- the sentence saying
    what the run lost, which is the part a reader needs and the part a bare
    `except` would delete.
    """
    return {
        "errors": [exc.to_app_error(node=node)],
        "agent_trace": [
            trace_event(
                TraceEventKind.ERROR_RECORDED,
                node=node,
                summary=f"{exc.message} {consequence}",
                detail=exc.detail,
            )
        ],
    }


def _fallback_query(
    dependency: DependencySpec, inventory: SymbolInventory, *, iteration: int
) -> RagQuery:
    """The query this loop issues when the model gives it nothing usable.

    Deterministic and mechanical: the dependency, both versions, and the
    symbols the analyzer is surest about. It is a worse query than a good
    model would write, and it is far better than no query -- a round that
    retrieves nothing produces an empty evidence set, and an empty evidence
    set is indistinguishable in the final report from a corpus that documents
    nothing.

    Marked `QueryOrigin.FALLBACK` so the trace says which one ran. A generic
    query text is otherwise the only clue that the model returned nothing,
    and it is not one a reader would notice.
    """
    ranked = sorted(
        inventory.entries,
        key=lambda stat: (stat.confidence is not Confidence.HIGH, -stat.count, stat.symbol),
    )
    symbols = tuple(stat.symbol for stat in ranked[:MAX_FALLBACK_SYMBOLS])
    return RagQuery(
        query_id=str(uuid.uuid4()),
        iteration=iteration,
        text=(
            f"{dependency.name} {dependency.current_version} to "
            f"{dependency.target_version} breaking changes affecting "
            f"{', '.join(symbols)}"
        ),
        symbols=symbols,
        to_version_major=dependency.target_major,
        rationale=(
            "Composed from the symbol inventory because the model proposed no "
            "usable query for this round."
        ),
        origin=QueryOrigin.FALLBACK,
    )


def _accept_planned(
    planned: PlannedQuery,
    dependency: DependencySpec,
    known_symbols: frozenset[str],
    *,
    iteration: int,
) -> RagQuery | None:
    """Turn one proposed query into a real one, or reject it.

    Two filters, and each closes a way the model can degrade retrieval
    without anything looking wrong:

    - **Blank text is rejected.** An empty query embeds to a meaningless
      vector and returns the collection's arbitrary nearest neighbours, which
      then enter the evidence set as retrieved documents.
    - **Symbols are intersected with the inventory.** `$contains` is
      exact-element, so an invented symbol matches no document -- and an `$or`
      filter containing one real symbol and three invented ones narrows to the
      real one while looking like a four-symbol search. Worse, a query whose
      symbols are *all* invented matches nothing at all and returns an empty
      result the loop would read as "the corpus documents nothing".

    A rejected rationale is replaced rather than rejecting the whole query:
    the rationale is a label for the reader, not part of the search, so
    discarding a good query because its explanation was blank would cost
    evidence to fix a caption.
    """
    text = planned.text.strip()
    if not text:
        return None

    symbols = tuple(sorted({s.strip() for s in planned.symbols if s.strip()} & known_symbols))
    rationale = planned.rationale.strip() or (
        "Planned for this round; the model supplied no rationale."
    )
    return RagQuery(
        query_id=str(uuid.uuid4()),
        iteration=iteration,
        text=text,
        symbols=symbols,
        to_version_major=dependency.target_major,
        rationale=rationale,
    )


def _latest_missing_topics(state: RAGState) -> tuple[str, ...]:
    evaluations = state["rag_evaluations"]
    return evaluations[-1].missing_topics if evaluations else ()


# -- the nodes --------------------------------------------------------------


def make_plan_retrieval(llm: TrackedLLM) -> NodeBody[RAGState]:
    """Query generation, and the deterministic decision to skip.

    Spec 7.3: "It also decides whether retrieval is warranted at all: zero
    usage sites means skip, recorded as an explicit decision rather than a
    silent no-op." That decision is made here by counting, before any model
    call -- see `RAGState.retrieval_necessary` for why the model is not asked.
    """

    async def body(state: RAGState) -> StateUpdate:
        dependency = state["dependency"]
        inventory = state["symbol_inventory"]
        iteration = state["iteration"] + 1

        if inventory is None or not inventory.entries:
            return {
                "retrieval_necessary": False,
                "agent_trace": [
                    trace_event(
                        TraceEventKind.AGENT_DECISION,
                        node="plan_retrieval",
                        summary=(
                            "Skipped knowledge-base retrieval: this repository "
                            f"contains no detected use of {dependency.name}, so there "
                            "are no symbols to look up."
                        ),
                    )
                ],
            }

        known = frozenset(stat.symbol for stat in inventory.entries)
        missing_topics = _latest_missing_topics(state)
        update: StateUpdate = {"iteration": iteration}
        queries: list[RagQuery] = []

        try:
            plan, call = await llm.invoke_structured(
                node="plan_retrieval",
                prompt=_plan_prompt(
                    dependency, inventory, iteration=iteration, missing_topics=missing_topics
                ),
                schema=RetrievalPlan,
            )
        except UpgradePilotError as exc:
            update.update(
                _degraded(
                    "plan_retrieval",
                    exc,
                    consequence=("Falling back to a query composed from the symbol inventory."),
                )
            )
        else:
            update["llm_calls"] = [call]
            for planned in plan.queries[:MAX_QUERIES_PER_ITERATION]:
                query = _accept_planned(planned, dependency, known, iteration=iteration)
                if query is not None:
                    queries.append(query)

        if not queries:
            queries = [_fallback_query(dependency, inventory, iteration=iteration)]

        update["rag_queries"] = queries
        origins = {query.origin.value for query in queries}
        update["summary"] = (
            f"Planned {len(queries)} search(es) for round {iteration} "
            f"({', '.join(sorted(origins))}-composed)."
        )
        return update

    return body


def make_retrieve(store: KnowledgeStore, *, limit: int = DEFAULT_LIMIT) -> NodeBody[RAGState]:
    """Run this round's queries and annotate what comes back.

    The store does the symbol join itself (spec 7.2), so this node never
    post-filters candidates in Python -- what it adds is the annotation set:
    every symbol in the inventory is passed as `symbol_annotations` so that a
    chunk reached semantically still reports which of the repository's symbols
    it happens to cover. `matched_symbols` feeds nothing that grades, but it
    is what lets the trace say *why* a document was kept.
    """

    async def body(state: RAGState) -> StateUpdate:
        dependency = state["dependency"]
        inventory = state["symbol_inventory"]
        iteration = state["iteration"]
        annotations = (
            tuple(stat.symbol for stat in inventory.entries) if inventory is not None else ()
        )

        this_round = [query for query in state["rag_queries"] if query.iteration == iteration]
        found: list[RetrievedChunk] = []
        events: list[TraceEvent] = []

        for query in this_round:
            events.append(
                trace_event(
                    TraceEventKind.QUERY_ISSUED,
                    node="retrieve",
                    summary=f"Searched the knowledge base for: {query.text}",
                    detail=(
                        f"round {query.iteration}, {query.origin.value}-composed"
                        + (f", symbols: {', '.join(query.symbols)}" if query.symbols else "")
                    ),
                )
            )
            try:
                chunks = store.search(
                    query.text,
                    dependency=dependency.canonical_name,
                    to_version_major=query.to_version_major,
                    symbols=query.symbols,
                    symbol_annotations=annotations,
                    limit=limit,
                )
            except UpgradePilotError as exc:
                # The store being unreachable is not a per-query problem, so
                # the remaining queries are abandoned rather than retried:
                # a knowledge base that is down for query one is down for
                # query two, and three rounds of the same failure produce
                # three identical errors and no evidence. `kb_unavailable`
                # ends the loop; spec 8.1's ceiling does the rest.
                degraded = _degraded(
                    "retrieve",
                    exc,
                    consequence="No further searches were attempted for this run.",
                )
                return {
                    "kb_unavailable": True,
                    "errors": degraded["errors"],
                    "agent_trace": [*events, *degraded["agent_trace"]],
                    "candidates": found,
                    "retrieved_sources": [chunk.to_source_ref() for chunk in found],
                    "summary": (
                        "The knowledge base could not be reached; retrieval stopped "
                        f"after {len(found)} chunk(s)."
                    ),
                }
            found.extend(chunks)

        events.append(
            trace_event(
                TraceEventKind.SOURCES_RETRIEVED,
                node="retrieve",
                summary=(
                    f"Round {iteration} retrieved {len(found)} chunk(s) from "
                    f"{len({chunk.source_id for chunk in found})} document(s)."
                ),
            )
        )
        return {
            "candidates": found,
            "retrieved_sources": [chunk.to_source_ref() for chunk in found],
            "agent_trace": events,
            "summary": (f"Ran {len(this_round)} search(es) and retrieved {len(found)} chunk(s)."),
        }

    return body


def make_evaluate_retrieval(llm: TrackedLLM) -> NodeBody[RAGState]:
    """Grade coverage with the model, then override it with arithmetic.

    Spec 7.3's deterministic gate. `annotate_coverage` reads each retrieved
    document's own `affected_symbols` and reports which of the repository's
    symbols nothing documents; if any *high-confidence* symbol is in that set,
    retrieval is insufficient no matter what the model concluded.

    The model is not called at all when nothing was retrieved. There is
    nothing to grade, the gate already knows the answer, and a call whose
    prompt lists zero documents is a paid round-trip that can only produce a
    guess.
    """

    async def body(state: RAGState) -> StateUpdate:
        dependency = state["dependency"]
        inventory = state["symbol_inventory"] or SymbolInventory()
        candidates = state["candidates"]
        iteration = state["iteration"]

        report = annotate_coverage(inventory, tuple(candidates))
        gate_sufficient = report.sufficient
        update: StateUpdate = {}
        notes: str | None = None
        model_sufficient = False
        missing_topics: tuple[str, ...] = ()

        if candidates:
            try:
                grade, call = await llm.invoke_structured(
                    node="evaluate_retrieval",
                    prompt=_grade_prompt(dependency, candidates, uncovered=report.uncovered),
                    schema=CoverageGrade,
                )
            except UpgradePilotError as exc:
                update.update(
                    _degraded(
                        "evaluate_retrieval",
                        exc,
                        consequence=(
                            "Coverage was judged by the deterministic gate alone for this round."
                        ),
                    )
                )
                # Deferring to the gate means adopting its verdict as the
                # model's, which is the only reading that does not invent an
                # opinion: asserting `False` would force another round on a
                # provider outage, and asserting `True` would let an outage
                # end the loop early. The gate is the one judge still
                # standing, so it judges alone -- and `notes` says so, since
                # a reader must not take this row for a graded round.
                model_sufficient = gate_sufficient
                notes = (
                    "The coverage grader could not be reached; this round was judged "
                    "by the deterministic symbol gate alone."
                )
            else:
                update["llm_calls"] = [call]
                model_sufficient = grade.sufficient
                missing_topics = tuple(
                    topic.strip() for topic in grade.missing_topics if topic.strip()
                )
                notes = grade.notes.strip() or None
        else:
            notes = "Nothing was retrieved in this round, so there was nothing to grade."

        evaluation = RagEvaluation(
            iteration=iteration,
            model_sufficient=model_sufficient,
            gate_sufficient=gate_sufficient,
            candidates_considered=len(candidates),
            uncovered_symbols=report.uncovered,
            uncovered_high_confidence=report.uncovered_high_confidence,
            missing_topics=missing_topics,
            notes=notes,
        )

        verdict = "sufficient" if evaluation.sufficient else "not yet sufficient"
        summary = f"Round {iteration} evidence graded {verdict}."
        if model_sufficient and not gate_sufficient:
            summary = (
                f"Round {iteration}: the coverage grade said sufficient, but "
                f"{len(report.uncovered_high_confidence)} high-confidence symbol(s) "
                "have no documentation behind them, so retrieval continues."
            )

        update["rag_evaluations"] = [evaluation]
        update["summary"] = summary
        update["uncovered_symbols"] = list(report.uncovered)
        update["agent_trace"] = [
            *update.get("agent_trace", []),
            trace_event(
                TraceEventKind.RETRIEVAL_EVALUATED,
                node="evaluate_retrieval",
                summary=summary,
                detail=(
                    ", ".join(report.uncovered_high_confidence)
                    if report.uncovered_high_confidence
                    else None
                ),
            ),
        ]
        return update

    return body


def _best_chunk_per_document(candidates: list[RetrievedChunk]) -> list[RetrievedChunk]:
    """One chunk per document: the highest-relevance one, kept whole.

    A `BreakingChange` carries a single `SourceRef`, so a document retrieved
    as three chunks has to be represented by one of them. Taking the best is
    the only choice that keeps the citation and the quoted text in agreement:
    the surviving chunk's id, text and score all come from the same retrieval,
    so following the citation lands on the passage the change was built from.

    Ties keep first appearance, which is the store's own ranking order, so the
    output is stable across runs over an unchanged corpus.
    """
    best: dict[str, RetrievedChunk] = {}
    order: list[str] = []
    for chunk in candidates:
        current = best.get(chunk.source_id)
        if current is None:
            best[chunk.source_id] = chunk
            order.append(chunk.source_id)
        elif chunk.relevance > current.relevance:
            best[chunk.source_id] = chunk
    return [best[source_id] for source_id in order]


def make_build_context() -> NodeBody[RAGState]:
    """Assemble the loop's output. No model is called here at all.

    Each `BreakingChange` is built out of one retrieved chunk: the document's
    own title, severity and symbol list, the chunk's verbatim text as the
    description, and a `SourceRef` naming that exact chunk. Nothing is
    summarised, so there is no step at which a plausible sentence could be
    introduced that the cited document does not support.

    `old_form` and `new_form` stay `None`. The corpus documents carry both,
    but only as prose inside the body -- extracting them would mean either a
    model call that could paraphrase them wrongly or a parser guessing at
    which fenced block is which. An absent field is honest; a wrong code
    sample presented as "the new form" is the most actionable kind of lie
    this product could tell.

    A document is turned into a `BreakingChange` only when it names at least
    one symbol this repository actually uses. The rest stay in
    `retrieved_sources` -- they were consulted, and the trace says so -- but
    they are not asserted as changes affecting this codebase. `affected_symbols`
    on the change itself keeps the document's *own* list rather than the
    intersection, because that is what the document says and the report cites
    the document; the matching against usage is spec 8.1's job, done where the
    usage sites are.
    """

    async def body(state: RAGState) -> StateUpdate:
        inventory = state["symbol_inventory"] or SymbolInventory()
        candidates = state["candidates"]
        evaluations = state["rag_evaluations"]
        used_symbols = frozenset(stat.symbol for stat in inventory.entries)

        report = annotate_coverage(inventory, tuple(candidates))
        changes: list[BreakingChange] = []
        for chunk in _best_chunk_per_document(candidates):
            relevant = tuple(sorted(set(chunk.affected_symbols) & used_symbols))
            if not relevant:
                continue
            changes.append(
                BreakingChange(
                    id=chunk.source_id,
                    title=chunk.title,
                    description=chunk.text,
                    severity=chunk.severity,
                    affected_symbols=chunk.affected_symbols,
                    source=chunk.to_source_ref(),
                )
            )

        last = evaluations[-1] if evaluations else None
        if not state["retrieval_necessary"]:
            stop_reason = RagStopReason.NOT_NECESSARY
        elif state["kb_unavailable"]:
            stop_reason = RagStopReason.KB_UNAVAILABLE
        elif last is not None and last.sufficient:
            stop_reason = RagStopReason.SUFFICIENT
        else:
            stop_reason = RagStopReason.ITERATION_LIMIT

        context = RagContext(
            iterations=state["iteration"],
            sources_considered=len(candidates),
            # `and stop_reason is SUFFICIENT` is not redundant belt-and-braces:
            # `RagContext` refuses `sufficient=True` alongside the two failure
            # stop reasons, and a run that was cut short by an unreachable
            # store one round after a passing evaluation would otherwise raise
            # a `ValidationError` here -- turning a degraded-but-reportable run
            # into an internal error at the last step.
            sufficient=bool(last is not None and last.sufficient)
            and stop_reason is RagStopReason.SUFFICIENT,
            stop_reason=stop_reason,
            unknowns=report.uncovered,
        )

        events = [
            trace_event(
                TraceEventKind.SOURCES_SELECTED,
                node="build_context",
                summary=(
                    f"{len(changes)} documented breaking change(s) affect symbols this "
                    f"repository uses, drawn from {len(candidates)} retrieved chunk(s)."
                ),
                detail=", ".join(change.id for change in changes) or None,
            )
        ]
        if context.unknowns:
            events.append(
                trace_event(
                    TraceEventKind.SOURCES_SELECTED,
                    node="build_context",
                    summary=(
                        f"{len(context.unknowns)} symbol(s) in use here have no "
                        "documented change behind them and are reported as unknowns."
                    ),
                    detail=", ".join(context.unknowns),
                )
            )

        return {
            "breaking_changes": changes,
            "rag_context": context,
            "agent_trace": events,
            "summary": (
                f"Built {len(changes)} breaking change(s) from "
                f"{len(candidates)} retrieved chunk(s); "
                f"{len(context.unknowns)} symbol(s) left as unknowns."
            ),
        }

    return body
