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

import posixpath
from typing import Annotated, Literal

from pydantic import AfterValidator, Field, StringConstraints

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


def _require_repo_relative(value: str) -> str:
    """Reject any path that does not name a file inside the analyzed tree.

    Checked against the *text*, never the filesystem: this validator runs on
    citations that may be constructed long after the workspace is deleted, and
    a filesystem probe here would both fail spuriously and turn a model
    constructor into an existence oracle.

    `posixpath` explicitly, not `os.path`: repository paths are POSIX by
    construction (`git log --name-only` emits POSIX, and `Path.as_posix()` is
    what the analyzer calls), and `os.path` would quietly accept `a\\b` as a
    single filename on this platform while treating it as a separator on
    another.
    """
    if value.startswith("/"):
        raise ValueError(f"path must be repository-relative, not absolute: {value!r}")
    if "\\" in value:
        raise ValueError(f"path must use '/' separators: {value!r}")
    segments = value.split("/")
    if any(segment in {"", ".", ".."} for segment in segments):
        raise ValueError(f"path must not contain empty, '.' or '..' segments: {value!r}")
    if posixpath.normpath(value) != value:
        raise ValueError(f"path must already be normalised: {value!r}")
    return value


def is_repo_relative(value: str) -> bool:
    """Whether `value` could be a `RepoRelativePath`.

    The producer-side companion to the validator below, and deliberately
    implemented BY it rather than beside it: a second copy of these rules
    would drift, and the whole point is that a path this returns False for
    can never reach a model constructor and raise.

    `Workspace` is the caller. Its input is an untrusted third-party
    repository, where `back\\slash.py` and (via git's `core.quotePath`) any
    escaped filename are legal on disk and unrepresentable as a citation.
    CLAUDE.md rule 20: such a path becomes a recorded gap, never an
    exception in the middle of an otherwise complete analysis.
    """
    stripped = value.strip()
    if not stripped:
        return False
    try:
        _require_repo_relative(stripped)
    except ValueError:
        return False
    return True


RepoRelativePath = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
    AfterValidator(_require_repo_relative),
]
"""A path naming a file inside the analyzed repository, relative to its root.

Every file-and-line citation the report prints is resolved against a
repository root the reader supplies. An absolute path resolves against the
analysis machine instead, and a `..` segment resolves outside the tree that
was analyzed -- both produce a citation that looks precise and cannot be
checked.

`NonBlankStr` alone was not enough: it accepted `/etc/passwd` and
`../outside.py` without complaint, which `PLANNING.md` recorded as a Phase 2
carry-in. The analyzer is the only producer of these values from Task 9
onward, and it emits `Path.relative_to(root).as_posix()`, which satisfies
this by construction.
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
    file: RepoRelativePath
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
