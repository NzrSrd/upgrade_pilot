from collections.abc import Mapping
from datetime import UTC, date, datetime
from typing import Annotated, get_args, get_origin

import pytest
from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from upgradepilot.models.base import HonestModel
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
    LanguageShare,
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


@pytest.mark.parametrize(
    ("raw", "canonical", "root"),
    [
        ("pydantic", "pydantic", "pydantic"),
        ("Pydantic", "pydantic", "pydantic"),
        ("python-dateutil", "python-dateutil", "python_dateutil"),
        ("zope.interface", "zope-interface", "zope_interface"),
        ("ruamel_yaml", "ruamel-yaml", "ruamel_yaml"),
    ],
)
def test_dependency_spec_canonical_forms(raw: str, canonical: str, root: str) -> None:
    spec = DependencySpec(name=raw, current_version="1", target_version="2")
    assert spec.canonical_name == canonical
    assert spec.import_root == root


def test_import_root_is_documented_as_a_guess_and_this_case_proves_it() -> None:
    """`python-dateutil` imports as `dateutil`, not `python_dateutil`.

    This test asserts the WRONG-looking value on purpose. It is the honest
    record that `import_root` is a heuristic, so that a later reader who
    "fixes" it has to delete a test that explains why it is not a bug -- and
    so that the confidence reducer in analyzer.py cannot be dropped as
    unnecessary.
    """
    spec = DependencySpec(name="python-dateutil", current_version="1", target_version="2")
    assert spec.import_root == "python_dateutil"
    assert spec.import_root != "dateutil"


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


def _mapping_types_in(annotation: object) -> list[object]:
    """Every mapping type reachable inside a possibly-parameterized annotation.

    `isinstance(annotation, type)` is NOT enough, and getting that wrong is
    what made the previous version of the guard below undetectable-by-design:
    `dict[str, SymbolStat]` is a `types.GenericAlias`, so
    `isinstance(dict[str, SymbolStat], type)` is `False` and a predicate built
    on it never fires on the exact shape it exists to forbid.

    `get_origin` sees through the parameterization (`dict[str, X]` -> `dict`)
    and returns `None` for a bare `dict`, so both are covered. The recursion
    over `get_args` covers a mapping nested inside a union, an `Annotated`, or
    a container -- `dict[str, X] | None` and `tuple[dict[str, X], ...]` are
    both still stored mappings.
    """
    found: list[object] = []
    origin = get_origin(annotation)
    base = annotation if origin is None else origin
    if isinstance(base, type) and issubclass(base, Mapping):
        found.append(annotation)
    for arg in get_args(annotation):
        found.extend(_mapping_types_in(arg))
    return found


def _stored_mappings(model: type[BaseModel]) -> dict[str, object]:
    """Fields of `model` whose annotation can hold a mapping."""
    return {
        name: field.annotation
        for name, field in model.model_fields.items()
        if _mapping_types_in(field.annotation)
    }


@pytest.mark.parametrize(
    "annotation",
    [
        pytest.param(dict[str, SymbolStat], id="parameterized-dict"),
        pytest.param(dict, id="bare-dict"),
        pytest.param(Mapping[str, SymbolStat], id="abc-mapping"),
        pytest.param(dict[str, SymbolStat] | None, id="optional-dict"),
        pytest.param(tuple[dict[str, SymbolStat], ...], id="dict-in-a-tuple"),
        pytest.param(Annotated[dict[str, SymbolStat], Field()], id="annotated-dict"),
    ],
)
def test_the_mapping_detector_can_actually_see_a_stored_mapping(annotation: object) -> None:
    """Positive control for the guard below, and the whole lesson of this fix.

    A test that asserts an absence is worthless unless it can be shown to
    detect a presence. The previous version could not: it used
    `isinstance(field.annotation, type)`, which is `False` for every
    parameterized generic, so re-adding `stats_by_symbol: dict[str,
    SymbolStat]` -- precisely the drift-prone shape it forbade -- left it
    green. Each shape here must be seen, or the negative assertion below is
    vacuous again.
    """
    assert _mapping_types_in(annotation), f"detector is blind to {annotation!r}"


@pytest.mark.parametrize(
    "annotation",
    [
        pytest.param(tuple[SymbolStat, ...], id="tuple-of-models"),
        pytest.param(str, id="str"),
        pytest.param(int | None, id="optional-int"),
        pytest.param(tuple[str, ...], id="tuple-of-str"),
    ],
)
def test_the_mapping_detector_does_not_cry_wolf(annotation: object) -> None:
    """The other half of a trustworthy detector: it must not flag the shapes
    this package actually uses, or the guard below would be unsatisfiable and
    would be deleted rather than fixed."""
    assert _mapping_types_in(annotation) == []


def test_symbol_inventory_has_no_stored_lookup_to_drift_from() -> None:
    """The old dict shape allowed {"foo": SymbolStat(symbol="bar")}.

    Two earlier versions of this test could not fail. The first asserted
    `all(key == value.symbol for ... in by_symbol.items())` -- a tautology,
    since `by_symbol` builds those keys *from* `.symbol`. The second asserted
    the right thing with a predicate blind to parameterized generics. What is
    claimable is structural -- no stored field exists for the lookup to
    disagree with -- and the detector above is now proven able to see one.
    """
    assert "by_symbol" not in SymbolInventory.model_fields
    assert _stored_mappings(SymbolInventory) == {}


def test_reintroducing_the_stored_lookup_is_caught() -> None:
    """End-to-end positive control, using the exact field the reviewer added
    to prove the old guard was blind. This is a real pydantic model, not a
    bare annotation, so it exercises `model_fields` the way the guard does --
    the layer where `field.annotation` could have been normalised into
    something the detector misses."""

    class DriftyInventory(HonestModel):
        entries: tuple[SymbolStat, ...] = ()
        stats_by_symbol: dict[str, SymbolStat] = {}

    assert _stored_mappings(DriftyInventory) == {
        "stats_by_symbol": dict[str, SymbolStat],
    }


def test_the_guard_covers_every_field_not_just_the_first() -> None:
    """A loop bug that stopped at the first field would leave a mapping added
    later invisible."""

    class ManyFields(HonestModel):
        a: str = "x"
        b: tuple[str, ...] = ()
        c: dict[str, float] = {}

    assert set(_stored_mappings(ManyFields)) == {"c"}


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


def test_commit_count_distinguishes_unknown_from_no_churn() -> None:
    site_ = site("X", Confidence.LOW, "a.py", 1)
    unknown = AffectedFile(path="a.py", usage_sites=(site_,))
    calm = AffectedFile(path="a.py", usage_sites=(site_,), commit_count=0)

    assert unknown.commit_count is None
    assert calm.commit_count == 0
    # The point of the change: these must not compare equal, because a factor
    # that treats them alike reports "stable" for a repository it never read.
    assert unknown.commit_count != calm.commit_count


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
        languages=(
            LanguageShare(language="Python", share=0.92, file_count=37),
            LanguageShare(language="TypeScript", share=0.08, file_count=3),
        ),
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
        languages=(),
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
        languages=(),
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
    languages: tuple[LanguageShare, ...] | None = None,
    detected_version: DetectedVersion | None = None,
) -> RepoAnalysis:
    return RepoAnalysis(
        commit_sha=commit_sha,
        languages=languages if languages is not None else (),
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
    ("language", "share", "file_count", "expected_type"),
    [
        pytest.param("", 0.5, 1, "string_too_short", id="blank-name"),
        pytest.param("   ", 0.5, 1, "string_too_short", id="whitespace-only-name"),
        pytest.param("Python", 0.0, 1, "greater_than", id="zero-share"),
        pytest.param("Python", -3.0, 1, "greater_than", id="negative-share"),
        pytest.param("Python", 1e9, 1, "less_than_equal", id="share-above-one"),
        pytest.param("Python", 0.5, 0, "greater_than_equal", id="zero-file-count"),
    ],
)
def test_language_share_bounds(
    language: str, share: float, file_count: int, expected_type: str
) -> None:
    """A language with a zero share is a language with no files, and listing
    it claims a presence the count contradicts -- so `share` is `gt=0.0`, not
    `ge=0.0`."""
    with pytest.raises(ValidationError) as excinfo:
        LanguageShare(language=language, share=share, file_count=file_count)
    errors = excinfo.value.errors()
    assert any(e["type"] == expected_type for e in errors), errors


def test_repo_analysis_rejects_duplicate_languages() -> None:
    """A duplicate language made the old dict silently drop one entry; as a
    tuple it would instead be double-counted by any consumer that sums."""
    with pytest.raises(ValidationError) as excinfo:
        _repo_analysis(
            total_python_files=0,
            analyzed_files=0,
            skipped_count=0,
            languages=(
                LanguageShare(language="Python", share=0.5, file_count=5),
                LanguageShare(language="Python", share=0.5, file_count=5),
            ),
        )
    assert "duplicate languages" in str(excinfo.value)


def test_repo_analysis_rejects_language_shares_that_do_not_sum_to_one() -> None:
    """The shares are computed over files with a recognised extension, so they
    partition that set and must total 1.0."""
    with pytest.raises(ValidationError) as excinfo:
        _repo_analysis(
            total_python_files=0,
            analyzed_files=0,
            skipped_count=0,
            languages=(LanguageShare(language="Python", share=0.4, file_count=4),),
        )
    assert "must total 1.0" in str(excinfo.value)


def test_repo_analysis_accepts_language_shares_that_sum_to_one() -> None:
    """The negative tests above are worthless unless this positive case is
    shown to still pass."""
    analysis = _repo_analysis(
        total_python_files=0,
        analyzed_files=0,
        skipped_count=0,
        languages=(
            LanguageShare(language="Python", share=0.9, file_count=9),
            LanguageShare(language="TypeScript", share=0.1, file_count=1),
        ),
    )
    assert [share.language for share in analysis.languages] == ["Python", "TypeScript"]


def test_repo_analysis_accepts_no_languages() -> None:
    """An empty repository (or one whose walk found nothing recognised)
    reports no shares rather than being forced to invent a total."""
    analysis = _repo_analysis(total_python_files=0, analyzed_files=0, skipped_count=0)
    assert analysis.languages == ()


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
