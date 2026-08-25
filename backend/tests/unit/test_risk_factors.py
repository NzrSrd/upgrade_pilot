"""The seven factors, and the table that grades them.

Spec 8.1: "Levels come from a documented threshold table, making each level
reproducible and unit-testable without an LLM." These tests are what that
sentence buys -- every factor gets a repository shape and an asserted level,
and every boundary in the table gets a case sitting exactly on it, because a
boundary written `>` where it should be `>=` is invisible everywhere except
at the boundary.

Nothing here builds a graph or calls a model. That is the point: if a level
needed either, it would not be reproducible.
"""

from datetime import date

import pytest

from upgradepilot.models.enums import (
    Confidence,
    DependencyRole,
    ManifestKind,
    RiskCategory,
    RiskLevel,
    Severity,
    SourceType,
    UsageKind,
    VersionConfidence,
)
from upgradepilot.models.evidence import (
    BreakingChange,
    ConstraintEvidence,
    DocEvidence,
    RepoEvidence,
    SourceRef,
)
from upgradepilot.models.inputs import UserConstraints
from upgradepilot.models.repo import (
    AffectedFile,
    DetectedVersion,
    Manifest,
    RepoAnalysis,
    SkippedFile,
    SymbolInventory,
    UsageSite,
)
from upgradepilot.services.risk.factors import (
    EXTRACTORS,
    MAX_EVIDENCE_PER_FACTOR,
    SEVERITY_AS_RISK,
    FactorInputs,
    analysis_coverage,
    blast_radius,
    breaking_change_exposure,
    churn_on_affected,
    constraint_pressure,
    evidence_coverage,
    extract_factors,
    untested_affected_files,
)
from upgradepilot.services.risk.thresholds import THRESHOLDS

TODAY = date(2026, 8, 25)


# -- fixtures ---------------------------------------------------------------


def a_site(
    file: str,
    line: int = 1,
    symbol: str = "validator",
    confidence: Confidence = Confidence.HIGH,
) -> UsageSite:
    return UsageSite(
        file=file,
        line=line,
        column=0,
        symbol=symbol,
        kind=UsageKind.DECORATOR,
        confidence=confidence,
        snippet="    @validator('x')",
    )


def an_affected_file(
    path: str,
    *,
    symbols: tuple[str, ...] = ("validator",),
    confidence: Confidence = Confidence.HIGH,
    commit_count: int | None = 0,
    is_test: bool = False,
) -> AffectedFile:
    return AffectedFile(
        path=path,
        usage_sites=tuple(
            a_site(path, line=index + 1, symbol=symbol, confidence=confidence)
            for index, symbol in enumerate(symbols)
        ),
        is_test=is_test,
        commit_count=commit_count,
    )


def an_analysis(
    *,
    affected: tuple[AffectedFile, ...] = (),
    total_python_files: int = 10,
    skipped: tuple[str, ...] = (),
    test_paths: tuple[str, ...] = (),
    role: DependencyRole = DependencyRole.DIRECT,
    reducers: tuple[str, ...] = (),
    with_version: bool = True,
) -> RepoAnalysis:
    sites = [site for file in affected for site in file.usage_sites]
    manifest = Manifest(path="pyproject.toml", kind=ManifestKind.PYPROJECT)
    return RepoAnalysis(
        commit_sha="abcdef1234567",
        detected_version=(
            DetectedVersion(
                value="1.10.13",
                specifier="==1.10.13",
                source_manifest=manifest,
                confidence=VersionConfidence.EXACT,
                role=role,
            )
            if with_version
            else None
        ),
        manifests=(manifest,),
        total_python_files=total_python_files,
        analyzed_files=len(affected),
        skipped_files=tuple(SkippedFile(path=path, reason="unparseable") for path in skipped),
        affected_files=affected,
        symbol_inventory=SymbolInventory.from_sites(sites),
        test_paths=test_paths,
        confidence_reducers=reducers,
    )


def a_change(
    source_id: str = "doc#validator",
    *,
    symbols: tuple[str, ...] = ("validator",),
    severity: Severity = Severity.HIGH,
) -> BreakingChange:
    return BreakingChange(
        id=source_id,
        title="A documented change",
        description="It changed.",
        severity=severity,
        affected_symbols=symbols,
        source=SourceRef(
            source_id=source_id,
            title="A documented change",
            source_type=SourceType.MIGRATION_GUIDE,
            url_or_reference="https://example.invalid/doc",
            chunk_id=f"{source_id}#chunk-0",
            relevance=0.8,
        ),
    )


def inputs_for(
    analysis: RepoAnalysis,
    *,
    changes: tuple[BreakingChange, ...] = (),
    constraints: UserConstraints | None = None,
) -> FactorInputs:
    return FactorInputs(
        analysis=analysis,
        breaking_changes=changes,
        constraints=constraints or UserConstraints(),
        today=TODAY,
    )


# -- the table --------------------------------------------------------------


def test_the_table_covers_every_category() -> None:
    """A factor added to `RiskCategory` without a row here would never be
    graded, and nothing else would notice: `extract_factors` would simply not
    produce it and the report would be one dimension short."""
    assert set(THRESHOLDS) == set(RiskCategory)


def test_every_extractor_has_a_row_and_every_row_an_extractor() -> None:
    assert len(EXTRACTORS) == len(THRESHOLDS)


@pytest.mark.parametrize("category", list(RiskCategory))
def test_a_metric_exactly_on_a_boundary_takes_the_higher_level(
    category: RiskCategory,
) -> None:
    """Boundaries are inclusive at the named value. Stated in the table's
    docstring because a reader checks a percentage against it by hand, and an
    exclusive boundary in code beside an inclusive one in their reading is a
    disagreement neither side can see."""
    threshold = THRESHOLDS[category]

    assert threshold.level_for(threshold.high_at) is RiskLevel.HIGH
    assert threshold.level_for(threshold.medium_at) is RiskLevel.MEDIUM
    assert threshold.level_for(threshold.medium_at - 0.001) is RiskLevel.LOW


def test_severity_is_mapped_rather_than_reconstructed() -> None:
    """The two enums share their member values today. Two enums that agree by
    coincidence are two that will disagree after either gains a member, and
    the failure would surface as a `ValueError` inside factor extraction."""
    assert set(SEVERITY_AS_RISK) == set(Severity)
    assert SEVERITY_AS_RISK[Severity.HIGH] is RiskLevel.HIGH


# -- 1. breaking_change_exposure --------------------------------------------


def test_exposure_matches_only_high_confidence_symbols() -> None:
    """A medium-confidence site is one the analyzer inferred through a
    resolved receiver rather than saw imported. Letting those feed the clamp
    -- the strongest mechanism in the system -- would put a heuristic in
    charge of the verdict."""
    analysis = an_analysis(
        affected=(an_affected_file("a.py", symbols=("dict",), confidence=Confidence.MEDIUM),)
    )

    factor = breaking_change_exposure(inputs_for(analysis, changes=(a_change(symbols=("dict",)),)))

    assert factor is None


def test_exposure_cites_the_document_and_the_line_together() -> None:
    """Either half alone is not a finding: "pydantic renamed `validator`" is
    true of every repository, and "you use `validator` at line 1" is not a
    risk. The pair is."""
    analysis = an_analysis(affected=(an_affected_file("a.py"),))

    factor = breaking_change_exposure(inputs_for(analysis, changes=(a_change(),)))

    assert factor is not None
    kinds = {evidence.kind for evidence in factor.evidence}
    assert kinds == {"doc", "repo"}
    doc = next(e for e in factor.evidence if isinstance(e, DocEvidence))
    repo = next(e for e in factor.evidence if isinstance(e, RepoEvidence))
    assert doc.chunk_id == "doc#validator#chunk-0"
    assert (repo.file, repo.line) == ("a.py", 1)


def test_exposure_is_absent_rather_than_low_when_nothing_is_documented() -> None:
    """An omitted factor is a gap the reader can see; a LOW factor with no
    matching document reads as "we checked and it is fine"."""
    analysis = an_analysis(affected=(an_affected_file("a.py"),))

    assert breaking_change_exposure(inputs_for(analysis)) is None


def test_exposure_grades_breadth_across_the_symbols_in_use() -> None:
    """One severe break among four confident symbols is narrower than four,
    and the metric says so -- while the clamp guarantees the verdict either
    way."""
    wide = an_analysis(
        affected=(an_affected_file("a.py", symbols=("validator", "Config")),),
    )
    narrow = an_analysis(
        affected=(
            an_affected_file(
                "a.py",
                symbols=("validator", "Config", "BaseModel", "parse_obj", "schema"),
            ),
        ),
    )
    changes = (a_change(symbols=("validator",)),)

    wide_factor = breaking_change_exposure(inputs_for(wide, changes=changes))
    narrow_factor = breaking_change_exposure(inputs_for(narrow, changes=changes))

    assert wide_factor is not None and narrow_factor is not None
    assert wide_factor.level is RiskLevel.HIGH
    assert narrow_factor.level is RiskLevel.MEDIUM


# -- 2. blast_radius --------------------------------------------------------


@pytest.mark.parametrize(
    ("affected_count", "total", "expected"),
    [
        (3, 10, RiskLevel.HIGH),
        (1, 10, RiskLevel.MEDIUM),
        (1, 11, RiskLevel.LOW),
    ],
)
def test_blast_radius_grades_a_share_of_the_whole_repository(
    affected_count: int, total: int, expected: RiskLevel
) -> None:
    """The denominator is `total_python_files`, not `analyzed_files`.
    Candidate selection admits files *because* they mention the dependency,
    so affected ÷ analyzed is near 1 for every repository and measures
    nothing."""
    affected = tuple(an_affected_file(f"a{index}.py") for index in range(affected_count))
    analysis = an_analysis(affected=affected, total_python_files=total)

    factor = blast_radius(inputs_for(analysis))

    assert factor is not None
    assert factor.level is expected


def test_blast_radius_ignores_test_files() -> None:
    """A test file using the dependency is not blast radius; it is the thing
    that would catch the blast."""
    analysis = an_analysis(
        affected=(
            an_affected_file("a.py"),
            an_affected_file("tests/test_a.py", is_test=True),
        ),
        total_python_files=10,
    )

    factor = blast_radius(inputs_for(analysis))

    assert factor is not None
    assert "1 of 10" in factor.detail


def test_blast_radius_is_omitted_when_nothing_is_affected() -> None:
    assert blast_radius(inputs_for(an_analysis())) is None


# -- 3. test_coverage_of_affected -------------------------------------------


def test_untested_affected_files_raise_the_level() -> None:
    analysis = an_analysis(
        affected=(an_affected_file("a.py"), an_affected_file("b.py")),
        test_paths=("tests/test_a.py",),
    )

    factor = untested_affected_files(inputs_for(analysis))

    assert factor is not None
    assert factor.level is RiskLevel.MEDIUM
    assert "1 of 2" in factor.detail


def test_the_coverage_claim_says_it_is_only_a_filename_match() -> None:
    """Real coverage needs the suite to run, which this system deliberately
    does not do. The weaker claim stated honestly beats a stronger one
    nothing supports."""
    analysis = an_analysis(affected=(an_affected_file("a.py"),))

    factor = untested_affected_files(inputs_for(analysis))

    assert factor is not None
    assert "does not run the suite" in factor.detail


def test_fully_tested_affected_files_still_cite_something() -> None:
    """`RiskFactor.evidence` has `min_length=1`, so a LOW factor needs
    evidence too -- here the files that *do* have tests, which is what the
    claim rests on."""
    analysis = an_analysis(affected=(an_affected_file("a.py"),), test_paths=("tests/test_a.py",))

    factor = untested_affected_files(inputs_for(analysis))

    assert factor is not None
    assert factor.level is RiskLevel.LOW
    assert factor.evidence


# -- 4. churn_on_affected ---------------------------------------------------


def test_churn_is_omitted_when_history_could_not_be_read() -> None:
    """`commit_count=None` is "we did not look". Grading it as low churn
    would print "these files are stable" where the truth is "we have no
    idea", and those are opposite advice."""
    analysis = an_analysis(affected=(an_affected_file("a.py", commit_count=None),))

    assert churn_on_affected(inputs_for(analysis)) is None


def test_a_read_history_showing_no_commits_is_graded_not_omitted() -> None:
    """The complement, and the reason `commit_count` is `int | None` rather
    than `int`: zero is a real, low-churn signal."""
    analysis = an_analysis(affected=(an_affected_file("a.py", commit_count=0),))

    factor = churn_on_affected(inputs_for(analysis))

    assert factor is not None
    assert factor.level is RiskLevel.LOW


@pytest.mark.parametrize(
    ("counts", "expected"),
    [
        ((5, 4), RiskLevel.HIGH),
        ((5, 0, 0, 0), RiskLevel.MEDIUM),
        ((1, 0, 0, 0, 0), RiskLevel.LOW),
    ],
)
def test_churn_grades_the_share_of_actively_changed_files(
    counts: tuple[int, ...], expected: RiskLevel
) -> None:
    affected = tuple(
        an_affected_file(f"a{index}.py", commit_count=count) for index, count in enumerate(counts)
    )

    factor = churn_on_affected(inputs_for(an_analysis(affected=affected)))

    assert factor is not None
    assert factor.level is expected


# -- 5. analysis_coverage ---------------------------------------------------


def test_analysis_coverage_takes_the_worse_of_its_two_gaps() -> None:
    """The two measure different populations -- unparseable files and
    uncertain usage sites -- so adding them produces a number that is not a
    share of anything."""
    analysis = an_analysis(
        affected=(an_affected_file("a.py", confidence=Confidence.LOW),),
        total_python_files=100,
        skipped=("broken.py",),
    )

    factor = analysis_coverage(inputs_for(analysis))

    assert factor is not None
    # 1% skipped, 100% of sites low confidence -> the larger drives the level.
    assert factor.level is RiskLevel.HIGH


def test_a_skipped_file_is_cited_at_line_one_and_says_so() -> None:
    """A file that would not parse has no line to cite. Line 1 is the
    least-wrong anchor, and the detail says what the citation means so it is
    not read as "the problem is on line 1"."""
    analysis = an_analysis(
        affected=(an_affected_file("a.py"),), skipped=("broken.py",), total_python_files=10
    )

    factor = analysis_coverage(inputs_for(analysis))

    assert factor is not None
    cited = {(e.file, e.line) for e in factor.evidence if isinstance(e, RepoEvidence)}
    assert ("broken.py", 1) in cited
    assert "points at the file, not at a problem on that line" in factor.detail


def test_analysis_coverage_is_omitted_when_nothing_was_missed() -> None:
    """A clean analysis has nothing to cite here, and a factor with nothing
    to cite is omitted rather than given a borrowed citation."""
    analysis = an_analysis(affected=(an_affected_file("a.py"),))

    assert analysis_coverage(inputs_for(analysis)) is None


# -- 6. evidence_coverage ---------------------------------------------------


def test_evidence_coverage_reports_the_symbols_nothing_documents() -> None:
    analysis = an_analysis(affected=(an_affected_file("a.py", symbols=("validator", "Config")),))

    factor = evidence_coverage(inputs_for(analysis, changes=(a_change(symbols=("validator",)),)))

    assert factor is not None
    assert factor.level is RiskLevel.HIGH
    assert "Config" in factor.detail
    cited = {e.file for e in factor.evidence if isinstance(e, RepoEvidence)}
    assert cited == {"a.py"}


def test_full_coverage_cites_the_documents_that_cover_it() -> None:
    """ "All symbols are documented" is itself a claim, so it is cited rather
    than asserted."""
    analysis = an_analysis(affected=(an_affected_file("a.py"),))

    factor = evidence_coverage(inputs_for(analysis, changes=(a_change(),)))

    assert factor is not None
    assert factor.level is RiskLevel.LOW
    assert all(isinstance(e, DocEvidence) for e in factor.evidence)


def test_evidence_coverage_is_omitted_without_high_confidence_symbols() -> None:
    analysis = an_analysis(affected=(an_affected_file("a.py", confidence=Confidence.MEDIUM),))

    assert evidence_coverage(inputs_for(analysis)) is None


# -- 7. constraint_pressure -------------------------------------------------


def test_default_constraints_produce_no_factor() -> None:
    """`UserConstraints`'s defaults exist so an omitted constraint never
    silently tightens the recommendation. A factor reporting "no pressure"
    would be a row of furniture in the report."""
    assert constraint_pressure(inputs_for(an_analysis())) is None


def test_a_constraint_is_cited_as_a_constraint() -> None:
    """The reason `ConstraintEvidence` exists. The alternative was citing an
    unrelated repository line so the constructor would accept -- a fabricated
    citation, which is worse than a missing factor by exactly the margin this
    project is about."""
    factor = constraint_pressure(
        inputs_for(an_analysis(), constraints=UserConstraints(zero_downtime=True))
    )

    assert factor is not None
    assert factor.evidence == (ConstraintEvidence(field="zero_downtime", value="true"),)


@pytest.mark.parametrize(
    ("constraints", "expected"),
    [
        (UserConstraints(zero_downtime=True), RiskLevel.MEDIUM),
        (UserConstraints(minimize_effort=True), RiskLevel.LOW),
        (
            UserConstraints(
                zero_downtime=True, deadline=date(2026, 9, 1), risk_tolerance=RiskLevel.LOW
            ),
            RiskLevel.HIGH,
        ),
    ],
)
def test_constraint_pressure_accumulates(constraints: UserConstraints, expected: RiskLevel) -> None:
    factor = constraint_pressure(inputs_for(an_analysis(), constraints=constraints))

    assert factor is not None
    assert factor.level is expected


def test_a_deadline_already_past_counts_as_imminent() -> None:
    """The alternative is treating an overdue migration as unconstrained."""
    factor = constraint_pressure(
        inputs_for(an_analysis(), constraints=UserConstraints(deadline=date(2026, 1, 1)))
    )

    assert factor is not None
    assert factor.level is RiskLevel.MEDIUM


def test_a_distant_deadline_is_cited_without_adding_pressure() -> None:
    """The citation is a fact about the request and belongs in the report;
    the pressure is a judgement about urgency and a deadline a year out is
    not urgent."""
    factor = constraint_pressure(
        inputs_for(an_analysis(), constraints=UserConstraints(deadline=date(2027, 8, 25)))
    )

    assert factor is not None
    assert factor.level is RiskLevel.LOW
    assert factor.evidence == (ConstraintEvidence(field="deadline", value="2027-08-25"),)


# -- the whole set ----------------------------------------------------------


def test_extract_factors_omits_what_it_cannot_cite() -> None:
    analysis = an_analysis(affected=(an_affected_file("a.py"),))

    factors = extract_factors(inputs_for(analysis, changes=(a_change(),)))

    categories = {factor.category for factor in factors}
    assert RiskCategory.CONSTRAINT_PRESSURE not in categories
    assert RiskCategory.ANALYSIS_COVERAGE not in categories
    assert RiskCategory.BREAKING_CHANGE_EXPOSURE in categories


def test_every_produced_factor_carries_evidence() -> None:
    """`min_length=1` already guarantees this at construction. Asserting it
    over the real extractor output is what catches an extractor that would
    have produced an empty one and instead silently returns `None`."""
    analysis = an_analysis(
        affected=(an_affected_file("a.py"), an_affected_file("b.py", commit_count=3)),
        skipped=("broken.py",),
        test_paths=("tests/test_a.py",),
    )

    factors = extract_factors(
        inputs_for(
            analysis,
            changes=(a_change(),),
            constraints=UserConstraints(zero_downtime=True),
        )
    )

    assert len(factors) == len(EXTRACTORS)
    assert all(factor.evidence for factor in factors)


def test_evidence_is_capped_and_the_cap_is_reported() -> None:
    """A factor citing forty usage sites is not more trustworthy than one
    citing six, and it is unreadable -- but a silent cap reads as the total."""
    affected = tuple(an_affected_file(f"a{index}.py") for index in range(20))
    analysis = an_analysis(affected=affected, total_python_files=40)

    factor = blast_radius(inputs_for(analysis))

    assert factor is not None
    assert len(factor.evidence) == MAX_EVIDENCE_PER_FACTOR
    assert f"Showing {MAX_EVIDENCE_PER_FACTOR} of 20" in factor.detail
