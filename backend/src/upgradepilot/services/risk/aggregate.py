"""From seven factors to one verdict, and from evidence to a confidence.

Spec 8.1's second half. Two mechanisms, both deterministic, both stated in
the `RiskAnalysis` they produce so that a reader can check the arithmetic
rather than take it:

- the **clamp**, which raises `overall_risk` to the worst severity among
  confirmed high-confidence exposures;
- the **ceilings**, which cap `confidence` for each specific reason this
  analysis is less complete than its factor list suggests.

No model is called here either. The LLM's `summary` and `qualitative_notes`
arrive as strings and are stored as strings; nothing in this module reads
them, so there is no path by which prose could move a number.
"""

from collections.abc import Sequence

from upgradepilot.models.enums import DependencyRole, RiskLevel
from upgradepilot.models.evidence import BreakingChange, RiskFactor
from upgradepilot.models.knowledge import RagContext
from upgradepilot.models.repo import RepoAnalysis
from upgradepilot.models.risk import (
    RISK_ORDER,
    ConfidenceCeiling,
    RiskAnalysis,
    higher_risk,
)
from upgradepilot.services.risk.factors import (
    SEVERITY_AS_RISK,
    confirmed_exposures,
)
from upgradepilot.services.risk.thresholds import (
    AGGREGATE_HIGH_AT,
    AGGREGATE_MEDIUM_AT,
)

LEVEL_SCORE: dict[RiskLevel, float] = {
    RiskLevel.LOW: 0.0,
    RiskLevel.MEDIUM: 0.5,
    RiskLevel.HIGH: 1.0,
}

BASE_CONFIDENCE = 0.85
"""What this analysis is worth when nothing has gone wrong.

Not 1.0, and the missing 0.15 is not modesty. This system reads a repository
without executing it: it cannot see a symbol reached through `getattr`, a
plugin loaded by name, or a dependency pinned by an environment the manifest
does not describe. A run that reported full confidence would be claiming a
completeness the method does not have, on every repository, before any
specific gap was even found.
"""

SKIPPED_FILES_CEILING_AT = 0.10
"""Spec 8.1: "skipped files exceed 10%". Strictly greater, so a repository at
exactly one file in ten is not capped -- the spec says *exceed*."""

NO_EVIDENCE_CEILING = 0.30
SKIPPED_FILES_CEILING = 0.50
TRANSITIVE_ONLY_CEILING = 0.40
UNDOCUMENTED_SYMBOL_CEILING = 0.60
UNKNOWN_CHURN_CEILING = 0.75
ANALYSIS_REDUCERS_CEILING = 0.75
NO_FACTORS_CEILING = 0.30
"""The ceilings, as constants rather than literals at their call sites.

Spec 8.1 fixes the first four. The last three are this phase's additions and
each has a reason: unreadable history means one of the seven dimensions was
not measured at all; the analyzer's own confidence reducers are gaps it found
and reported and that nothing else here would otherwise act on; and a verdict
built from no factors is a verdict built from nothing.
"""


def aggregate_level(factors: Sequence[RiskFactor]) -> RiskLevel:
    """The weighted mean of the factor levels, graded by the table.

    Weighted by each factor's fixed weight, and divided by the weights of the
    factors that are actually **present** rather than by the full table's
    total. Dividing by the full total would make an omitted factor pull the
    verdict down: a repository whose history could not be read would score
    lower than an identical one whose history was read and showed churn, for
    no reason but the missing measurement. Absent evidence lowers
    *confidence*, never risk -- that separation is the whole design, and
    quietly breaking it in a denominator would be hard to see.

    An empty factor list is LOW, and is prevented from reading as reassurance
    by `RiskAnalysis`'s no-factors ceiling rather than by inventing a level
    here.
    """
    if not factors:
        return RiskLevel.LOW

    total_weight = sum(factor.weight for factor in factors)
    if total_weight == 0.0:
        return RiskLevel.LOW

    score = sum(LEVEL_SCORE[factor.level] * factor.weight for factor in factors) / total_weight
    if score >= AGGREGATE_HIGH_AT:
        return RiskLevel.HIGH
    if score >= AGGREGATE_MEDIUM_AT:
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


def clamp_floor(
    analysis: RepoAnalysis, breaking_changes: Sequence[BreakingChange]
) -> RiskLevel | None:
    """The worst documented severity among symbols the AST proved are in use.

    `None` when there is no confirmed exposure at all, which is different
    from a floor of LOW: `None` means the clamp does not apply, and a floor
    of LOW would be a floor nothing can fall below anyway. The distinction
    shows up in `RiskAnalysis.clamp_floor`, which a reader uses to tell "no
    documented break is in use here" from "the documented break in use is
    minor".
    """
    matched = confirmed_exposures(analysis, breaking_changes)
    if not matched:
        return None
    severities = [
        SEVERITY_AS_RISK[change.severity] for changes in matched.values() for change in changes
    ]
    return max(severities, key=lambda level: RISK_ORDER[level])


def confidence_ceilings(
    *,
    analysis: RepoAnalysis,
    rag_context: RagContext | None,
    breaking_changes: Sequence[BreakingChange],
    factors: Sequence[RiskFactor],
) -> tuple[ConfidenceCeiling, ...]:
    """Every reason this verdict cannot be as confident as it looks.

    Each ceiling names itself in a sentence the reader is shown, because "why
    30%?" is the question the number provokes and an unexplained cap is
    indistinguishable from a bug.

    A `rag_context` of `None` is treated as *no evidence*, not as an unknown
    to be skipped. It means the retrieval subgraph did not produce a context
    -- the analysis failed upstream, or the wrapper skipped the loop -- and in
    every one of those cases no corpus document informed this run.
    """
    ceilings: list[ConfidenceCeiling] = []

    if rag_context is None or not rag_context.evidence_available:
        ceilings.append(
            ConfidenceCeiling(
                reason=(
                    "No documented evidence was retrieved for this upgrade, so every "
                    "judgement below rests on the repository alone."
                ),
                ceiling=NO_EVIDENCE_CEILING,
            )
        )

    if analysis.skipped_ratio > SKIPPED_FILES_CEILING_AT:
        ceilings.append(
            ConfidenceCeiling(
                reason=(
                    f"{analysis.skipped_ratio:.0%} of this repository's Python files "
                    "could not be parsed, so usage in them is invisible to this report."
                ),
                ceiling=SKIPPED_FILES_CEILING,
            )
        )

    detected = analysis.detected_version
    if detected is not None and detected.role is DependencyRole.TRANSITIVE_ONLY:
        ceilings.append(
            ConfidenceCeiling(
                reason=(
                    "This dependency is present only transitively: no manifest in this "
                    "repository declares it, so the upgrade is not wholly under this "
                    "repository's control."
                ),
                ceiling=TRANSITIVE_ONLY_CEILING,
            )
        )

    high_confidence = analysis.symbol_inventory.high_confidence_symbols()
    documented = confirmed_exposures(analysis, breaking_changes)
    undocumented = [symbol for symbol in high_confidence if symbol not in documented]
    if undocumented:
        ceilings.append(
            ConfidenceCeiling(
                reason=(
                    f"{len(undocumented)} symbol(s) this repository certainly uses have "
                    f"no documented change behind them: {', '.join(undocumented)}."
                ),
                ceiling=UNDOCUMENTED_SYMBOL_CEILING,
            )
        )

    if analysis.affected_files and all(
        file.commit_count is None for file in analysis.affected_files
    ):
        ceilings.append(
            ConfidenceCeiling(
                reason=(
                    "This repository's commit history could not be read, so change "
                    "activity on the affected files is unknown rather than low."
                ),
                ceiling=UNKNOWN_CHURN_CEILING,
            )
        )

    if analysis.confidence_reducers:
        ceilings.append(
            ConfidenceCeiling(
                reason=(
                    "The repository analysis reported "
                    f"{len(analysis.confidence_reducers)} gap(s) in what it could read: "
                    + " ".join(analysis.confidence_reducers)
                ),
                ceiling=ANALYSIS_REDUCERS_CEILING,
            )
        )

    if not factors:
        ceilings.append(
            ConfidenceCeiling(
                reason=(
                    "Not one of the seven risk dimensions had evidence behind it, so "
                    "there is nothing here that was measured."
                ),
                ceiling=NO_FACTORS_CEILING,
            )
        )

    return tuple(ceilings)


def build_risk_analysis(
    *,
    analysis: RepoAnalysis,
    breaking_changes: Sequence[BreakingChange],
    rag_context: RagContext | None,
    factors: Sequence[RiskFactor],
    summary: str,
    qualitative_notes: Sequence[str] = (),
) -> RiskAnalysis:
    """Assemble the verdict. Every number here is computed, none is supplied.

    `summary` and `qualitative_notes` are the only inputs that came from a
    model, and they are the only two fields no other field reads.
    """
    aggregate = aggregate_level(factors)
    floor = clamp_floor(analysis, breaking_changes)
    overall = higher_risk(aggregate, floor) if floor is not None else aggregate

    ceilings = confidence_ceilings(
        analysis=analysis,
        rag_context=rag_context,
        breaking_changes=breaking_changes,
        factors=factors,
    )
    confidence = BASE_CONFIDENCE
    for ceiling in ceilings:
        confidence = min(confidence, ceiling.ceiling)

    return RiskAnalysis(
        overall_risk=overall,
        aggregate_risk=aggregate,
        clamp_floor=floor,
        confidence=confidence,
        confidence_ceilings=ceilings,
        factors=tuple(factors),
        summary=summary,
        qualitative_notes=tuple(note for note in qualitative_notes if note.strip()),
    )
