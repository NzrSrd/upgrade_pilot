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

from pydantic import BaseModel, ConfigDict, Field

from upgradepilot.models.enums import RiskCategory, RiskLevel, Severity, SourceType


class SourceRef(BaseModel):
    """A resolvable pointer into the knowledge base."""

    model_config = ConfigDict(frozen=True)

    source_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    source_type: SourceType
    url_or_reference: str = Field(min_length=1)
    chunk_id: str = Field(min_length=1)
    relevance: float = Field(ge=0.0, le=1.0)


class RepoEvidence(BaseModel):
    """A specific line of the analyzed repository."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["repo"] = "repo"
    file: str = Field(min_length=1)
    line: int = Field(ge=1)
    snippet: str | None = None


class DocEvidence(BaseModel):
    """A specific chunk of a corpus document."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["doc"] = "doc"
    source_id: str = Field(min_length=1)
    chunk_id: str = Field(min_length=1)
    relevance: float | None = Field(default=None, ge=0.0, le=1.0)


EvidenceRef = Annotated[RepoEvidence | DocEvidence, Field(discriminator="kind")]


class BreakingChange(BaseModel):
    """A documented change. `source` is required: no citation, no change."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    old_form: str | None = None
    new_form: str | None = None
    severity: Severity
    affected_symbols: tuple[str, ...] = Field(min_length=1)
    source: SourceRef


class RiskFactor(BaseModel):
    """One dimension of risk. `evidence` must be non-empty."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    category: RiskCategory
    level: RiskLevel
    weight: float = Field(ge=0.0, le=1.0)
    detail: str = Field(min_length=1)
    evidence: tuple[EvidenceRef, ...] = Field(min_length=1)
