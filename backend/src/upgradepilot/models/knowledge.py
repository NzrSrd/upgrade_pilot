"""Knowledge-base models: the corpus document, its chunks, and what
retrieval hands back.

Spec §7.2. Three shapes, one for each side of the store:

- `CorpusDocument` is what an author writes -- frontmatter plus body, one
  breaking change per file. It is the *only* origin of a `SourceRef`, so
  every constraint here exists to stop a document parsing into a plausible
  wrong shape and then citing itself confidently.
- `DocumentChunk` is what gets embedded. `chunk_id` is the citation key the
  report prints.
- `RetrievedChunk` is what comes back out, carrying enough metadata to build
  a `SourceRef` without a second round-trip to the store.

The RAG-loop models (`RagQuery`, `RagEvaluation`, `RagContext`) arrived with
Phase 5, at the bottom of this module -- they are the loop's own record of
what it asked, what it got graded, and what it concluded.
"""

from datetime import date
from typing import Annotated, Self

from pydantic import AfterValidator, ConfigDict, Field, model_validator

from upgradepilot.models.base import HonestModel
from upgradepilot.models.enums import QueryOrigin, RagStopReason, Severity, SourceType
from upgradepilot.models.evidence import NonBlankStr, SourceRef
from upgradepilot.models.inputs import canonicalize_name

CanonicalDependency = Annotated[NonBlankStr, AfterValidator(canonicalize_name)]
"""A dependency name normalised to its PEP 503 form at construction.

Normalised in the type rather than checked in a validator, so there is no
way to build a `CorpusDocument` holding the un-normalised spelling. The
corpus is filtered with Chroma's exact-match `$contains` against
`DependencySpec.name`, which is canonicalized on the same function: a
document ingested as `Pydantic` would be invisible to every query for
`pydantic`, and an empty result set reads as "no known breaking changes".
"""


SYMBOL_BEARING_SOURCE_TYPES = frozenset(
    {SourceType.MIGRATION_GUIDE, SourceType.CHANGELOG, SourceType.COMPAT_NOTE}
)
"""Source types that document a specific API change and must name symbols.

Retrieval joins the repository's symbol inventory against
`affected_symbols` with Chroma's `$contains`. A migration guide naming no
symbol can never be reached by that join, so the symbol it documents reads
as *uncovered* -- an under-report, and precisely the failure §7.3's
deterministic sufficiency gate exists to catch. Requiring at least one
symbol here makes that authoring mistake impossible rather than silent.

The complement -- `adr`, `upgrade_report` -- is internal engineering
guidance: prose about how to approach a migration, reached semantically
rather than by symbol. Requiring a symbol there would mean inventing one,
and an invented symbol is a false join. So the rule is applied where it can
be honoured and not where it cannot.
"""


class CorpusDocument(HonestModel):
    """One authored corpus document: validated frontmatter plus its body.

    `extra="forbid"`, deliberately. A typo'd frontmatter key -- `serverity`,
    `affected_symbol` -- is otherwise a silent no-op: the field keeps its
    default, the document is indexed with metadata the author believes they
    set, and nothing downstream can tell. Forbidding extras turns that into
    an ingestion failure naming the key.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: NonBlankStr
    """The citation key. Unique across the corpus -- see `load_corpus`."""

    title: NonBlankStr
    source_type: SourceType
    dependency: CanonicalDependency

    from_version: NonBlankStr
    to_version: NonBlankStr
    to_version_major: int
    """The scalar Chroma narrows on. Must agree with `to_version`."""

    affected_symbols: tuple[NonBlankStr, ...] = ()
    severity: Severity
    url_or_reference: NonBlankStr
    created_at: date
    tags: tuple[NonBlankStr, ...] = ()

    body: NonBlankStr
    """Supplied by the parser, never by the frontmatter."""

    path: NonBlankStr
    """Where this document was read from, relative to the corpus root.

    Carried so that every downstream failure -- a chunk that embeds badly, a
    symbol that never joins -- is diagnosed by opening a file rather than by
    grepping the corpus for a `source_id`.
    """

    @model_validator(mode="after")
    def _major_must_agree_with_the_version_string(self) -> Self:
        """`to_version` is prose the reader sees; `to_version_major` is what
        retrieval filters on. Disagreement means a query narrowed to major 3
        can return a document whose own text says it is about 2.0 -- the
        filter and the citation contradicting each other with nothing in the
        output to show it.

        Only the leading component is read, so ordinary release spellings
        (`2.0`, `2.9.0`, `2.0b1`) are not collateral damage. A `to_version`
        with no leading integer at all is refused for the same reason: there
        is then no way to check the two against each other.
        """
        leading = self.to_version.split(".", 1)[0]
        digits = ""
        for character in leading:
            if not character.isdigit():
                break
            digits += character
        if not digits:
            raise ValueError(
                f"to_version_major cannot be checked against to_version={self.to_version!r}: "
                "the version must start with its major number (e.g. '2.0', '2.0b1')"
            )
        if int(digits) != self.to_version_major:
            raise ValueError(
                f"to_version_major={self.to_version_major} disagrees with "
                f"to_version={self.to_version!r}, whose major is {int(digits)}"
            )
        return self

    @model_validator(mode="after")
    def _symbol_bearing_documents_must_name_a_symbol(self) -> Self:
        if self.source_type in SYMBOL_BEARING_SOURCE_TYPES and not self.affected_symbols:
            raise ValueError(
                f"affected_symbols must name at least one symbol for "
                f"source_type={self.source_type.value!r}: retrieval joins on it with "
                "$contains, so a document naming none can never be reached by symbol and "
                "the change it documents would read as uncovered"
            )
        return self


class DocumentChunk(HonestModel):
    """One embedded unit of a document. `chunk_id` is what a report cites."""

    chunk_id: NonBlankStr
    source_id: NonBlankStr
    ordinal: int = Field(ge=0)
    text: NonBlankStr


class RetrievedChunk(HonestModel):
    """A chunk returned by retrieval, with the metadata a citation needs.

    Carries the document's identifying metadata rather than just its
    `source_id` so that building a `SourceRef` -- and therefore printing a
    citation -- never needs a second round-trip to the store. A retrieval
    result that cannot name its own source is not usable evidence.
    """

    chunk_id: NonBlankStr
    source_id: NonBlankStr
    title: NonBlankStr
    source_type: SourceType
    url_or_reference: NonBlankStr
    text: NonBlankStr

    dependency: NonBlankStr
    to_version_major: int
    severity: Severity
    affected_symbols: tuple[NonBlankStr, ...] = ()

    distance: float = Field(ge=0.0)
    """Raw vector distance as the store reported it. Kept alongside
    `relevance` because the mapping between them is a decision this project
    makes, and losing the input to that decision makes it uncheckable."""

    relevance: float = Field(ge=0.0, le=1.0)

    matched_symbols: tuple[NonBlankStr, ...] = ()
    """Which of the query's symbols this chunk's document actually names.

    Annotated at retrieval time (spec §7.3's `retrieve`), and the input to
    the deterministic sufficiency gate. Empty means the chunk was reached
    semantically and covers none of the symbols that were asked about --
    which is a legitimate result, and one the gate must be able to see.
    """

    def to_source_ref(self) -> SourceRef:
        return SourceRef(
            source_id=self.source_id,
            title=self.title,
            source_type=self.source_type,
            url_or_reference=self.url_or_reference,
            chunk_id=self.chunk_id,
            relevance=self.relevance,
        )


class IngestReport(HonestModel):
    """What an ingestion run actually wrote.

    Counted from the store after the write rather than from the input, so a
    write that silently did nothing cannot report success. This is the
    operator's only confirmation that the corpus they authored is the corpus
    that got indexed.
    """

    documents: int = Field(ge=0)
    chunks: int = Field(ge=0)


# ---------------------------------------------------------------------------
# The RAG loop's own records (spec 7.3). Phase 5.
#
# Three shapes, one per question a reader asks about a retrieval loop: what
# did it ask (`RagQuery`), how well did that go (`RagEvaluation`), and what
# did the whole loop conclude (`RagContext`). All three are rendered in the
# agent trace, so each carries a reason rather than only a verdict.
# ---------------------------------------------------------------------------


class RagQuery(HonestModel):
    """One query issued against the knowledge base.

    Records the filters that were actually sent, not the ones that were
    asked for. `plan_retrieval` intersects the model's proposed symbols with
    the repository's real inventory before querying, so a symbol the model
    invented never reaches Chroma -- and never appears here either, because
    this is the record a reader checks the retrieval against.
    """

    query_id: NonBlankStr
    iteration: int = Field(ge=1)
    text: NonBlankStr

    symbols: tuple[NonBlankStr, ...] = ()
    """The `$contains` symbol filter, exactly as sent."""

    source_types: tuple[SourceType, ...] = ()
    to_version_major: int | None = None

    rationale: NonBlankStr
    """One observable line saying why this query was issued.

    Rendered in the trace, so CLAUDE.md rule 26 applies: it is a sentence
    about the *search*, never a window into the prompt that produced it.
    """

    origin: QueryOrigin = QueryOrigin.MODEL


class RagEvaluation(HonestModel):
    """One round's coverage grade: the model's opinion, and the gate's veto.

    Spec 7.3 puts an LLM in charge of grading and then overrides it
    mechanically. Both halves are stored because both are facts about the
    run, and because a single merged boolean would make the override
    invisible -- "the model said sufficient and the gate disagreed" is
    exactly what a reader needs to see when a loop ran three rounds.

    `sufficient` is derived rather than stored (CLAUDE.md rule 21). A stored
    verdict could disagree with the two inputs printed beside it, which is
    the one thing this model exists to prevent.
    """

    iteration: int = Field(ge=1)

    model_sufficient: bool
    """What the LLM concluded. Carries no authority on its own."""

    gate_sufficient: bool
    """The deterministic gate: no high-confidence symbol lacks a document.

    Constrained below to agree with `uncovered_high_confidence`, so it cannot
    be set to a value the evidence does not support.
    """

    candidates_considered: int = Field(ge=0)
    uncovered_symbols: tuple[NonBlankStr, ...] = ()
    uncovered_high_confidence: tuple[NonBlankStr, ...] = ()
    missing_topics: tuple[NonBlankStr, ...] = ()
    """What the model says is still missing. Fed back into the next round's
    query planning, which is the only thing that makes another iteration
    different from a repeat of the first."""

    notes: str | None = None

    @property
    def sufficient(self) -> bool:
        """The gate can only ever veto.

        `and`, deliberately, and in this direction only: a model that says
        "insufficient" is asking for another round, which costs one more
        retrieval and is allowed; a model that says "sufficient" over a
        mechanical gap is the failure mode, and the gate refuses it. There is
        no arrangement of these two booleans in which the gate turns a `False`
        into a `True` -- if there were, the gate would be a suggestion.
        """
        return self.model_sufficient and self.gate_sufficient

    @model_validator(mode="after")
    def _the_gate_must_agree_with_its_own_evidence(self) -> Self:
        """`gate_sufficient` is not an opinion, so it may not be set freely.

        It means exactly "no high-confidence symbol is uncovered", and the
        uncovered set is right here in the same object. Letting the two
        disagree would allow a caller -- or a resumed, mis-deserialized
        state -- to record a passing gate beside the evidence that it failed.
        """
        expected = not self.uncovered_high_confidence
        if self.gate_sufficient != expected:
            raise ValueError(
                f"gate_sufficient={self.gate_sufficient} contradicts "
                f"uncovered_high_confidence={self.uncovered_high_confidence!r}: the gate "
                "means 'no high-confidence symbol lacks a document' and is derived from "
                "that set, never asserted alongside it"
            )
        return self

    @model_validator(mode="after")
    def _uncovered_high_confidence_is_part_of_uncovered(self) -> Self:
        """A symbol cannot be uncovered-at-high-confidence and covered.

        The two tuples come from one `CoverageReport`, so they agree by
        construction today. The check is what keeps that true if a second
        producer ever appears: `uncovered_symbols` is what the report prints
        as `unknowns`, and a high-confidence gap missing from it would be a
        gap the reader is never shown.
        """
        stray = sorted(set(self.uncovered_high_confidence) - set(self.uncovered_symbols))
        if stray:
            raise ValueError(
                f"uncovered_high_confidence names symbols absent from uncovered_symbols: {stray}"
            )
        return self


class RagContext(HonestModel):
    """What the retrieval loop concluded, as the rest of the graph reads it.

    Spec 8.1 turns `evidence_available` into a hard confidence ceiling, which
    is why it is **derived from the source count** rather than stored: a
    stored flag is exactly the field a node under time pressure sets to
    `True` beside an empty evidence list, and the ceiling would then never
    engage. Absent evidence must not be able to produce a confident answer,
    and the cheapest way to guarantee that is to make the flag uncomputable
    from anything but the count.
    """

    iterations: int = Field(ge=0)
    """Completed retrieval rounds. Zero when retrieval was not necessary."""

    sources_considered: int = Field(ge=0)
    sufficient: bool
    stop_reason: RagStopReason

    unknowns: tuple[NonBlankStr, ...] = ()
    """Symbols the repository uses that no retrieved document documents.

    Every tier, not just high confidence: the gate only *blocks* on
    high-confidence gaps, but the report names all of them, because a
    medium-confidence symbol with no documentation behind it is still a thing
    the reader should know was not explained.
    """

    @property
    def evidence_available(self) -> bool:
        """Whether anything was retrieved at all. See the class docstring."""
        return self.sources_considered > 0

    @model_validator(mode="after")
    def _a_sufficient_loop_must_have_run_and_found_something(self) -> Self:
        """Sufficiency is a claim about evidence, so it needs evidence.

        Two contradictions are refused. `sufficient=True` with nothing
        retrieved is the empty-corpus lie this whole module guards against:
        the deterministic gate passes trivially on an empty inventory, and
        without this check that verdict would travel onward as "retrieval
        succeeded". And `sufficient=True` under a `stop_reason` of
        `kb_unavailable` or `iteration_limit` claims success from the two
        outcomes that are defined by not reaching it.
        """
        if not self.sufficient:
            return self
        if self.sources_considered == 0:
            raise ValueError(
                "sufficient=True with sources_considered=0: an empty result set cannot "
                "support a sufficiency claim, whatever the gate concluded about an "
                "inventory it had nothing to check against"
            )
        if self.stop_reason in {RagStopReason.KB_UNAVAILABLE, RagStopReason.ITERATION_LIMIT}:
            raise ValueError(
                f"sufficient=True with stop_reason={self.stop_reason.value!r}: that reason "
                "means the loop stopped without reaching sufficiency"
            )
        return self

    @model_validator(mode="after")
    def _not_necessary_means_nothing_was_asked(self) -> Self:
        """`NOT_NECESSARY` is a decision taken before any query is issued.

        Recording it beside a non-zero iteration count would describe a loop
        that ran and then claimed it never needed to, which is the shape of
        a run whose skip decision was written over the top of real work.
        """
        if self.stop_reason is RagStopReason.NOT_NECESSARY and self.iterations != 0:
            raise ValueError(
                f"stop_reason='not_necessary' with iterations={self.iterations}: retrieval "
                "was either skipped before the first query or it was not unnecessary"
            )
        return self
