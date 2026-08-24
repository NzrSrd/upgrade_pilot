"""Citation types.

The invariants here are the product's central promise made structural:
a breaking change without a source, or a risk factor without evidence,
cannot be constructed at all.

`RiskFactor.evidence` and `BreakingChange.affected_symbols` are tuples, not
lists. `ConfigDict(frozen=True)` blocks field *assignment* but does not stop
mutation of a contained `list` (e.g. `.clear()`), which would silently empty
a "required" collection after construction. A tuple has no such mutating
methods, so once these models are built the invariant holds for their
lifetime, not just at construction time.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from upgradepilot.models.enums import RiskCategory, RiskLevel, Severity, SourceType

NonBlankStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
"""A string that carries actual content.

`Field(min_length=1)` alone accepts `"   "`, so a citation could be
structurally present and practically unresolvable — a `url_or_reference` of
whitespace is an uncited claim wearing a citation's clothes. Stripping first
also normalises symbol names, which matters because the corpus is filtered
with Chroma's `$contains`, and that match is exact-element: a symbol stored as
`" Config "` would never match a query for `"Config"`.

Deliberately NOT applied to `RepoEvidence.snippet`, where leading whitespace is
the source file's own indentation and stripping it would corrupt the quote.
"""


class SourceRef(BaseModel):
    """A resolvable pointer into the knowledge base."""

    model_config = ConfigDict(frozen=True)

    source_id: NonBlankStr
    title: NonBlankStr
    source_type: SourceType
    url_or_reference: NonBlankStr
    chunk_id: NonBlankStr
    relevance: float = Field(ge=0.0, le=1.0)


class RepoEvidence(BaseModel):
    """A specific line of the analyzed repository."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["repo"] = "repo"
    file: NonBlankStr
    line: int = Field(ge=1)
    snippet: str | None = None


class DocEvidence(BaseModel):
    """A specific chunk of a corpus document."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["doc"] = "doc"
    source_id: NonBlankStr
    chunk_id: NonBlankStr
    relevance: float | None = Field(default=None, ge=0.0, le=1.0)


EvidenceRef = Annotated[RepoEvidence | DocEvidence, Field(discriminator="kind")]


class BreakingChange(BaseModel):
    """A documented change. `source` is required: no citation, no change."""

    model_config = ConfigDict(frozen=True)

    id: NonBlankStr
    title: NonBlankStr
    description: NonBlankStr
    old_form: NonBlankStr | None = None
    new_form: NonBlankStr | None = None
    severity: Severity
    affected_symbols: tuple[NonBlankStr, ...] = Field(min_length=1)
    source: SourceRef


class RiskFactor(BaseModel):
    """One dimension of risk. `evidence` must be non-empty."""

    model_config = ConfigDict(frozen=True)

    id: NonBlankStr
    name: NonBlankStr
    category: RiskCategory
    level: RiskLevel
    weight: float = Field(ge=0.0, le=1.0)
    detail: NonBlankStr
    evidence: tuple[EvidenceRef, ...] = Field(min_length=1)
