"""The three candidate strategies, and the predicate that decides whether to ask.

Spec 8.2. The single most important thing in this module is the *negative*
case: **when the stated constraints already settle the question, the graph
does not interrupt at all.** A system that stops to ask whatever it can think
of trains its users to click through, and a dialog everyone clicks through is
worse than no dialog -- it launders a default into an apparent decision.

The predicate is therefore stated as arithmetic over an enumerated set of
axes, not as a judgement:

    interrupt  <=>  at least two strategies are viable
                    AND two of them differ on an axis
                        the constraints do not settle

Both halves are needed and both are testable. Viability removes strategies a
hard constraint forbids; settling removes *differences* the user has already
expressed a preference about. What is left is a genuine tradeoff, which is
the only thing worth a human's attention.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from upgradepilot.models.enums import (
    DecisionAxis,
    EffortLevel,
    RiskLevel,
    StrategyId,
)
from upgradepilot.models.inputs import UserConstraints
from upgradepilot.models.risk import RISK_ORDER

EFFORT_ORDER: dict[EffortLevel, int] = {
    EffortLevel.LOW: 0,
    EffortLevel.MEDIUM: 1,
    EffortLevel.HIGH: 2,
}
"""Same reason `RISK_ORDER` exists: `EffortLevel` is a `StrEnum`, so `>` on
two members compares them alphabetically and `max(HIGH, LOW)` is `LOW`."""

DEADLINE_PRESSURE_DAYS = 14
"""A deadline inside two weeks settles the effort axis.

The same boundary `constraint_pressure` uses, and deliberately the same
number: two places treating "imminent" differently would produce a run whose
risk factor says the deadline is pressing and whose decision logic says it is
not.
"""


@dataclass(frozen=True, slots=True)
class Strategy:
    """One way to perform the migration, with its position on each axis."""

    id: StrategyId
    label: str
    summary: str
    risk: RiskLevel
    effort: EffortLevel
    downtime: bool
    consequences: tuple[str, ...]

    def axis_value(self, axis: DecisionAxis) -> object:
        match axis:
            case DecisionAxis.RISK:
                return self.risk
            case DecisionAxis.EFFORT:
                return self.effort
            case DecisionAxis.DOWNTIME:
                return self.downtime


CATALOG: tuple[Strategy, ...] = (
    Strategy(
        id=StrategyId.DIRECT_MIGRATION,
        label="Migrate directly",
        summary=(
            "Change every affected file to the new API in one piece of work and "
            "release it together."
        ),
        risk=RiskLevel.HIGH,
        effort=EffortLevel.MEDIUM,
        # "Downtime" here means a coordinated cutover during which the old and
        # new versions cannot both be running -- which is what a single
        # release of a whole-codebase API change is. Spelled out on
        # `DecisionOption.downtime` too, because teams read the word
        # differently and the zero-downtime constraint is decided against
        # exactly this reading.
        downtime=True,
        consequences=(
            "The whole codebase moves at once, so there is no period where two "
            "styles of the API coexist.",
            "A problem found after release affects every call site rather than a subset.",
            "Rolling back means reverting the whole change.",
        ),
    ),
    Strategy(
        id=StrategyId.COMPATIBILITY_LAYER,
        label="Introduce a compatibility layer",
        summary=(
            "Wrap the changed API behind this codebase's own interface, move call "
            "sites to the wrapper, then swap the implementation."
        ),
        risk=RiskLevel.LOW,
        effort=EffortLevel.HIGH,
        downtime=False,
        consequences=(
            "Call sites change once, to a stable local interface, rather than twice.",
            "The wrapper is code this codebase then owns and has to remove later.",
            "The upgrade can be reverted by swapping the implementation back.",
        ),
    ),
    Strategy(
        id=StrategyId.STAGED_ROLLOUT,
        label="Migrate in stages",
        summary=(
            "Convert one area at a time, releasing each stage independently, "
            "highest-severity usage first."
        ),
        risk=RiskLevel.MEDIUM,
        effort=EffortLevel.HIGH,
        downtime=False,
        consequences=(
            "Each stage is small enough to review and revert on its own.",
            "The codebase spends the migration in a mixed state, so both styles must keep working.",
            "Total elapsed time is longer than a single cutover.",
        ),
    ),
)
"""The three strategies of spec 8.2, with fixed axis values.

Fixed rather than derived from the repository, and that is a real decision.
A strategy's *risk* here is the risk of the approach -- a single cutover is a
riskier way to change code than a staged one, whatever is being changed -- not
the risk of this particular upgrade, which is what `RiskAnalysis` measures.
Mixing the two would make the options move under the reader as the repository
changed, and a comparison whose axes move is not a comparison.
"""


def viable_strategies(constraints: UserConstraints) -> tuple[Strategy, ...]:
    """The strategies a hard constraint does not forbid.

    Exactly one hard rule today: a stated zero-downtime requirement rules out
    any strategy that needs a coordinated cutover. It is hard because the user
    said so in as many words, and offering it anyway would be asking them to
    restate a constraint they already gave.

    Every other constraint -- effort, deadline, risk tolerance -- is a
    *preference*, and preferences settle axes rather than delete options. The
    distinction matters: deleting an option on a preference can empty the set
    (zero-downtime plus minimise-effort would leave nothing), and a run with no
    viable strategy has no plan to generate.
    """
    if not constraints.zero_downtime:
        return CATALOG
    return tuple(strategy for strategy in CATALOG if not strategy.downtime)


def settled_axes(constraints: UserConstraints, *, today: date) -> frozenset[DecisionAxis]:
    """The axes the user has already expressed a preference on.

    An axis is settled when the constraints say which direction is preferred,
    not when they name a value: `minimize_effort` does not say "medium
    effort", it says "less effort is better", and that is enough to decide any
    comparison on the effort axis without asking.

    `risk_tolerance` settles the risk axis only when it is **not** the default
    `MEDIUM`. The default exists so an omitted constraint never silently
    tightens the recommendation (see `UserConstraints`); reading it as a
    stated preference would make every run's risk axis settled and no strategy
    question would ever be asked.
    """
    settled: set[DecisionAxis] = set()
    if constraints.zero_downtime:
        settled.add(DecisionAxis.DOWNTIME)
    if constraints.minimize_effort:
        settled.add(DecisionAxis.EFFORT)
    if (
        constraints.deadline is not None
        and (constraints.deadline - today).days <= DEADLINE_PRESSURE_DAYS
    ):
        settled.add(DecisionAxis.EFFORT)
    if constraints.risk_tolerance is not RiskLevel.MEDIUM:
        settled.add(DecisionAxis.RISK)
    return frozenset(settled)


def unsettled_differences(
    strategies: Sequence[Strategy], settled: frozenset[DecisionAxis]
) -> frozenset[DecisionAxis]:
    """Axes on which the given strategies actually disagree and nothing has
    decided.

    Both halves are required. An axis nobody differs on is not a tradeoff --
    if every viable strategy avoids downtime, the downtime axis is moot
    whether or not the user mentioned it. An axis the constraints settle is
    not a tradeoff either, even when the strategies do differ on it.
    """
    differing: set[DecisionAxis] = set()
    for axis in DecisionAxis:
        if axis in settled:
            continue
        values = {strategy.axis_value(axis) for strategy in strategies}
        if len(values) > 1:
            differing.add(axis)
    return frozenset(differing)


def needs_human_choice(constraints: UserConstraints, *, today: date) -> bool:
    """Spec 8.2's interrupt predicate, as one boolean.

    Kept as its own function rather than inlined into the node so it can be
    tested against a constraint set alone, with no graph, no model and no
    repository -- which is the only way to have a test per constraint
    combination rather than per run.
    """
    viable = viable_strategies(constraints)
    if len(viable) < 2:
        return False
    return bool(unsettled_differences(viable, settled_axes(constraints, today=today)))


DEFAULT_PRIORITY: tuple[DecisionAxis, ...] = (
    DecisionAxis.RISK,
    DecisionAxis.EFFORT,
    DecisionAxis.DOWNTIME,
)
"""Axis order when the user has stated no preference.

Risk first, because the whole product exists to answer "how likely is this to
break things"; then effort; then downtime, which is the axis fewest teams
have an opinion about until they say so. A default is a claim about what
matters, so it is written down rather than emerging from a weight.
"""


def ranking_priority(constraints: UserConstraints, *, today: date) -> tuple[DecisionAxis, ...]:
    """The axes in the order this run should compare strategies on.

    **Ordering, not weighting**, and the difference is the point. An earlier
    version of this module summed weighted penalties, and it recommended the
    *highest*-effort strategy to a user who had asked to minimise effort --
    because the lowest-risk option's risk saving outweighed its effort cost
    at whatever weights happened to be written down. Every fix was a matter of
    choosing a bigger number, which is a sign the model was wrong rather than
    the numbers. A stated preference is not "this axis counts 3x", it is
    "compare on this first", and lexicographic ordering says exactly that with
    nothing left to tune.

    Stated preferences come first, in a fixed order among themselves
    (downtime, effort, risk) so that a user stating two of them still gets a
    deterministic answer. A `risk_tolerance` of `HIGH` is the one preference
    that *deprioritises* its axis: it does not say "prefer risk", it says risk
    matters less than the rest, so the axis moves to last.
    """
    settled = settled_axes(constraints, today=today)
    stated: list[DecisionAxis] = []
    if DecisionAxis.DOWNTIME in settled:
        stated.append(DecisionAxis.DOWNTIME)
    if DecisionAxis.EFFORT in settled:
        stated.append(DecisionAxis.EFFORT)
    if constraints.risk_tolerance is RiskLevel.LOW:
        stated.append(DecisionAxis.RISK)

    rest = [axis for axis in DEFAULT_PRIORITY if axis not in stated]
    if constraints.risk_tolerance is RiskLevel.HIGH and DecisionAxis.RISK in rest:
        rest = [axis for axis in rest if axis is not DecisionAxis.RISK] + [DecisionAxis.RISK]
    return tuple(stated + rest)


def _cost(strategy: Strategy, axis: DecisionAxis) -> int:
    """This strategy's cost on one axis. Lower is better, always."""
    match axis:
        case DecisionAxis.RISK:
            return RISK_ORDER[strategy.risk]
        case DecisionAxis.EFFORT:
            return EFFORT_ORDER[strategy.effort]
        case DecisionAxis.DOWNTIME:
            return int(strategy.downtime)


def rank_key(strategy: Strategy, constraints: UserConstraints, *, today: date) -> tuple[int, ...]:
    """The sort key: one cost per axis, in this run's priority order.

    Never *decides* anything a human is being asked about. When the predicate
    above fires, this only orders the options and marks one as recommended; it
    decides only where the constraints already settled the question, and there
    it is choosing between options the user has effectively ranked already.
    """
    priority = ranking_priority(constraints, today=today)
    return tuple(_cost(strategy, axis) for axis in priority)


def ranked(constraints: UserConstraints, *, today: date) -> tuple[Strategy, ...]:
    """Viable strategies, best first.

    Ties break on the catalog's declaration order rather than arbitrarily, so
    two runs over identical inputs recommend the same thing -- a
    recommendation that moved between identical runs would be worse than no
    recommendation.
    """
    order = {strategy.id: index for index, strategy in enumerate(CATALOG)}
    return tuple(
        sorted(
            viable_strategies(constraints),
            key=lambda strategy: (
                rank_key(strategy, constraints, today=today),
                order[strategy.id],
            ),
        )
    )


def recommended(constraints: UserConstraints, *, today: date) -> Strategy:
    """The best-scoring viable strategy.

    Always returns one: `viable_strategies` can only empty the catalog through
    the zero-downtime rule, and two of the three strategies avoid downtime, so
    the viable set is never empty. `test_the_viable_set_is_never_empty` pins
    that rather than leaving it as a property of the current table.
    """
    return ranked(constraints, today=today)[0]
