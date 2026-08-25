"""The human-in-the-loop contract: what is asked, and what comes back.

Spec 8.2. Two directions, and they are untrusted in opposite ways.

**Outward**, an `InterruptPayload` is the entire basis on which someone
decides. It carries the reason the question exists, the evidence behind it,
every option with its consequences, and what happens if nobody answers -- so
the question can be answered by a person who was not watching the run. The
constraints here exist to stop a question that cannot be answered from being
asked: fewer than two options is not a choice, a recommendation naming an
option that does not exist is a broken UI, and an option with no consequences
is a button with no label on the thing it does.

**Inward**, a `HumanDecision` is whatever HTTP handed us. `interrupt()`
returns the resume value unvalidated, so the node validates against this model
and re-interrupts on an unknown option rather than proceeding with garbage.
That validation is in `graph/nodes/judgment.py`; what lives here is the shape
it validates against.

One measured detail that shaped this file. On the pinned LangGraph, every
`interrupt()` call inside one node reports the **same** `Interrupt.id` -- the
id identifies the task, not the question (`backend/probes/probe_interrupt.py`).
So nothing may rely on that id to tell which question is pending;
`InterruptPayload.question_id` is the identity, and it travels inside the
payload where it cannot be confused with LangGraph's own.
"""

from typing import Self

from pydantic import AwareDatetime, Field, model_validator

from upgradepilot.models.base import HonestModel
from upgradepilot.models.enums import DecisionKind, EffortLevel, RiskLevel
from upgradepilot.models.evidence import EvidenceRef, NonBlankStr


class DecisionOption(HonestModel):
    """One answer a human may give, with what it costs and what it commits to."""

    id: NonBlankStr
    label: NonBlankStr
    summary: NonBlankStr

    risk_level: RiskLevel
    effort: EffortLevel
    downtime: bool
    """Whether this option requires a coordinated cutover during which the old
    and new versions cannot both be running. Spelled out because "downtime"
    means different things to different teams, and the zero-downtime
    constraint is decided against exactly this reading."""

    consequences: tuple[NonBlankStr, ...] = Field(min_length=1)
    """What follows from choosing this. Required and non-empty: an option
    whose consequences are unstated is a button whose effect the person
    pressing it has to guess."""

    supporting_evidence: tuple[EvidenceRef, ...] = Field(min_length=1)
    """Why this option is on the table at all.

    `min_length=1` for the same reason `RiskFactor.evidence` has it. An option
    is a recommendation, and a recommendation with nothing behind it is the
    plausible prose CLAUDE.md rule 1 exists to prevent -- it is simply
    plausible prose with a button next to it.
    """


class InterruptPayload(HonestModel):
    """Everything a person needs to answer one question without watching the run."""

    question_id: NonBlankStr
    kind: DecisionKind
    reason: NonBlankStr
    """Why the run stopped here. Distinct from `question`: the reason is what
    the evidence made unavoidable, the question is what is being asked."""

    question: NonBlankStr
    evidence: tuple[EvidenceRef, ...] = Field(min_length=1)
    options: tuple[DecisionOption, ...] = Field(min_length=2)
    """At least two. Spec 8.2 interrupts only when two options genuinely
    remain, so a payload with one is a run that stopped to announce a
    foregone conclusion -- the ceremonial dialog the conditional edge exists
    to prevent, arriving through the data instead of the control flow."""

    recommendation_id: str | None = None
    consequences_if_unanswered: NonBlankStr
    """What the run cannot do while it waits. The honest answer is usually
    "nothing further happens", and saying so is what stops a pending question
    from being mistaken for a completed run."""

    validation_error: str | None = None
    """Set when this payload is being asked *again* after an unusable answer.

    Carried on the payload rather than raised, because the person answering is
    the one who needs to see it and an exception would reach a log instead.
    """

    @model_validator(mode="after")
    def _option_ids_are_unique(self) -> Self:
        """Two options sharing an id make the selection ambiguous, and the
        ambiguity resolves silently -- a lookup finds the first, the reader
        clicked the second."""
        ids = [option.id for option in self.options]
        if len(ids) != len(set(ids)):
            duplicated = sorted({identifier for identifier in ids if ids.count(identifier) > 1})
            raise ValueError(f"duplicate option ids in {self.question_id!r}: {duplicated}")
        return self

    @model_validator(mode="after")
    def _the_recommendation_is_one_of_the_options(self) -> Self:
        """A recommendation naming an option that is not offered renders as no
        recommendation at all, which is the same failure as having none while
        looking like having one."""
        if self.recommendation_id is None:
            return self
        if self.recommendation_id not in {option.id for option in self.options}:
            raise ValueError(
                f"recommendation_id={self.recommendation_id!r} is not one of the options "
                f"offered by {self.question_id!r}: "
                f"{sorted(option.id for option in self.options)}"
            )
        return self

    def option(self, option_id: str) -> DecisionOption | None:
        """The named option, or `None`. The lookup the resume path validates
        against -- see `graph/nodes/judgment.py`."""
        return next((option for option in self.options if option.id == option_id), None)


class HumanDecision(HonestModel):
    """One answered question. Validated from untrusted resume input."""

    question_id: NonBlankStr
    selected_option_id: NonBlankStr
    rationale: str | None = None
    decided_at: AwareDatetime


class DecisionApplication(HonestModel):
    """How one human decision changed the plan that followed it.

    Spec 8.3 makes the human's influence structural rather than claimed: a
    plan that carries a decision must say what the decision *did*, in a
    sentence, and `validate_plan`'s ninth check refuses a plan where a
    decision exists and this list is empty. "The human's answer affected the
    output" stops being an assertion in a README and becomes a validation
    failure when it is not true.
    """

    decision_id: NonBlankStr
    how_it_changed_the_plan: NonBlankStr


def unanswered(
    pending: tuple[InterruptPayload, ...] | list[InterruptPayload],
    answered: tuple[HumanDecision, ...] | list[HumanDecision],
) -> tuple[InterruptPayload, ...]:
    """The questions still waiting, in the order they were raised.

    Derived rather than stored (CLAUDE.md rule 21). A stored "current
    question" field is one that drifts the moment a resume lands: the answer
    arrives on the `human_decisions` channel and the pointer does not, and the
    UI then shows a question that has already been answered while the run
    proceeds past it.
    """
    settled = {decision.question_id for decision in answered}
    return tuple(payload for payload in pending if payload.question_id not in settled)
