"""The risk verdict, and the two mechanisms that keep it honest.

Spec 8.1. Everything here exists to make one sentence true: *the model cannot
talk its way to a comfortable answer.* It does that in two ways, and both are
enforced by validation rather than by the node that builds the object -- a
rule enforced in a node body holds until someone writes a second node.

**The clamp.** `overall_risk` is computed from the factor levels and then
raised, if necessary, to the maximum severity among confirmed
high-confidence breaking-change exposures. Both halves are stored:
`aggregate_risk` is what the factors said and `clamp_floor` is what the
evidence demanded, and `overall_risk` must equal the higher of the two. A
verdict below a documented break that the AST proved is in use is not a
judgement call this system is willing to make, so it is unconstructable.

**The ceilings.** `confidence` is capped by every applicable
`ConfidenceCeiling`, and the object refuses a confidence above the lowest
one. Absent evidence, an unparseable share of the repository, a
transitive-only pin and an undocumented high-confidence symbol each cap it,
and each records the reason it applied -- so "why is this only 30% confident"
is answered in the object rather than in a node's control flow.

What the LLM contributes is `summary` and `qualitative_notes`: prose over a
factor set it cannot invent, carrying no weight in any level. CLAUDE.md rule
19 says the model never produces a risk factor level; this module goes one
step further and keeps it away from `overall_risk` too, which is a stronger
property than spec 8.1's "clamps override the model" and the same intent --
a clamp that never has to fire cannot be got round.
"""

from typing import Self

from pydantic import Field, model_validator

from upgradepilot.models.base import HonestModel
from upgradepilot.models.enums import RiskLevel
from upgradepilot.models.evidence import NonBlankStr, RiskFactor

RISK_ORDER: dict[RiskLevel, int] = {
    RiskLevel.LOW: 0,
    RiskLevel.MEDIUM: 1,
    RiskLevel.HIGH: 2,
}
"""The one place risk levels are ordered.

`RiskLevel` is a `StrEnum`, so `>` on two members compares them
alphabetically: `"low" > "high"` is `True`. Every comparison in this project
goes through this mapping instead, because the alphabetical answer is wrong
in exactly the direction that under-reports -- `max("high", "low")` is
`"low"`, so a clamp written with the obvious operator would quietly clamp
*down*.
"""


def higher_risk(first: RiskLevel, second: RiskLevel) -> RiskLevel:
    """The more severe of two levels. See `RISK_ORDER` for why not `max`."""
    return first if RISK_ORDER[first] >= RISK_ORDER[second] else second


class ConfidenceCeiling(HonestModel):
    """One reason this analysis cannot be as confident as its factors suggest.

    Carries its reason as a user-facing sentence rather than a code, because
    the ceiling is the answer to a question the reader is actually asking --
    "why does this say 30%?" -- and a code would need a lookup table
    somewhere else that could disagree with the number beside it.
    """

    reason: NonBlankStr
    ceiling: float = Field(ge=0.0, le=1.0)


class RiskAnalysis(HonestModel):
    """The risk verdict. See the module docstring for what it refuses."""

    overall_risk: RiskLevel
    aggregate_risk: RiskLevel
    """What the weighted factor levels alone came to.

    Stored beside `overall_risk` so a clamped verdict is visible as one: the
    two differing is the whole story of "the factors read medium, and a
    documented high-severity break in active use raised it".
    """

    clamp_floor: RiskLevel | None = None
    """The maximum severity among confirmed high-confidence exposures, or
    `None` when there are none. The floor `overall_risk` may not go below."""

    confidence: float = Field(ge=0.0, le=1.0)
    confidence_ceilings: tuple[ConfidenceCeiling, ...] = ()

    factors: tuple[RiskFactor, ...] = ()
    """The seven factors, minus any that had nothing to cite.

    Empty is legal and means something specific: not one of the seven
    dimensions had a single piece of evidence behind it. That is a real state
    for a repository with no detected usage, and it is why an empty factor
    set forces a confidence ceiling below -- a verdict computed from nothing
    must not be able to present itself confidently.
    """

    summary: NonBlankStr
    """The LLM's narrative. Prose only: it carries no weight in any level."""

    qualitative_notes: tuple[NonBlankStr, ...] = ()

    @model_validator(mode="after")
    def _the_clamp_holds(self) -> Self:
        """`overall_risk` is exactly `max(aggregate_risk, clamp_floor)`.

        Both directions are refused, and the second matters as much as the
        first. Below the floor is the failure spec 8.1 names: a documented
        break the AST proved is in use, reported as lower risk than the
        document itself assigns. *Above* the computed maximum is a different
        failure and a quieter one -- a verdict inflated past what its own
        inputs support, which is unfalsifiable in the report and destroys the
        reader's ability to tell a serious finding from a cautious one.
        """
        expected = self.aggregate_risk
        if self.clamp_floor is not None:
            expected = higher_risk(expected, self.clamp_floor)
        if self.overall_risk is not expected:
            raise ValueError(
                f"overall_risk={self.overall_risk.value!r} is not "
                f"max(aggregate_risk={self.aggregate_risk.value!r}, "
                f"clamp_floor={self.clamp_floor.value if self.clamp_floor else None!r}) "
                f"= {expected.value!r}: the verdict is derived from those two and may "
                "not be asserted alongside them"
            )
        return self

    @model_validator(mode="after")
    def _no_ceiling_can_be_exceeded(self) -> Self:
        """`confidence` may not exceed the lowest applicable ceiling.

        The ceilings are in the same object as the number they bound, so this
        check needs nothing from outside and cannot be skipped by a caller
        that forgot to apply one -- it can only be skipped by *not recording*
        the ceiling, which is a visible omission rather than an invisible
        one.
        """
        if not self.confidence_ceilings:
            return self
        lowest = min(ceiling.ceiling for ceiling in self.confidence_ceilings)
        if self.confidence > lowest:
            applied = min(self.confidence_ceilings, key=lambda entry: entry.ceiling)
            raise ValueError(
                f"confidence={self.confidence} exceeds the ceiling {lowest} imposed by: "
                f"{applied.reason}"
            )
        return self

    @model_validator(mode="after")
    def _a_verdict_from_no_factors_cannot_be_confident(self) -> Self:
        """An empty factor set means nothing was measurable.

        Without this a repository the analyzer found nothing in would produce
        `overall_risk=low` at full confidence -- "we looked and it is fine" --
        when what actually happened is "we found nothing to look at". Those
        are the two readings this whole project exists to keep apart.
        """
        if not self.factors and not self.confidence_ceilings:
            raise ValueError(
                "a RiskAnalysis with no factors must record at least one confidence "
                "ceiling: a verdict computed from nothing may not present itself as "
                "a verdict computed from evidence"
            )
        return self
