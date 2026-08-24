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
            category=RiskCategory.BREAKING_CHANGE,
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
        category=RiskCategory.BREAKING_CHANGE,
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
        category=RiskCategory.BREAKING_CHANGE,
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
