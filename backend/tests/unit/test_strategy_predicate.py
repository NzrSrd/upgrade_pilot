"""Spec 8.2's interrupt predicate, one test per constraint combination.

The predicate is the whole mechanism: **the graph interrupts only when at
least two strategies remain viable and they differ on an axis the stated
constraints do not already settle.** A system that stops to ask whatever it
can think of trains its users to click through, and a dialog everyone clicks
through is worse than no dialog -- it launders a default into an apparent
decision.

Tested here rather than through the graph because the interesting cases are
combinations of constraints, and one graph run per combination would be a
minute of git and Chroma per boolean.
"""

from datetime import date

import pytest

from upgradepilot.models.enums import DecisionAxis, EffortLevel, RiskLevel, StrategyId
from upgradepilot.models.inputs import UserConstraints
from upgradepilot.services.strategy.catalog import (
    CATALOG,
    EFFORT_ORDER,
    needs_human_choice,
    ranked,
    ranking_priority,
    recommended,
    settled_axes,
    unsettled_differences,
    viable_strategies,
)

TODAY = date(2026, 8, 25)


# -- the catalog ------------------------------------------------------------


def test_effort_is_ordered_by_the_table_not_alphabetically() -> None:
    """Same trap as `RISK_ORDER`: `EffortLevel` is a `StrEnum`, so
    `max(HIGH, LOW)` is `LOW`."""
    assert EFFORT_ORDER[EffortLevel.HIGH] > EFFORT_ORDER[EffortLevel.MEDIUM]


def test_every_strategy_id_has_exactly_one_entry() -> None:
    assert {strategy.id for strategy in CATALOG} == set(StrategyId)
    assert len(CATALOG) == len(StrategyId)


def test_the_viable_set_is_never_empty() -> None:
    """`recommended` indexes `ranked(...)[0]`, so an empty viable set would be
    an `IndexError` in the middle of risk assessment. Two of the three
    strategies avoid downtime, which is what makes the zero-downtime rule
    unable to empty the catalog -- pinned here rather than left as a property
    of the current table."""
    for constraints in (
        UserConstraints(),
        UserConstraints(zero_downtime=True),
        UserConstraints(zero_downtime=True, minimize_effort=True, risk_tolerance=RiskLevel.LOW),
    ):
        assert viable_strategies(constraints)


# -- viability: hard constraints only ---------------------------------------


def test_zero_downtime_rules_out_a_coordinated_cutover() -> None:
    """Hard, because the user said so in as many words. Offering it anyway
    would be asking them to restate a constraint they already gave."""
    viable = viable_strategies(UserConstraints(zero_downtime=True))

    assert StrategyId.DIRECT_MIGRATION not in {strategy.id for strategy in viable}


def test_a_preference_never_deletes_an_option() -> None:
    """Effort, deadline and risk tolerance are preferences, and preferences
    settle axes rather than delete options. Deleting on a preference can empty
    the set -- zero-downtime plus minimise-effort would leave nothing -- and a
    run with no viable strategy has no plan to generate."""
    constraints = UserConstraints(
        minimize_effort=True, deadline=date(2026, 8, 26), risk_tolerance=RiskLevel.LOW
    )

    assert len(viable_strategies(constraints)) == len(CATALOG)


# -- settling ---------------------------------------------------------------


def test_the_default_risk_tolerance_settles_nothing() -> None:
    """`UserConstraints`'s defaults exist so an omitted constraint never
    silently tightens the recommendation. Reading the default `MEDIUM` as a
    stated preference would settle the risk axis on every run, and no strategy
    question would ever be asked."""
    assert settled_axes(UserConstraints(), today=TODAY) == frozenset()


@pytest.mark.parametrize(
    ("constraints", "axis"),
    [
        (UserConstraints(zero_downtime=True), DecisionAxis.DOWNTIME),
        (UserConstraints(minimize_effort=True), DecisionAxis.EFFORT),
        (UserConstraints(deadline=date(2026, 9, 1)), DecisionAxis.EFFORT),
        (UserConstraints(risk_tolerance=RiskLevel.LOW), DecisionAxis.RISK),
        (UserConstraints(risk_tolerance=RiskLevel.HIGH), DecisionAxis.RISK),
    ],
)
def test_each_stated_constraint_settles_its_axis(
    constraints: UserConstraints, axis: DecisionAxis
) -> None:
    assert axis in settled_axes(constraints, today=TODAY)


def test_a_distant_deadline_does_not_settle_the_effort_axis() -> None:
    """A deadline six months out is a fact about the request, not a statement
    that less work is preferred."""
    constraints = UserConstraints(deadline=date(2027, 2, 1))

    assert DecisionAxis.EFFORT not in settled_axes(constraints, today=TODAY)


def test_an_axis_nobody_differs_on_is_not_a_tradeoff() -> None:
    """Both halves of the predicate are required. If every viable strategy
    avoids downtime, the downtime axis is moot whether or not the user
    mentioned it."""
    viable = viable_strategies(UserConstraints(zero_downtime=True))

    differences = unsettled_differences(viable, frozenset())

    assert DecisionAxis.DOWNTIME not in differences


# -- the predicate ----------------------------------------------------------


def test_an_unconstrained_run_asks() -> None:
    """The ordinary case: three strategies differing on risk and effort, and
    nothing said about either."""
    assert needs_human_choice(UserConstraints(), today=TODAY) is True


def test_constraints_that_settle_every_difference_do_not_ask() -> None:
    """Spec 8.2's negative case, and the reason the conditional edge exists:
    "If constraints decide it, no interrupt occurs.\""""
    constraints = UserConstraints(
        zero_downtime=True, minimize_effort=True, risk_tolerance=RiskLevel.LOW
    )

    assert needs_human_choice(constraints, today=TODAY) is False


def test_settling_one_axis_is_not_enough_to_stop_asking() -> None:
    """Two strategies differing on risk and effort, with only effort settled,
    still leaves a real choice."""
    assert needs_human_choice(UserConstraints(minimize_effort=True), today=TODAY) is True


def test_a_deadline_settles_effort_the_same_way_minimize_effort_does() -> None:
    """The same boundary `constraint_pressure` uses, deliberately: two places
    treating "imminent" differently would produce a run whose risk factor says
    the deadline is pressing and whose decision logic says it is not."""
    near = UserConstraints(
        zero_downtime=True, deadline=date(2026, 8, 30), risk_tolerance=RiskLevel.LOW
    )
    # `far` states no risk tolerance, so the risk axis stays open -- which is
    # the only axis the two zero-downtime strategies differ on.
    far = UserConstraints(zero_downtime=True, deadline=date(2027, 8, 30))

    assert needs_human_choice(near, today=TODAY) is False
    assert needs_human_choice(far, today=TODAY) is True


# -- scoring ----------------------------------------------------------------


def test_the_priority_is_an_ordering_not_a_weighting() -> None:
    """An earlier version summed weighted penalties and recommended the
    *highest*-effort strategy to a user who asked to minimise effort, because
    the lowest-risk option's risk saving outweighed its effort cost at
    whatever weights happened to be written down. Every fix was a matter of
    choosing a bigger number, which is a sign the model was wrong rather than
    the numbers."""
    stated = ranking_priority(UserConstraints(minimize_effort=True), today=TODAY)

    assert stated[0] is DecisionAxis.EFFORT


def test_a_high_risk_tolerance_deprioritises_risk_rather_than_seeking_it() -> None:
    """It does not say "prefer risk"; it says risk matters less than the
    rest, so the axis moves last."""
    priority = ranking_priority(UserConstraints(risk_tolerance=RiskLevel.HIGH), today=TODAY)

    assert priority[-1] is DecisionAxis.RISK
    assert recommended(UserConstraints(risk_tolerance=RiskLevel.HIGH), today=TODAY).id is (
        StrategyId.DIRECT_MIGRATION
    )


def test_a_low_risk_tolerance_recommends_the_lowest_risk_approach() -> None:
    best = recommended(UserConstraints(risk_tolerance=RiskLevel.LOW), today=TODAY)

    assert best.id is StrategyId.COMPATIBILITY_LAYER


def test_minimise_effort_recommends_the_least_work() -> None:
    best = recommended(UserConstraints(minimize_effort=True), today=TODAY)

    assert best.id is StrategyId.DIRECT_MIGRATION


def test_zero_downtime_never_recommends_a_cutover() -> None:
    best = recommended(UserConstraints(zero_downtime=True, minimize_effort=True), today=TODAY)

    assert best.downtime is False


def test_the_ranking_is_stable_across_identical_runs() -> None:
    """Ties break on the catalog's declaration order rather than arbitrarily.
    A recommendation that moved between identical runs would be worse than no
    recommendation."""
    constraints = UserConstraints()

    first = [strategy.id for strategy in ranked(constraints, today=TODAY)]
    second = [strategy.id for strategy in ranked(constraints, today=TODAY)]

    assert first == second
    assert len(first) == len(set(first))
