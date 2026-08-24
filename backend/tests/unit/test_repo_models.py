from datetime import UTC, date, datetime

import pytest
from pydantic import TypeAdapter, ValidationError

from upgradepilot.models.enums import (
    Confidence,
    DependencyRole,
    ManifestKind,
    RiskLevel,
    UsageKind,
    VersionConfidence,
)
from upgradepilot.models.inputs import (
    DependencySpec,
    LocalRepoRef,
    RemoteRepoRef,
    RepoRef,
    UserConstraints,
)
from upgradepilot.models.repo import (
    AffectedFile,
    CommitRecord,
    DetectedVersion,
    Manifest,
    RepoAnalysis,
    SkippedFile,
    SymbolInventory,
    UsageSite,
)


def site(symbol: str, confidence: Confidence, file: str = "src/app/models.py", line: int = 1):
    return UsageSite(
        file=file,
        line=line,
        column=0,
        symbol=symbol,
        kind=UsageKind.METHOD_CALL,
        confidence=confidence,
        snippet=f"{symbol}()",
    )


def test_repo_ref_discriminates_remote_and_local() -> None:
    adapter = TypeAdapter(RepoRef)

    remote = adapter.validate_python(
        {"kind": "remote", "url": "https://github.com/acme/payment-service"}
    )
    local = adapter.validate_python({"kind": "local", "path": "/Users/nzrsrd/Code/demo"})

    assert isinstance(remote, RemoteRepoRef)
    assert isinstance(local, LocalRepoRef)
    with pytest.raises(ValidationError):
        adapter.validate_python({"kind": "ftp", "url": "ftp://x"})


def test_dependency_spec_rejects_blank_versions() -> None:
    with pytest.raises(ValidationError):
        DependencySpec(name="pydantic", current_version="", target_version="2.13.4")


def test_dependency_spec_rejects_an_unchanged_version() -> None:
    """Analyzing 1.10.13 -> 1.10.13 is a user error worth catching at the boundary."""
    with pytest.raises(ValidationError) as excinfo:
        DependencySpec(name="pydantic", current_version="1.10.13", target_version="1.10.13")
    assert "differ" in str(excinfo.value)


def test_user_constraints_defaults_are_permissive() -> None:
    constraints = UserConstraints()
    assert constraints.zero_downtime is False
    assert constraints.minimize_effort is False
    assert constraints.deadline is None
    assert constraints.risk_tolerance is RiskLevel.MEDIUM


def test_user_constraints_accepts_a_deadline() -> None:
    constraints = UserConstraints(zero_downtime=True, deadline=date(2026, 9, 1))
    assert constraints.deadline == date(2026, 9, 1)


def test_symbol_inventory_counts_sites_per_symbol() -> None:
    inventory = SymbolInventory.from_sites(
        [
            site("validator", Confidence.HIGH, "src/app/models.py", 10),
            site("validator", Confidence.HIGH, "src/app/other.py", 4),
            site("dict", Confidence.MEDIUM, "src/app/service.py", 20),
        ]
    )

    assert inventory.by_symbol["validator"].count == 2
    assert set(inventory.by_symbol["validator"].files) == {"src/app/models.py", "src/app/other.py"}
    assert inventory.by_symbol["dict"].count == 1


def test_symbol_confidence_is_the_best_of_its_sites() -> None:
    """Spec 7.1: a symbol is high-confidence if ANY site is high-confidence."""
    inventory = SymbolInventory.from_sites(
        [
            site("dict", Confidence.LOW, "src/util.py", 3),
            site("dict", Confidence.HIGH, "src/app/models.py", 7),
            site("dict", Confidence.MEDIUM, "src/app/service.py", 9),
        ]
    )
    assert inventory.by_symbol["dict"].confidence is Confidence.HIGH


def test_symbol_confidence_medium_when_no_high_site_exists() -> None:
    inventory = SymbolInventory.from_sites(
        [
            site("dict", Confidence.LOW, "src/util.py", 3),
            site("dict", Confidence.MEDIUM, "src/app/service.py", 9),
        ]
    )
    assert inventory.by_symbol["dict"].confidence is Confidence.MEDIUM


def test_high_confidence_symbols_are_sorted_and_filtered() -> None:
    inventory = SymbolInventory.from_sites(
        [
            site("validator", Confidence.HIGH),
            site("Config", Confidence.HIGH),
            site("dict", Confidence.MEDIUM),
        ]
    )
    assert inventory.high_confidence_symbols() == ("Config", "validator")


def test_empty_inventory_is_valid() -> None:
    inventory = SymbolInventory.from_sites([])
    assert inventory.entries == ()
    assert inventory.high_confidence_symbols() == ()


def test_symbol_inventory_cannot_disagree_with_its_own_symbols() -> None:
    """The old dict shape allowed {"foo": SymbolStat(symbol="bar")}. The tuple
    shape makes the key redundant, so drift is unconstructible."""
    inventory = SymbolInventory.from_sites(
        [site("validator", Confidence.HIGH, "src/app/models.py", 1)]
    )
    assert isinstance(inventory.entries, tuple)
    stat = inventory.by_symbol["validator"]
    assert stat.symbol == "validator"
    # There is no way to construct an entry whose dict key and .symbol disagree,
    # because there is no dict key anymore -- by_symbol is derived from .symbol.
    assert all(key == value.symbol for key, value in inventory.by_symbol.items())


def test_affected_file_requires_at_least_one_usage_site() -> None:
    with pytest.raises(ValidationError):
        AffectedFile(path="src/app/models.py", usage_sites=(), symbols=(), is_test=False)


def test_affected_file_derives_symbols_from_sites() -> None:
    affected = AffectedFile.from_sites(
        path="src/app/models.py",
        sites=[site("validator", Confidence.HIGH), site("Config", Confidence.HIGH)],
        is_test=False,
        commit_count=3,
        last_modified=datetime(2026, 8, 1, tzinfo=UTC),
    )
    assert affected.symbols == ("Config", "validator")
    assert affected.commit_count == 3


def test_affected_file_usage_sites_cannot_be_emptied_after_construction() -> None:
    """An AffectedFile with zero usage sites claims a file is affected with
    nothing to show for it -- the same invariant as RiskFactor.evidence."""
    affected = AffectedFile.from_sites(
        path="src/app/models.py",
        sites=[site("validator", Confidence.HIGH)],
        is_test=False,
    )
    assert isinstance(affected.usage_sites, tuple)
    with pytest.raises(AttributeError):
        affected.usage_sites.clear()  # type: ignore[attr-defined]
    assert len(affected.usage_sites) == 1


def test_detected_version_records_provenance() -> None:
    detected = DetectedVersion(
        value="1.10.13",
        specifier="==1.10.13",
        source_manifest=Manifest(
            path="requirements.txt", kind=ManifestKind.REQUIREMENTS, declared_specifier="==1.10.13"
        ),
        confidence=VersionConfidence.EXACT,
        role=DependencyRole.DIRECT,
    )
    assert detected.confidence is VersionConfidence.EXACT
    assert detected.source_manifest.kind is ManifestKind.REQUIREMENTS


def test_repo_analysis_reports_discrepancy_against_the_stated_version() -> None:
    analysis = RepoAnalysis(
        commit_sha="a" * 40,
        languages={"Python": 0.92, "TypeScript": 0.08},
        manifests=(
            Manifest(
                path="pyproject.toml", kind=ManifestKind.PYPROJECT, declared_specifier="^1.10"
            ),
        ),
        detected_version=DetectedVersion(
            value="1.10.13",
            specifier="^1.10",
            source_manifest=Manifest(
                path="pyproject.toml", kind=ManifestKind.PYPROJECT, declared_specifier="^1.10"
            ),
            confidence=VersionConfidence.RANGE,
            role=DependencyRole.DIRECT,
        ),
        total_python_files=40,
        analyzed_files=38,
        skipped_files=(SkippedFile(path="src/broken.py", reason="SyntaxError at line 3"),),
        affected_files=(),
        symbol_inventory=SymbolInventory.from_sites([]),
        commit_records=(
            CommitRecord(
                sha="b" * 40,
                timestamp=datetime(2026, 8, 20, tzinfo=UTC),
                files=("src/app/models.py",),
            ),
        ),
        test_paths=("tests/test_models.py",),
    )

    assert analysis.version_discrepancy(stated="1.9.0") == ("1.9.0", "1.10.13")
    assert analysis.version_discrepancy(stated="1.10.13") is None
    assert analysis.skipped_ratio == pytest.approx(1 / 40)


def test_repo_analysis_skipped_ratio_is_zero_for_an_empty_repo() -> None:
    analysis = RepoAnalysis(
        commit_sha=None,
        languages={},
        manifests=(),
        detected_version=None,
        total_python_files=0,
        analyzed_files=0,
        skipped_files=(),
        affected_files=(),
        symbol_inventory=SymbolInventory.from_sites([]),
        commit_records=(),
        test_paths=(),
    )
    assert analysis.skipped_ratio == 0.0


def test_repo_analysis_skipped_files_cannot_be_emptied_after_construction() -> None:
    """skipped_ratio feeds a confidence ceiling; a mutable collection would let
    that ceiling be moved after it was computed."""
    analysis = RepoAnalysis(
        commit_sha=None,
        languages={},
        manifests=(),
        detected_version=None,
        total_python_files=10,
        analyzed_files=9,
        skipped_files=(SkippedFile(path="src/broken.py", reason="SyntaxError"),),
        affected_files=(),
        symbol_inventory=SymbolInventory.from_sites([]),
        commit_records=(),
        test_paths=(),
    )
    assert isinstance(analysis.skipped_files, tuple)
    with pytest.raises(AttributeError):
        analysis.skipped_files.clear()  # type: ignore[attr-defined]
    assert analysis.skipped_ratio == pytest.approx(1 / 10)


def test_a_whitespace_only_sha_is_rejected() -> None:
    """min_length=7 alone accepts seven spaces."""
    with pytest.raises(ValidationError) as excinfo:
        CommitRecord(
            sha="       ",
            timestamp=datetime(2026, 8, 20, tzinfo=UTC),
            files=(),
        )
    errors = excinfo.value.errors()
    assert any(e["loc"] == ("sha",) and e["type"] == "string_too_short" for e in errors)


def test_a_whitespace_only_symbol_is_rejected() -> None:
    """Symbols feed the exact-element $contains join; a padded symbol matches
    nothing."""
    with pytest.raises(ValidationError) as excinfo:
        UsageSite(
            file="src/app/models.py",
            line=1,
            column=0,
            symbol="   ",
            kind=UsageKind.METHOD_CALL,
            confidence=Confidence.HIGH,
            snippet="foo()",
        )
    errors = excinfo.value.errors()
    assert any(e["loc"] == ("symbol",) and e["type"] == "string_too_short" for e in errors)
