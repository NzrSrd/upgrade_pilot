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

The RAG-loop models (`RagQuery`, `RagEvaluation`, `RagContext`) are Phase 5,
where a real caller can shape them. Only what Phase 3 consumes lives here.
"""

from datetime import date
from typing import Annotated, Self

from pydantic import AfterValidator, ConfigDict, Field, model_validator

from upgradepilot.models.base import HonestModel
from upgradepilot.models.enums import Severity, SourceType
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
