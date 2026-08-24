import pytest
from pydantic import TypeAdapter, ValidationError

from upgradepilot.models.enums import RiskCategory, RiskLevel, Severity, SourceType
from upgradepilot.models.evidence import (
    BreakingChange,
    DocEvidence,
    EvidenceRef,
    RepoEvidence,
    RiskFactor,
    SourceRef,
)


def a_source() -> SourceRef:
    return SourceRef(
        source_id="pydantic-v2-migration#validator-renamed",
        title="@validator replaced by @field_validator",
        source_type=SourceType.MIGRATION_GUIDE,
        url_or_reference="https://docs.pydantic.dev/latest/migration/",
        chunk_id="chunk-1",
        relevance=0.94,
    )


def a_repo_evidence() -> RepoEvidence:
    return RepoEvidence(file="src/models.py", line=12, snippet="@validator('email')")


def a_breaking_change() -> BreakingChange:
    return BreakingChange(
        id="bc-1",
        title="@validator removed",
        description="renamed to @field_validator",
        old_form="@validator",
        new_form="@field_validator",
        severity=Severity.HIGH,
        affected_symbols=["validator", "root_validator"],
        source=a_source(),
    )


def test_breaking_change_requires_a_source() -> None:
    """The core invariant: an uncited breaking change is unconstructable."""
    with pytest.raises(ValidationError) as excinfo:
        BreakingChange(
            id="bc-1",
            title="@validator removed",
            description="renamed",
            old_form="@validator",
            new_form="@field_validator",
            severity=Severity.HIGH,
            affected_symbols=["validator"],
        )
    assert "source" in str(excinfo.value)


def test_breaking_change_with_a_source_is_valid() -> None:
    change = BreakingChange(
        id="bc-1",
        title="@validator removed",
        description="renamed to @field_validator",
        old_form="@validator",
        new_form="@field_validator",
        severity=Severity.HIGH,
        affected_symbols=["validator", "root_validator"],
        source=a_source(),
    )
    assert change.source.source_id.startswith("pydantic-v2-migration")
    assert change.severity is Severity.HIGH


def test_breaking_change_requires_at_least_one_symbol() -> None:
    with pytest.raises(ValidationError):
        BreakingChange(
            id="bc-2",
            title="t",
            description="d",
            old_form=None,
            new_form=None,
            severity=Severity.LOW,
            affected_symbols=[],
            source=a_source(),
        )


def test_risk_factor_requires_evidence() -> None:
    """A risk factor citing nothing is unconstructable."""
    with pytest.raises(ValidationError) as excinfo:
        RiskFactor(
            id="rf-1",
            name="breaking_change_exposure",
            category=RiskCategory.BREAKING_CHANGE_EXPOSURE,
            level=RiskLevel.HIGH,
            weight=0.4,
            detail="three high-confidence sites collide with documented changes",
            evidence=[],
        )
    assert "evidence" in str(excinfo.value)


def test_risk_factor_accepts_mixed_evidence_kinds() -> None:
    factor = RiskFactor(
        id="rf-1",
        name="breaking_change_exposure",
        category=RiskCategory.BREAKING_CHANGE_EXPOSURE,
        level=RiskLevel.HIGH,
        weight=0.4,
        detail="collides with a documented change",
        evidence=[
            RepoEvidence(file="src/models.py", line=12, snippet="@validator('email')"),
            DocEvidence(source_id="pydantic-v2-migration#validator-renamed", chunk_id="chunk-1"),
        ],
    )
    assert [e.kind for e in factor.evidence] == ["repo", "doc"]


def test_evidence_ref_discriminates_on_kind() -> None:
    adapter = TypeAdapter(EvidenceRef)

    repo = adapter.validate_python({"kind": "repo", "file": "a.py", "line": 3})
    doc = adapter.validate_python({"kind": "doc", "source_id": "s", "chunk_id": "c"})

    assert isinstance(repo, RepoEvidence)
    assert isinstance(doc, DocEvidence)
    with pytest.raises(ValidationError):
        adapter.validate_python({"kind": "guess", "file": "a.py", "line": 3})


def test_repo_evidence_rejects_a_nonpositive_line() -> None:
    with pytest.raises(ValidationError):
        RepoEvidence(file="a.py", line=0)


def test_relevance_is_bounded() -> None:
    with pytest.raises(ValidationError):
        SourceRef(
            source_id="s",
            title="t",
            source_type=SourceType.ADR,
            url_or_reference="ref",
            chunk_id="c",
            relevance=1.4,
        )


def test_risk_factor_evidence_cannot_be_emptied_after_construction() -> None:
    """frozen=True stops assignment but not list mutation, so evidence is a
    tuple. A RiskFactor that reached the UI with zero evidence would be an
    uncited claim -- CLAUDE.md rule 1's exact failure mode."""
    factor = RiskFactor(
        id="rf-1",
        name="breaking_change_exposure",
        category=RiskCategory.BREAKING_CHANGE_EXPOSURE,
        level=RiskLevel.HIGH,
        weight=0.5,
        detail="@validator is removed in v2",
        evidence=[a_repo_evidence()],
    )

    assert isinstance(factor.evidence, tuple)
    assert not hasattr(factor.evidence, "clear")
    with pytest.raises(ValidationError):
        factor.evidence = ()  # type: ignore[misc]


def test_breaking_change_symbols_cannot_be_emptied_after_construction() -> None:
    change = a_breaking_change()

    assert isinstance(change.affected_symbols, tuple)
    assert not hasattr(change.affected_symbols, "append")
    with pytest.raises(ValidationError):
        change.affected_symbols = ()  # type: ignore[misc]


def test_a_whitespace_only_citation_is_rejected() -> None:
    """`min_length=1` alone accepts "   ", which would let a citation be
    structurally present and practically unresolvable — an uncited claim
    wearing a citation's clothes."""
    with pytest.raises(ValidationError) as exc:
        SourceRef(
            source_id="pydantic-v2-migration#validator-renamed",
            title="@validator replaced by @field_validator",
            source_type=SourceType.MIGRATION_GUIDE,
            url_or_reference="   ",
            chunk_id="chunk-1",
            relevance=0.94,
        )

    assert exc.value.errors()[0]["type"] == "string_too_short"
    assert exc.value.errors()[0]["loc"] == ("url_or_reference",)


def test_a_whitespace_only_symbol_is_rejected() -> None:
    """Blankness is checked per element, not just on the tuple's length."""
    with pytest.raises(ValidationError) as exc:
        BreakingChange(
            id="bc-1",
            title="@validator removed",
            description="renamed to @field_validator",
            severity=Severity.HIGH,
            affected_symbols=["validator", "  "],
            source=a_source(),
        )

    assert exc.value.errors()[0]["type"] == "string_too_short"
    assert exc.value.errors()[0]["loc"] == ("affected_symbols", 1)


def test_symbols_are_stripped_so_they_match_the_corpus_filter() -> None:
    """The corpus is filtered with Chroma's `$contains`, which is
    exact-element: a symbol stored as " Config " would never match a query
    for "Config". Normalising here is what makes that join reliable."""
    change = BreakingChange(
        id="bc-1",
        title="  @validator removed  ",
        description="renamed to @field_validator",
        severity=Severity.HIGH,
        affected_symbols=["  validator  ", "\troot_validator\n"],
        source=a_source(),
    )

    assert change.affected_symbols == ("validator", "root_validator")
    assert change.title == "@validator removed"


def test_snippet_keeps_its_indentation() -> None:
    """RepoEvidence.snippet is a verbatim quote from the repository. Stripping
    it would corrupt the evidence, so NonBlankStr is deliberately not used."""
    evidence = RepoEvidence(file="src/models.py", line=12, snippet="    @validator('email')")

    assert evidence.snippet == "    @validator('email')"


@pytest.mark.parametrize("weight", [-0.1, 1.4], ids=["below-zero", "above-one"])
def test_risk_factor_weight_is_bounded(weight: float) -> None:
    """`weight` is this factor's share of the composite risk score. Outside
    [0.0, 1.0] the score it feeds is not a share of anything, and the number
    reaches the report."""
    with pytest.raises(ValidationError) as exc:
        RiskFactor(
            id="rf-1",
            name="breaking_change_exposure",
            category=RiskCategory.BREAKING_CHANGE_EXPOSURE,
            level=RiskLevel.HIGH,
            weight=weight,
            detail="collides with a documented change",
            evidence=[a_repo_evidence()],
        )
    assert any(e["loc"] == ("weight",) for e in exc.value.errors())


@pytest.mark.parametrize("weight", [0.0, 1.0], ids=["zero", "one"])
def test_risk_factor_weight_accepts_both_endpoints(weight: float) -> None:
    """ge/le, not gt/lt: a factor can carry no weight or all of it."""
    factor = RiskFactor(
        id="rf-1",
        name="breaking_change_exposure",
        category=RiskCategory.BREAKING_CHANGE_EXPOSURE,
        level=RiskLevel.HIGH,
        weight=weight,
        detail="collides with a documented change",
        evidence=[a_repo_evidence()],
    )
    assert factor.weight == weight


@pytest.mark.parametrize("relevance", [-0.1, 1.4], ids=["below-zero", "above-one"])
def test_doc_evidence_relevance_is_bounded(relevance: float) -> None:
    """The same bound as SourceRef.relevance, on the type the retrieval path
    actually cites. It was the covered one that had the test."""
    with pytest.raises(ValidationError) as exc:
        DocEvidence(source_id="s", chunk_id="c", relevance=relevance)
    assert any(e["loc"] == ("relevance",) for e in exc.value.errors())


def test_doc_evidence_relevance_may_be_absent() -> None:
    """None is a legitimate "not scored", distinct from a score of 0.0."""
    assert DocEvidence(source_id="s", chunk_id="c").relevance is None
    assert DocEvidence(source_id="s", chunk_id="c", relevance=0.0).relevance == 0.0


_REJECTED = (
    "/etc/passwd",  # absolute
    "../outside/secrets.py",  # parent escape
    "src/../../outside.py",  # parent escape, interior
    "./src/app.py",  # curdir prefix
    ".",  # curdir itself
    "src\\app\\models.py",  # windows separator
    "   ",  # blank (already covered by NonBlankStr, kept as a guard)
)
_ACCEPTED = (
    "src/app/models.py",
    "models.py",
    "a/b/c/d/e.py",
    "src/app/.hidden.py",  # a leading dot on a *segment* is a real filename
)


@pytest.mark.parametrize("path", _REJECTED)
def test_repo_evidence_rejects_non_repo_relative_paths(path: str) -> None:
    """Every citation this product prints resolves against a repository root.

    An absolute path in a citation points at the analysis machine's disk, not
    at the user's repository, and a `..` segment points outside the tree that
    was analyzed at all. Either one is a citation the reader cannot check --
    CLAUDE.md rule 1's exact failure.
    """
    with pytest.raises(ValidationError):
        RepoEvidence(file=path, line=1)


@pytest.mark.parametrize("path", _ACCEPTED)
def test_repo_evidence_accepts_ordinary_repo_relative_paths(path: str) -> None:
    """The negative test above is worthless unless the positive direction is
    shown to still pass: a validator that rejected everything would satisfy it."""
    assert RepoEvidence(file=path, line=1).file == path


# Copied verbatim from spec 8.1's factor table. If the spec changes, this
# tuple changes with it in the same commit -- it is a transcription of the
# authority, not an independent opinion.
_SPEC_8_1_FACTORS = (
    "breaking_change_exposure",
    "blast_radius",
    "test_coverage_of_affected",
    "churn_on_affected",
    "analysis_coverage",
    "evidence_coverage",
    "constraint_pressure",
)


def test_risk_categories_match_the_spec_factor_table_exactly() -> None:
    """Both directions, deliberately.

    Phase 6 builds one RiskFactor per member of this enum and the report
    prints the value as the factor's name. A member the spec does not define
    is a factor with no documented threshold table; a spec factor with no
    member is a factor that silently never gets computed. `==` on sorted
    tuples catches both; `all(x in y)` catches only one.
    """
    assert tuple(sorted(c.value for c in RiskCategory)) == tuple(sorted(_SPEC_8_1_FACTORS))
