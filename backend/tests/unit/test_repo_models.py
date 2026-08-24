from collections.abc import Mapping
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
    SymbolStat,
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


def test_symbol_inventory_has_no_stored_lookup_to_drift_from() -> None:
    """The old dict shape allowed {"foo": SymbolStat(symbol="bar")}.

    The previous version of this test asserted
    `all(key == value.symbol for ... in by_symbol.items())`, which is a
    tautology: `by_symbol` builds those keys *from* `.symbol`, so it cannot
    fail whatever the model looks like. It read as assurance and provided
    none. What is actually claimable is structural -- that no stored field
    exists for the lookup to disagree with -- so that is what is asserted,
    and it goes red the moment someone re-adds a mapping field.
    """
    assert "by_symbol" not in SymbolInventory.model_fields
    mappings = {
        name: field.annotation
        for name, field in SymbolInventory.model_fields.items()
        if isinstance(field.annotation, type) and issubclass(field.annotation, Mapping)
    }
    assert mappings == {}, f"a stored mapping can drift from .symbol: {mappings}"


def test_symbol_inventory_rejects_duplicate_symbols() -> None:
    """The remaining drift vector once the dict is gone: two entries for one
    symbol make `by_symbol` silently drop one and `high_confidence_symbols`
    double-count it, and both feed evidence_coverage."""
    with pytest.raises(ValidationError) as excinfo:
        SymbolInventory(
            entries=(
                SymbolStat(
                    symbol="validator",
                    count=1,
                    files=("a.py",),
                    confidence=Confidence.HIGH,
                ),
                SymbolStat(
                    symbol="validator",
                    count=2,
                    files=("b.py",),
                    confidence=Confidence.LOW,
                ),
            )
        )
    assert "duplicate symbols" in str(excinfo.value)


def test_from_sites_never_produces_duplicate_entries() -> None:
    """The aggregating entry point must satisfy the invariant it feeds."""
    inventory = SymbolInventory.from_sites(
        [
            site("validator", Confidence.LOW, "a.py", 1),
            site("validator", Confidence.HIGH, "b.py", 2),
        ]
    )
    assert [stat.symbol for stat in inventory.entries] == ["validator"]


def test_symbol_stat_count_cannot_be_fewer_than_its_files() -> None:
    """A stat claiming one usage site across three files contradicts its own
    evidence, and `count` feeds the blast-radius figures the report quotes."""
    with pytest.raises(ValidationError) as excinfo:
        SymbolStat(
            symbol="validator",
            count=1,
            files=("a.py", "b.py", "c.py"),
            confidence=Confidence.HIGH,
        )
    assert "at least the number of files" in str(excinfo.value)


def test_symbol_stat_allows_several_sites_in_one_file() -> None:
    """count > len(files) is the normal case, not a defect: the constraint is
    `>=`, deliberately not `==`."""
    stat = SymbolStat(symbol="validator", count=5, files=("a.py",), confidence=Confidence.HIGH)
    assert stat.count == 5


def test_symbol_stat_rejects_a_zero_count() -> None:
    """A symbol with zero usage sites is not a detected symbol at all."""
    with pytest.raises(ValidationError) as excinfo:
        SymbolStat(symbol="validator", count=0, files=(), confidence=Confidence.HIGH)
    errors = excinfo.value.errors()
    assert any(e["loc"] == ("count",) and e["type"] == "greater_than_equal" for e in errors)


def test_symbol_stat_requires_at_least_one_file() -> None:
    """`files` is where the symbol was seen; empty means the count cites
    nothing."""
    with pytest.raises(ValidationError) as excinfo:
        SymbolStat(symbol="validator", count=1, files=(), confidence=Confidence.HIGH)
    errors = excinfo.value.errors()
    assert any(e["loc"] == ("files",) and e["type"] == "too_short" for e in errors)


def test_affected_file_rejects_sites_from_another_file() -> None:
    """A finding reported against one file while citing lines from another is
    CLAUDE.md rule 1's failure mode, and was structurally valid."""
    with pytest.raises(ValidationError) as excinfo:
        AffectedFile(
            path="src/app/models.py",
            usage_sites=(site("validator", Confidence.HIGH, "src/app/other.py", 3),),
        )
    assert "must all belong to path" in str(excinfo.value)


def test_affected_file_symbols_is_derived_not_stored() -> None:
    """Rule 21. As a field it could disagree with `usage_sites`; `usage_sites`
    carries the cited file and line, `symbols` is what the corpus is queried
    with, so drift between them means citing evidence for a symbol nobody
    uses."""
    assert "symbols" not in AffectedFile.model_fields
    assert "symbols" in AffectedFile.model_computed_fields

    affected = AffectedFile(
        path="src/app/models.py",
        usage_sites=(
            site("validator", Confidence.HIGH, "src/app/models.py", 1),
            site("Config", Confidence.HIGH, "src/app/models.py", 9),
            site("validator", Confidence.LOW, "src/app/models.py", 20),
        ),
    )
    assert affected.symbols == ("Config", "validator")
    # Still serialised, so the wire shape it had as a field is unchanged.
    assert affected.model_dump()["symbols"] == ("Config", "validator")


def test_affected_file_symbols_cannot_be_set_to_contradict_the_sites() -> None:
    """The point of item 3: a `symbols` that disagrees with `usage_sites` must
    be unconstructible by any route, including `model_copy`."""
    affected = AffectedFile.from_sites(
        path="src/app/models.py",
        sites=[site("validator", Confidence.HIGH)],
    )
    with pytest.raises(ValidationError):
        affected.symbols = ("anything",)  # type: ignore[misc]
    with pytest.raises(ValueError, match="not fields"):
        affected.model_copy(update={"symbols": ("anything",)})
    assert affected.symbols == ("validator",)


def test_affected_file_requires_at_least_one_usage_site() -> None:
    with pytest.raises(ValidationError):
        AffectedFile(path="src/app/models.py", usage_sites=(), is_test=False)


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


def test_affected_file_from_sites_rejects_an_empty_sites_list() -> None:
    """The most-likely-used entry point (`from_sites`, not the raw
    constructor) must enforce the same non-empty invariant."""
    with pytest.raises(ValidationError) as excinfo:
        AffectedFile.from_sites(path="src/app/models.py", sites=[], is_test=False)
    errors = excinfo.value.errors()
    assert any(e["loc"] == ("usage_sites",) and e["type"] == "too_short" for e in errors)


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


def _repo_analysis(
    *,
    total_python_files: int,
    analyzed_files: int,
    skipped_count: int,
    commit_sha: str | None = None,
    languages: dict[str, float] | None = None,
    detected_version: DetectedVersion | None = None,
) -> RepoAnalysis:
    return RepoAnalysis(
        commit_sha=commit_sha,
        languages=languages if languages is not None else {},
        manifests=(),
        detected_version=detected_version,
        total_python_files=total_python_files,
        analyzed_files=analyzed_files,
        skipped_files=tuple(
            SkippedFile(path=f"src/broken_{i}.py", reason="SyntaxError")
            for i in range(skipped_count)
        ),
        affected_files=(),
        symbol_inventory=SymbolInventory.from_sites([]),
        commit_records=(),
        test_paths=(),
    )


def test_repo_analysis_rejects_analyzed_plus_skipped_exceeding_total() -> None:
    """The reviewer constructed total=2 with 3 skipped_files and got
    skipped_ratio == 1.5 -- an out-of-range ratio that would corrupt the
    analysis_coverage risk factor and a confidence ceiling."""
    with pytest.raises(ValidationError) as excinfo:
        _repo_analysis(total_python_files=2, analyzed_files=0, skipped_count=3)
    message = str(excinfo.value)
    assert "analyzed_files=0" in message
    assert "skipped_files=3" in message
    assert "total_python_files=2" in message


def test_repo_analysis_accepts_the_boundary_case_where_counts_exactly_fill_total() -> None:
    """analyzed + skipped == total is the fully-accounted-for case."""
    analysis = _repo_analysis(total_python_files=5, analyzed_files=3, skipped_count=2)
    assert analysis.skipped_ratio == pytest.approx(2 / 5)


def test_repo_analysis_accepts_cap_excluded_files() -> None:
    """analyzed + skipped < total is legitimate: files excluded by the
    max_repo_files / max_repo_bytes caps are attempted by neither path."""
    analysis = _repo_analysis(total_python_files=10, analyzed_files=3, skipped_count=2)
    assert analysis.skipped_ratio == pytest.approx(2 / 10)


def test_repo_analysis_skipped_ratio_never_exceeds_one() -> None:
    for total, analyzed, skipped_count in [(0, 0, 0), (1, 1, 0), (1, 0, 1), (100, 40, 60)]:
        analysis = _repo_analysis(
            total_python_files=total, analyzed_files=analyzed, skipped_count=skipped_count
        )
        assert 0.0 <= analysis.skipped_ratio <= 1.0


def test_repo_analysis_rejects_a_blank_commit_sha() -> None:
    """`commit_sha=""` used to construct while `CommitRecord.sha` was already
    a `ShaStr`. This sha is what every file-and-line citation in the report is
    resolved against; two fields naming the same thing must be validated the
    same way."""
    with pytest.raises(ValidationError) as excinfo:
        _repo_analysis(total_python_files=0, analyzed_files=0, skipped_count=0, commit_sha="")
    errors = excinfo.value.errors()
    assert any(e["loc"] == ("commit_sha",) and e["type"] == "string_too_short" for e in errors)


def test_repo_analysis_rejects_a_whitespace_only_commit_sha() -> None:
    """The same reason ShaStr strips first: min_length=7 alone accepts seven
    spaces."""
    with pytest.raises(ValidationError):
        _repo_analysis(
            total_python_files=0, analyzed_files=0, skipped_count=0, commit_sha="       "
        )


def test_repo_analysis_accepts_a_short_sha_prefix_and_a_missing_one() -> None:
    """A 7-character prefix is what `git rev-parse --short` gives, and None is
    the legitimate "no commit yet" case."""
    assert (
        _repo_analysis(
            total_python_files=0, analyzed_files=0, skipped_count=0, commit_sha="abc1234"
        ).commit_sha
        == "abc1234"
    )
    assert (
        _repo_analysis(
            total_python_files=0, analyzed_files=0, skipped_count=0, commit_sha=None
        ).commit_sha
        is None
    )


@pytest.mark.parametrize(
    ("languages", "expected_type"),
    [
        pytest.param({"": 0.5}, "string_too_short", id="blank-name"),
        pytest.param({"   ": 0.5}, "string_too_short", id="whitespace-only-name"),
        pytest.param({"Python": -3.0}, "greater_than_equal", id="negative-share"),
        pytest.param({"Python": 1e9}, "less_than_equal", id="share-above-one"),
    ],
)
def test_repo_analysis_bounds_the_languages_map(
    languages: dict[str, float], expected_type: str
) -> None:
    """The reviewer constructed {"": -3.0, "Python": 1e9}: a blank language
    name and a nonsensical proportion, both stored."""
    with pytest.raises(ValidationError) as excinfo:
        _repo_analysis(total_python_files=0, analyzed_files=0, skipped_count=0, languages=languages)
    errors = excinfo.value.errors()
    assert any(e["loc"][0] == "languages" and e["type"] == expected_type for e in errors), errors


def test_repo_analysis_does_not_require_languages_to_sum_to_one() -> None:
    """The weaker defensible rule, chosen on purpose: whether the shares sum
    to 1.0 depends on what the Phase 2 analyzer counts, which does not exist
    yet. Bounds and non-blank keys is what can be defended today."""
    analysis = _repo_analysis(
        total_python_files=0,
        analyzed_files=0,
        skipped_count=0,
        languages={"Python": 0.4, "TypeScript": 0.1},
    )
    assert analysis.languages == {"Python": 0.4, "TypeScript": 0.1}


def test_usage_site_rejects_a_nonpositive_line() -> None:
    """Line numbers are 1-based; a 0 would cite a line that does not exist.
    The identical bound on RepoEvidence.line was already covered, this one
    was not."""
    with pytest.raises(ValidationError) as excinfo:
        UsageSite(
            file="src/app/models.py",
            line=0,
            column=0,
            symbol="validator",
            kind=UsageKind.METHOD_CALL,
            confidence=Confidence.HIGH,
        )
    errors = excinfo.value.errors()
    assert any(e["loc"] == ("line",) and e["type"] == "greater_than_equal" for e in errors)


def test_usage_site_rejects_a_negative_column() -> None:
    """Columns are 0-based (`ast` reports col_offset from 0), so 0 is valid
    and -1 is not."""
    with pytest.raises(ValidationError) as excinfo:
        UsageSite(
            file="src/app/models.py",
            line=1,
            column=-1,
            symbol="validator",
            kind=UsageKind.METHOD_CALL,
            confidence=Confidence.HIGH,
        )
    errors = excinfo.value.errors()
    assert any(e["loc"] == ("column",) and e["type"] == "greater_than_equal" for e in errors)


def test_version_discrepancy_strips_the_callers_stated_version() -> None:
    """`stated` is raw caller input, not a validated field. Without the strip,
    a pasted " 1.10.13 " would be reported as a version discrepancy against
    the version it actually equals -- a false finding in the report."""
    analysis = _repo_analysis(
        total_python_files=0,
        analyzed_files=0,
        skipped_count=0,
        detected_version=DetectedVersion(
            value="1.10.13",
            specifier="==1.10.13",
            source_manifest=Manifest(path="requirements.txt", kind=ManifestKind.REQUIREMENTS),
            confidence=VersionConfidence.EXACT,
            role=DependencyRole.DIRECT,
        ),
    )

    assert analysis.version_discrepancy(stated="  1.10.13\n") is None
    assert analysis.version_discrepancy(stated="  1.9.0  ") == ("1.9.0", "1.10.13")
