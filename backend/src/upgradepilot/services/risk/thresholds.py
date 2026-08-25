"""The threshold table: where a level comes from, written down once.

Spec 8.1: "Levels come from a documented threshold table, making each level
reproducible and unit-testable without an LLM." This module is that table.
Nothing here consults a model, reads a file or touches the network -- it is
arithmetic over numbers another module measured, which is what lets every
boundary have a test that pins it.

**Every metric is a gap**, phrased so that higher is worse, without
exception. `test_coverage_of_affected` therefore measures the share of
affected files *without* a locatable test, not the share with one. The
uniformity is not tidiness: with mixed directions, one comparison written the
wrong way round produces a plausible level that is exactly inverted, and
nothing downstream can tell. One direction means one comparison, in one
place.

**Boundaries are inclusive at the named value.** A metric of exactly
`high_at` is HIGH. Stated because "10% of files affected" is a number a
reader will check against the table by hand, and a boundary that is exclusive
in the code and inclusive in their reading is a disagreement neither side can
see.
"""

from dataclasses import dataclass

from upgradepilot.models.enums import RiskCategory, RiskLevel


@dataclass(frozen=True, slots=True)
class Threshold:
    """One factor's metric, its two boundaries, and its weight."""

    category: RiskCategory
    metric: str
    """What the number means, in one line. Printed in the factor's detail, so
    a reader can check the level against the table without reading code."""

    medium_at: float
    high_at: float
    weight: float
    """This factor's share of the weighted aggregate. Fixed per factor rather
    than measured, so the aggregate is a property of the table and not of the
    repository being analysed -- a weight that moved with the data would make
    two runs incomparable."""

    def level_for(self, metric: float) -> RiskLevel:
        if metric >= self.high_at:
            return RiskLevel.HIGH
        if metric >= self.medium_at:
            return RiskLevel.MEDIUM
        return RiskLevel.LOW


THRESHOLDS: dict[RiskCategory, Threshold] = {
    RiskCategory.BREAKING_CHANGE_EXPOSURE: Threshold(
        category=RiskCategory.BREAKING_CHANGE_EXPOSURE,
        metric=(
            "share of high-confidence symbols hit by a documented change, "
            "weighted by that change's severity"
        ),
        # The boundaries are deliberately low. This factor's metric is a mean
        # over every high-confidence symbol, so one severe documented break in
        # a repository using twenty symbols scores 0.05 -- and one severe
        # break in active use is not a low-risk situation. The clamp in
        # `aggregate.py` is what actually guarantees the verdict; these
        # boundaries grade *breadth*, and grading breadth generously is the
        # side to err on.
        medium_at=0.05,
        high_at=0.25,
        weight=1.0,
    ),
    RiskCategory.EVIDENCE_COVERAGE: Threshold(
        category=RiskCategory.EVIDENCE_COVERAGE,
        metric="share of high-confidence symbols with no documented change behind them",
        medium_at=0.20,
        high_at=0.50,
        weight=0.7,
    ),
    RiskCategory.BLAST_RADIUS: Threshold(
        category=RiskCategory.BLAST_RADIUS,
        metric="share of the repository's Python files that use the dependency",
        medium_at=0.10,
        high_at=0.30,
        weight=0.6,
    ),
    RiskCategory.TEST_COVERAGE_OF_AFFECTED: Threshold(
        category=RiskCategory.TEST_COVERAGE_OF_AFFECTED,
        metric="share of affected files with no locatable corresponding test",
        # Exact thirds, not 0.34 / 0.67. The detail line prints this metric as
        # a rounded percentage, and two untested files out of three prints as
        # "67%" -- which read against a boundary of 0.67 says HIGH while the
        # unrounded 0.6666... graded MEDIUM. A reader checking the level
        # against the table by hand would have been right and the code wrong.
        medium_at=1 / 3,
        high_at=2 / 3,
        weight=0.6,
    ),
    RiskCategory.ANALYSIS_COVERAGE: Threshold(
        category=RiskCategory.ANALYSIS_COVERAGE,
        metric=(
            "the larger of: share of Python files that could not be parsed, and "
            "share of usage sites graded low confidence"
        ),
        medium_at=0.05,
        high_at=0.20,
        weight=0.5,
    ),
    RiskCategory.CHURN_ON_AFFECTED: Threshold(
        category=RiskCategory.CHURN_ON_AFFECTED,
        metric=("share of affected files touched at least twice in the history window"),
        medium_at=0.25,
        high_at=0.50,
        weight=0.4,
    ),
    RiskCategory.CONSTRAINT_PRESSURE: Threshold(
        category=RiskCategory.CONSTRAINT_PRESSURE,
        metric="weighted pressure from the constraints the user stated",
        medium_at=1 / 3,
        high_at=2 / 3,
        weight=0.5,
    ),
}
"""One row per spec 8.1 factor. `test_the_table_covers_every_category`
asserts the correspondence, so a factor added to `RiskCategory` without a row
here fails immediately rather than silently never being graded."""

CHURN_ACTIVE_COMMITS = 2
"""Commits within the history window that make a file "actively changed".

One commit is every file in a repository's initial import, so a threshold of
one grades every fresh clone as maximum churn. Two is the smallest number
that distinguishes a file someone is working on from a file that merely
exists.
"""

AGGREGATE_MEDIUM_AT = 0.30
AGGREGATE_HIGH_AT = 0.60
"""Boundaries for the weighted mean of the factor levels (LOW=0.0, MEDIUM=0.5,
HIGH=1.0). Inclusive at the named value, like every boundary above."""
