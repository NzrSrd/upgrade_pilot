"""Citation types.

The invariants here are the product's central promise made structural:
a breaking change without a source, or a risk factor without evidence,
cannot be constructed at all.

`RiskFactor.evidence` and `BreakingChange.affected_symbols` are tuples, not
lists. `frozen=True` (set once on `HonestModel`) blocks field *assignment*
but does not stop mutation of a contained `list` (e.g. `.clear()`), which
would silently empty a "required" collection after construction. A tuple has
no such mutating methods.

Exactly what that buys, stated honestly — an earlier version of this
docstring claimed the invariant "holds for their lifetime", and it did not:
`model_copy(update=...)` skipped validation entirely and put an empty tuple
back. That hole is closed on `HonestModel`, so the guarantee now is that
neither ordinary construction, nor mutation of a built model, nor
`model_copy(update=...)` can produce a model that violates its own
constraints. `model_construct` is a deliberate, documented exception — see
`models/base.py` for why it is left open.
"""

from typing import Annotated, Literal

from pydantic import Field, StringConstraints

from upgradepilot.models.base import HonestModel
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


class SourceRef(HonestModel):
    """A resolvable pointer into the knowledge base."""

    source_id: NonBlankStr
    title: NonBlankStr
    source_type: SourceType
    url_or_reference: NonBlankStr
    chunk_id: NonBlankStr
    relevance: float = Field(ge=0.0, le=1.0)


class RepoEvidence(HonestModel):
    """A specific line of the analyzed repository."""

    kind: Literal["repo"] = "repo"
    file: NonBlankStr
    line: int = Field(ge=1)
    snippet: str | None = None


class DocEvidence(HonestModel):
    """A specific chunk of a corpus document."""

    kind: Literal["doc"] = "doc"
    source_id: NonBlankStr
    chunk_id: NonBlankStr
    relevance: float | None = Field(default=None, ge=0.0, le=1.0)


EvidenceRef = Annotated[RepoEvidence | DocEvidence, Field(discriminator="kind")]


class BreakingChange(HonestModel):
    """A documented change. `source` is required: no citation, no change."""

    id: NonBlankStr
    title: NonBlankStr
    description: NonBlankStr
    old_form: NonBlankStr | None = None
    new_form: NonBlankStr | None = None
    severity: Severity
    affected_symbols: tuple[NonBlankStr, ...] = Field(min_length=1)
    source: SourceRef


class RiskFactor(HonestModel):
    """One dimension of risk. `evidence` must be non-empty."""

    id: NonBlankStr
    name: NonBlankStr
    category: RiskCategory
    level: RiskLevel
    weight: float = Field(ge=0.0, le=1.0)
    detail: NonBlankStr
    evidence: tuple[EvidenceRef, ...] = Field(min_length=1)
