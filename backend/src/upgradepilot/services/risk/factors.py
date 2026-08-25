"""Extracting spec 8.1's seven risk factors, mechanically.

No model is called here. Every level comes from a measured number and the
threshold table beside it, which is what "reproducible and unit-testable
without an LLM" means in practice: each factor below has a test that feeds it
a repository shape and asserts a level, and each boundary has a test that
sits on it.

**A factor with nothing to cite is omitted, never emitted empty.**
`RiskFactor.evidence` has `min_length=1`, so the type already refuses an
uncited factor; the choice this module makes is what to do about that, and
the choice is silence. The alternative -- reaching for an unrelated
repository line so the constructor accepts -- is a fabricated citation, and a
fabricated citation is worse than a missing factor by exactly the margin this
whole project is about. An omitted factor is visible: `RiskAnalysis` records
a confidence ceiling when the set comes out empty, and the report prints the
factors it has rather than seven rows of which some are furniture.

**Evidence is capped and the cap is reported.** A factor citing forty usage
sites is not more trustworthy than one citing six, and it is unreadable. The
detail line says how many exist, so the cap never reads as the total.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date

from upgradepilot.models.enums import (
    Confidence,
    RiskCategory,
    RiskLevel,
    Severity,
)
from upgradepilot.models.evidence import (
    BreakingChange,
    ConstraintEvidence,
    DocEvidence,
    EvidenceRef,
    RepoEvidence,
    RiskFactor,
)
from upgradepilot.models.inputs import UserConstraints
from upgradepilot.models.repo import AffectedFile, RepoAnalysis, UsageSite
from upgradepilot.services.analysis.layout import corresponding_test_paths
from upgradepilot.services.risk.thresholds import (
    CHURN_ACTIVE_COMMITS,
    THRESHOLDS,
    Threshold,
)

MAX_EVIDENCE_PER_FACTOR = 6
"""Evidence refs kept on one factor. See the module docstring."""

SEVERITY_AS_RISK: dict[Severity, RiskLevel] = {
    Severity.LOW: RiskLevel.LOW,
    Severity.MEDIUM: RiskLevel.MEDIUM,
    Severity.HIGH: RiskLevel.HIGH,
}
"""A documented change's severity, read as a risk level.

Spelled out rather than `RiskLevel(change.severity.value)`, which happens to
work because the two enums share their member values today. Two enums that
agree by coincidence are two enums that will disagree after either one gains
a member, and the failure would be a `ValueError` deep inside factor
extraction rather than a compile-time gap here.
"""

SEVERITY_WEIGHT: dict[Severity, float] = {
    Severity.LOW: 0.3,
    Severity.MEDIUM: 0.6,
    Severity.HIGH: 1.0,
}
"""How much each documented severity contributes to the exposure metric.

Not linear in thirds: a high-severity break is more than three times the
problem of a low-severity one, because the low ones are typically
deprecations that keep working. The exact numbers are a judgement, which is
why they are written down here where a test can pin them rather than inlined
where they would read as arithmetic.
"""


@dataclass(frozen=True, slots=True)
class FactorInputs:
    """Everything the seven extractors read, gathered once.

    One bundle rather than seven parameter lists so that adding an input
    reaches every factor that needs it without touching the six that do not,
    and so `extract_factors` has one thing to build.
    """

    analysis: RepoAnalysis
    breaking_changes: tuple[BreakingChange, ...]
    constraints: UserConstraints
    today: date
    """Injected rather than read from the clock, so the deadline arm of
    `constraint_pressure` is testable at all. A factor that consults
    `date.today()` internally has a level that changes overnight."""


def _repo_evidence(site: UsageSite) -> RepoEvidence:
    return RepoEvidence(file=site.file, line=site.line, snippet=site.snippet)


def _first_sites(files: Iterable[AffectedFile]) -> list[RepoEvidence]:
    """One citation per file: its first usage site, in file order.

    The *first* site rather than an arbitrary one, so two runs over an
    unchanged repository cite the same lines -- a report whose citations move
    between identical runs cannot be diffed, and diffing two reports is how a
    team sees what an upgrade changed.
    """
    return [_repo_evidence(file.usage_sites[0]) for file in files]


def _capped(evidence: Sequence[EvidenceRef]) -> tuple[EvidenceRef, ...]:
    return tuple(evidence[:MAX_EVIDENCE_PER_FACTOR])


def _shown(evidence: Sequence[EvidenceRef]) -> str:
    """The "and there are more" clause, or nothing when the cap did not bind."""
    if len(evidence) <= MAX_EVIDENCE_PER_FACTOR:
        return ""
    return f" Showing {MAX_EVIDENCE_PER_FACTOR} of {len(evidence)} citations."


def _factor(
    threshold: Threshold,
    *,
    name: str,
    level: RiskLevel,
    detail: str,
    evidence: Sequence[EvidenceRef],
) -> RiskFactor | None:
    """Build a factor, or `None` when nothing supports it."""
    if not evidence:
        return None
    return RiskFactor(
        id=threshold.category.value,
        name=name,
        category=threshold.category,
        level=level,
        weight=threshold.weight,
        detail=detail + _shown(evidence),
        evidence=_capped(evidence),
    )


def _graded(threshold: Threshold, metric: float) -> RiskLevel:
    return threshold.level_for(metric)


# -- 1. breaking_change_exposure --------------------------------------------


def confirmed_exposures(
    analysis: RepoAnalysis, breaking_changes: Sequence[BreakingChange]
) -> dict[str, list[BreakingChange]]:
    """High-confidence symbols matched to the changes that document them.

    **High confidence only, and the restriction is the point.** A
    medium-confidence usage site is one the analyzer inferred through a
    receiver it resolved rather than one it saw imported; treating those as
    confirmed exposure would let a heuristic drive the clamp that overrides
    every other judgement in the system. The clamp is the strongest mechanism
    here, so it is fed only by the evidence the analyzer is surest of.

    Matching is exact, mirroring Chroma's `$contains` and
    `annotate_coverage`: a document about `ConfigDict` does not document
    `Config`.
    """
    high_confidence = set(analysis.symbol_inventory.high_confidence_symbols())
    matched: dict[str, list[BreakingChange]] = {}
    for change in breaking_changes:
        for symbol in change.affected_symbols:
            if symbol in high_confidence:
                matched.setdefault(symbol, []).append(change)
    return matched


def _sites_for_symbol(analysis: RepoAnalysis, symbol: str) -> list[UsageSite]:
    return [
        site
        for affected in analysis.affected_files
        for site in affected.usage_sites
        if site.symbol == symbol
    ]


def breaking_change_exposure(inputs: FactorInputs) -> RiskFactor | None:
    """Documented breaks, matched against symbols the AST proved are in use.

    Evidence pairs the two halves deliberately: the `DocEvidence` says what
    changed and the `RepoEvidence` says where this repository does it. Either
    alone is an assertion -- "pydantic renamed `validator`" is true of every
    repository, and "you use `validator` at line 12" is not a risk. The pair
    is the finding.
    """
    threshold = THRESHOLDS[RiskCategory.BREAKING_CHANGE_EXPOSURE]
    analysis = inputs.analysis
    high_confidence = analysis.symbol_inventory.high_confidence_symbols()
    matched = confirmed_exposures(analysis, inputs.breaking_changes)
    if not matched:
        return None

    per_symbol = {
        symbol: max(SEVERITY_WEIGHT[change.severity] for change in changes)
        for symbol, changes in matched.items()
    }
    metric = sum(per_symbol.values()) / len(high_confidence) if high_confidence else 0.0

    evidence: list[EvidenceRef] = []
    for symbol in sorted(matched):
        worst = max(matched[symbol], key=lambda change: SEVERITY_WEIGHT[change.severity])
        evidence.append(
            DocEvidence(
                source_id=worst.source.source_id,
                chunk_id=worst.source.chunk_id,
                relevance=worst.source.relevance,
            )
        )
        sites = _sites_for_symbol(analysis, symbol)
        if sites:
            evidence.append(_repo_evidence(sites[0]))

    return _factor(
        threshold,
        name="Documented breaking changes in symbols this repository uses",
        level=_graded(threshold, metric),
        detail=(
            f"{len(matched)} of {len(high_confidence)} high-confidence symbol(s) are "
            f"named by a documented breaking change ({threshold.metric}: "
            f"{metric:.2f})."
        ),
        evidence=evidence,
    )


# -- 2. blast_radius --------------------------------------------------------


def blast_radius(inputs: FactorInputs) -> RiskFactor | None:
    """How much of the repository touches the dependency.

    The denominator is `total_python_files`, **not** `analyzed_files`, and
    this is a correction to spec 8.1's wording rather than a reading of it.
    `analyzed_files` counts the files candidate selection admitted, and
    candidate selection admits files precisely because they mention the
    dependency -- so affected ÷ analyzed is close to 1 for every repository
    ever analysed and measures nothing. Spec 8.1's own name for the factor is
    "blast radius", which is a share of the codebase, and a share of the
    codebase needs the codebase in the denominator.
    """
    threshold = THRESHOLDS[RiskCategory.BLAST_RADIUS]
    analysis = inputs.analysis
    affected = [file for file in analysis.affected_files if not file.is_test]
    if not affected or analysis.total_python_files == 0:
        return None

    metric = len(affected) / analysis.total_python_files
    return _factor(
        threshold,
        name="Share of the codebase that uses this dependency",
        level=_graded(threshold, metric),
        detail=(
            f"{len(affected)} of {analysis.total_python_files} Python file(s) use the "
            f"dependency ({metric:.0%})."
        ),
        evidence=_first_sites(affected),
    )


# -- 3. test_coverage_of_affected -------------------------------------------


def untested_affected_files(inputs: FactorInputs) -> RiskFactor | None:
    """Whether the files that must change have tests that would notice.

    Named for the *gap* rather than for its `RiskCategory`
    (`test_coverage_of_affected`), for two reasons that happen to point the
    same way. The metric here is the share of affected files *without* a
    test, so "coverage" names the complement of what the function returns;
    and a module-level callable whose name begins with `test_` is collected
    by pytest as a test case the moment any test module imports it, which
    fails as a missing fixture rather than as the naming collision it is.
    The category keeps the spec's name; the function says what it measures.

    A *locatable* test, by filename convention (`test_<stem>.py` /
    `<stem>_test.py`), which is a weaker claim than "these lines are covered"
    and is stated as such in the detail. Real coverage needs the suite to run,
    which this system deliberately does not do -- executing an untrusted
    repository is a different product with a different threat model. The
    honest version of the weaker claim is more useful than a stronger one we
    cannot support.
    """
    threshold = THRESHOLDS[RiskCategory.TEST_COVERAGE_OF_AFFECTED]
    analysis = inputs.analysis
    affected = [file for file in analysis.affected_files if not file.is_test]
    if not affected:
        return None

    untested = [
        file for file in affected if not corresponding_test_paths(file.path, analysis.test_paths)
    ]
    metric = len(untested) / len(affected)
    cited = untested or affected
    return _factor(
        threshold,
        name="Affected files with no locatable test",
        level=_graded(threshold, metric),
        detail=(
            f"{len(untested)} of {len(affected)} affected file(s) have no test file "
            f"named for them ({metric:.0%}). Locatable by filename convention only: "
            "this system does not run the suite, so it cannot claim line coverage."
        ),
        evidence=_first_sites(cited),
    )


# -- 4. churn_on_affected ---------------------------------------------------


def churn_on_affected(inputs: FactorInputs) -> RiskFactor | None:
    """Recent commit activity on the files that must change.

    Returns `None` when **no** affected file has a commit count, which is the
    "we did not look" state `AffectedFile.commit_count`'s `int | None` exists
    to preserve: the repository has no `.git`, or its history could not be
    read. Grading that as low churn would print "these files are stable"
    where the truth is "we have no idea", and the two are opposite advice.
    A `0` is a different matter entirely and is graded: history *was* read and
    the file was not touched.
    """
    threshold = THRESHOLDS[RiskCategory.CHURN_ON_AFFECTED]
    affected = list(inputs.analysis.affected_files)
    known = [file for file in affected if file.commit_count is not None]
    if not known:
        return None

    active = [file for file in known if (file.commit_count or 0) >= CHURN_ACTIVE_COMMITS]
    metric = len(active) / len(known)
    cited = sorted(known, key=lambda file: (-(file.commit_count or 0), file.path))
    return _factor(
        threshold,
        name="Recent change activity on affected files",
        level=_graded(threshold, metric),
        detail=(
            f"{len(active)} of {len(known)} affected file(s) with readable history were "
            f"touched at least {CHURN_ACTIVE_COMMITS} time(s) in the window ({metric:.0%})."
        ),
        evidence=_first_sites(cited),
    )


# -- 5. analysis_coverage ---------------------------------------------------


def analysis_coverage(inputs: FactorInputs) -> RiskFactor | None:
    """How much of the repository this analysis could not see clearly.

    Two gaps, and the metric is the **larger** rather than the sum: they
    measure different populations -- files that would not parse, and usage
    sites the analyzer graded uncertain -- so adding them produces a number
    that is not a share of anything. The worse of the two is a share of
    something, and it is the one that should drive the level.
    """
    threshold = THRESHOLDS[RiskCategory.ANALYSIS_COVERAGE]
    analysis = inputs.analysis
    sites = [site for file in analysis.affected_files for site in file.usage_sites]
    low_sites = [site for site in sites if site.confidence is Confidence.LOW]

    skipped_ratio = analysis.skipped_ratio
    low_share = len(low_sites) / len(sites) if sites else 0.0
    metric = max(skipped_ratio, low_share)

    evidence: list[EvidenceRef] = [_repo_evidence(site) for site in low_sites]
    # A skipped file has no line to cite, because the reason it is here is
    # that it could not be parsed into lines. Line 1 exists in any non-empty
    # file and is the least-wrong anchor; the detail says what the citation
    # means so it is not read as "the problem is on line 1".
    evidence += [RepoEvidence(file=skipped.path, line=1) for skipped in analysis.skipped_files]

    return _factor(
        threshold,
        name="Parts of the repository this analysis could not read clearly",
        level=_graded(threshold, metric),
        detail=(
            f"{len(analysis.skipped_files)} of {analysis.total_python_files} Python "
            f"file(s) could not be parsed ({skipped_ratio:.0%}), and {len(low_sites)} of "
            f"{len(sites)} usage site(s) are low confidence ({low_share:.0%}). "
            "A citation to line 1 of an unparseable file points at the file, not at a "
            "problem on that line."
        ),
        evidence=evidence,
    )


# -- 6. evidence_coverage ---------------------------------------------------


def evidence_coverage(inputs: FactorInputs) -> RiskFactor | None:
    """High-confidence symbols with nothing documented behind them.

    The complement of the sufficiency gate, reported as risk rather than as a
    loop condition: the gate decides whether to keep searching, and this says
    what the search ended up not finding. A symbol the repository certainly
    uses and the corpus says nothing about is the case where this product
    knows least and a reader would assume it knows most.
    """
    threshold = THRESHOLDS[RiskCategory.EVIDENCE_COVERAGE]
    analysis = inputs.analysis
    high_confidence = analysis.symbol_inventory.high_confidence_symbols()
    if not high_confidence:
        return None

    documented = confirmed_exposures(analysis, inputs.breaking_changes)
    uncovered = [symbol for symbol in high_confidence if symbol not in documented]
    metric = len(uncovered) / len(high_confidence)

    evidence: list[EvidenceRef] = []
    for symbol in uncovered:
        sites = _sites_for_symbol(analysis, symbol)
        if sites:
            evidence.append(_repo_evidence(sites[0]))
    if not evidence:
        # Nothing is uncovered, so the supporting evidence is the documents
        # that cover everything -- cited rather than asserted, because "all
        # symbols are documented" is itself a claim.
        evidence = [
            DocEvidence(
                source_id=change.source.source_id,
                chunk_id=change.source.chunk_id,
                relevance=change.source.relevance,
            )
            for change in inputs.breaking_changes
        ]

    return _factor(
        threshold,
        name="High-confidence symbols with no documented change",
        level=_graded(threshold, metric),
        detail=(
            f"{len(uncovered)} of {len(high_confidence)} high-confidence symbol(s) have "
            f"no documented breaking change behind them ({metric:.0%})"
            + (f": {', '.join(uncovered)}." if uncovered else ".")
        ),
        evidence=evidence,
    )


# -- 7. constraint_pressure -------------------------------------------------

CONSTRAINT_WEIGHTS = {
    "zero_downtime": 0.4,
    "deadline_imminent": 0.4,
    "deadline_near": 0.2,
    "minimize_effort": 0.2,
    "low_risk_tolerance": 0.3,
}
"""How much each stated constraint contributes to pressure.

Capped at 1.0 by the metric below rather than by these summing to it: a user
may state every constraint at once, and a total above 1.0 is a real fact
about that request even though the level it maps to cannot go higher than
HIGH.
"""

DEADLINE_IMMINENT_DAYS = 14
DEADLINE_NEAR_DAYS = 30
"""A deadline inside two weeks is imminent; inside a month is near. Both
boundaries are inclusive, and a deadline already past is imminent -- the
alternative is treating an overdue migration as unconstrained."""


def constraint_pressure(inputs: FactorInputs) -> RiskFactor | None:
    """Pressure from what the user said they need, cited as what it is.

    Returns `None` when every constraint is at its permissive default, which
    is the honest reading: `UserConstraints`'s defaults exist so that an
    omitted constraint never silently tightens the recommendation, and a
    factor reporting "no pressure" would be a row of furniture in the report.

    The evidence is `ConstraintEvidence` -- see `models/evidence.py` for why
    a third variant was added rather than citing a repository line that has
    nothing to do with the constraint.
    """
    threshold = THRESHOLDS[RiskCategory.CONSTRAINT_PRESSURE]
    constraints = inputs.constraints
    pressure = 0.0
    evidence: list[EvidenceRef] = []
    reasons: list[str] = []

    if constraints.zero_downtime:
        pressure += CONSTRAINT_WEIGHTS["zero_downtime"]
        reasons.append("zero downtime is required")
        evidence.append(ConstraintEvidence(field="zero_downtime", value="true"))

    if constraints.deadline is not None:
        days = (constraints.deadline - inputs.today).days
        if days <= DEADLINE_IMMINENT_DAYS:
            pressure += CONSTRAINT_WEIGHTS["deadline_imminent"]
            reasons.append(f"the deadline is {days} day(s) away")
        elif days <= DEADLINE_NEAR_DAYS:
            pressure += CONSTRAINT_WEIGHTS["deadline_near"]
            reasons.append(f"the deadline is {days} day(s) away")
        evidence.append(
            ConstraintEvidence(field="deadline", value=constraints.deadline.isoformat())
        )

    if constraints.minimize_effort:
        pressure += CONSTRAINT_WEIGHTS["minimize_effort"]
        reasons.append("effort is to be minimised")
        evidence.append(ConstraintEvidence(field="minimize_effort", value="true"))

    if constraints.risk_tolerance is RiskLevel.LOW:
        pressure += CONSTRAINT_WEIGHTS["low_risk_tolerance"]
        reasons.append("stated risk tolerance is low")
        evidence.append(ConstraintEvidence(field="risk_tolerance", value="low"))

    if not evidence:
        return None

    metric = min(1.0, pressure)
    return _factor(
        threshold,
        name="Pressure from the stated constraints",
        level=_graded(threshold, metric),
        detail=(
            ("; ".join(reasons).capitalize() + "." if reasons else "A deadline is set.")
            + f" Combined pressure {metric:.2f}."
        ),
        evidence=evidence,
    )


EXTRACTORS = (
    breaking_change_exposure,
    evidence_coverage,
    blast_radius,
    untested_affected_files,
    analysis_coverage,
    churn_on_affected,
    constraint_pressure,
)
"""The seven, in the order the report prints them.

Ordered by what a reader wants first -- what is documented to break, then how
much is undocumented, then how far it spreads -- rather than by spec 8.1's
table order, which is alphabetical-ish and puts constraint pressure in the
middle of the repository facts.
"""


def extract_factors(inputs: FactorInputs) -> tuple[RiskFactor, ...]:
    """Every factor that has something to cite, in report order."""
    return tuple(
        factor for factor in (extract(inputs) for extract in EXTRACTORS) if factor is not None
    )
