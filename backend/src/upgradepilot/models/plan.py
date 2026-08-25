"""The migration plan, the validation report, and the run's final output.

Spec 8.3 and 8.4. Two ideas run through this file.

**A step that cannot be checked is not a step.** `MigrationStep` requires
either a file it changes or an evidence ref explaining why it exists. A step
saying "review the remaining usages" with neither is the kind of filler a
model produces when it has run out of things to say, and it is indistinguishable
in a rendered plan from a step someone can act on.

**The human's influence is structural, not claimed.**
`MigrationPlan.human_decisions_applied` pairs each decision with a sentence
saying what it changed, and `validate_plan`'s ninth check refuses a plan where
a decision exists and that list is empty. "The human's answer affected the
output" stops being an assertion in a README and becomes a validation failure
when it is not true.

`ValidationReport` deliberately has no `passed` field. It is derived from the
outcomes, because a stored verdict beside a list of failures is the one shape
that can lie about itself -- and `COMPLETED_WITH_WARNINGS` is exactly the case
where the two would be tempted to disagree.
"""

from typing import Self

from pydantic import AwareDatetime, Field, computed_field, model_validator

from upgradepilot.models.base import HonestModel
from upgradepilot.models.decision import DecisionApplication, HumanDecision
from upgradepilot.models.enums import StrategyId, ValidationCheckId
from upgradepilot.models.evidence import (
    BreakingChange,
    EvidenceRef,
    NonBlankStr,
    RepoRelativePath,
)
from upgradepilot.models.inputs import DependencySpec, RepoRef, UserConstraints
from upgradepilot.models.knowledge import RagContext
from upgradepilot.models.repo import AffectedFile, RepoAnalysis, ShaStr
from upgradepilot.models.risk import RiskAnalysis
from upgradepilot.models.trace import TraceEvent
from upgradepilot.models.usage import UsageSummary


class MigrationStep(HonestModel):
    """One piece of work, with something behind it."""

    order: int = Field(ge=1)
    title: NonBlankStr
    description: NonBlankStr

    files: tuple[RepoRelativePath, ...] = ()
    """Files this step changes. Produced from the analysis record, never by a
    model -- CLAUDE.md rule 19. A model asked for a file path will produce a
    plausible one, and a plausible path in a migration plan is an instruction
    to edit a file that does not exist."""

    rationale_evidence: tuple[EvidenceRef, ...] = ()
    validation: str | None = None
    """How to tell this step worked, in one line. Optional because some steps
    are checked by the next step failing, and inventing a check for those
    would be inventing a claim."""

    requires_downtime: bool = False
    """Whether this step needs a coordinated cutover. Set from the chosen
    strategy, never from prose: check 10 refuses a plan with a downtime step
    under a zero-downtime constraint, and a flag a model could set would make
    that check a negotiation."""

    @model_validator(mode="after")
    def _a_step_must_name_a_file_or_cite_a_reason(self) -> Self:
        """Spec 8.3, and the reason it is a constraint rather than a
        guideline: "review the remaining usages" with neither a file nor a
        citation is filler, and in a rendered plan it is indistinguishable
        from work someone can actually do."""
        if not self.files and not self.rationale_evidence:
            raise ValueError(
                f"step {self.order} ({self.title!r}) names no file and cites no "
                "evidence: a step with neither cannot be checked and cannot be acted on"
            )
        return self


class UnaddressedFile(HonestModel):
    """A file the plan does not change, and why not.

    The alternative to this model is silence, and silence is what makes a
    partial plan read as a complete one. Spec 8.4's eighth check requires
    every high-confidence affected file to be either addressed by a step or
    named here -- so "we did not cover this" is a thing the report says out
    loud rather than a gap the reader has to find by comparing two lists.
    """

    path: RepoRelativePath
    reason: NonBlankStr


class MigrationPlan(HonestModel):
    """The plan. Ordered steps, the strategy behind them, and what was left out."""

    strategy_id: StrategyId
    summary: NonBlankStr
    steps: tuple[MigrationStep, ...] = ()
    human_decisions_applied: tuple[DecisionApplication, ...] = ()
    unaddressed_with_reason: tuple[UnaddressedFile, ...] = ()
    mitigations: tuple[NonBlankStr, ...] = ()

    @model_validator(mode="after")
    def _steps_are_contiguously_ordered(self) -> Self:
        """`order` must be 1..n with no gaps and no repeats.

        Half of spec 8.4's seventh check, enforced here as well as there
        because the two failures are different: the validator reports it to
        the reader, and this stops a plan whose steps cannot be rendered in a
        stable sequence from being constructed at all. A gap or a repeat makes
        "step 3" ambiguous in a document people work through in order.
        """
        orders = [step.order for step in self.steps]
        if orders != list(range(1, len(orders) + 1)):
            raise ValueError(
                f"steps must be numbered 1..{len(orders)} with no gaps or repeats, got {orders}"
            )
        return self

    def addressed_paths(self) -> frozenset[str]:
        return frozenset(path for step in self.steps for path in step.files)


class ValidationOutcome(HonestModel):
    """One check, its verdict, and what failed it."""

    check_id: ValidationCheckId
    passed: bool
    detail: NonBlankStr
    offenders: tuple[NonBlankStr, ...] = ()
    """What specifically failed -- a path, a source id, a step number. Empty
    for a passing check, and required for a failing one: a failure the reader
    cannot locate is a failure they cannot fix."""

    @model_validator(mode="after")
    def _a_failure_must_name_what_failed(self) -> Self:
        if not self.passed and not self.offenders:
            raise ValueError(
                f"check {self.check_id.value!r} failed without naming an offender: a "
                "failure the reader cannot locate is a failure they cannot fix"
            )
        return self


class ValidationReport(HonestModel):
    """Every check's outcome for one attempt at the plan."""

    attempt: int = Field(ge=1)
    """Which generation attempt this report grades. Spec 8.4 allows exactly
    one bounded repair, so this is 1 or 2 and the second's failures are what
    the report shows."""

    outcomes: tuple[ValidationOutcome, ...] = Field(min_length=1)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def passed(self) -> bool:
        """Derived, never stored (CLAUDE.md rule 21). A stored verdict beside
        a list of failures is the one shape that can lie about itself, and
        `COMPLETED_WITH_WARNINGS` is exactly where the two would be tempted to
        disagree.

        `@computed_field` rather than a bare property, for the same reason
        `AffectedFile.symbols` is one: it has to appear in `model_dump()`, and
        therefore in the API response and the generated TypeScript. A derived
        value the frontend cannot see is one the frontend re-derives, which is
        a second implementation of the rule in a language that cannot check
        it against this one.
        """
        return all(outcome.passed for outcome in self.outcomes)

    @property
    def failures(self) -> tuple[ValidationOutcome, ...]:
        return tuple(outcome for outcome in self.outcomes if not outcome.passed)

    @model_validator(mode="after")
    def _each_check_is_reported_once(self) -> Self:
        """Two outcomes for one check id make `passed` depend on which one a
        reader happens to look at, and make the report's own count wrong."""
        ids = [outcome.check_id for outcome in self.outcomes]
        if len(ids) != len(set(ids)):
            duplicated = sorted({i.value for i in ids if ids.count(i) > 1})
            raise ValueError(f"duplicate validation outcomes: {duplicated}")
        return self


class FinalReport(HonestModel):
    """Everything the run established, assembled once by `finalize`.

    A single object rather than a set of channels the API re-reads, because
    the report is what a reader is handed and it has to be internally
    consistent: the usage summary, the risk verdict and the plan all come from
    one moment in the run's state. `finalize` is a pure function over that
    state, so building it twice from the same checkpoint gives the same
    report.

    There is deliberately **no status field**. Spec 6.5: status is computed
    from the checkpoint plus the run registry, and a stored copy would drift
    from reality on crash -- which is the very case `ORPHANED` exists for.
    `COMPLETED` versus `COMPLETED_WITH_WARNINGS` is derivable from
    `validation.passed`, so storing it would add nothing but a way to
    disagree.
    """

    thread_id: NonBlankStr
    repo_ref: RepoRef
    dependency: DependencySpec
    constraints: UserConstraints
    commit_sha: ShaStr | None
    completed_at: AwareDatetime

    repo_analysis: RepoAnalysis | None
    affected_files: tuple[AffectedFile, ...] = ()
    breaking_changes: tuple[BreakingChange, ...] = ()
    rag_context: RagContext | None = None
    risk_analysis: RiskAnalysis | None = None
    migration_plan: MigrationPlan | None = None
    validation: ValidationReport | None = None
    human_decisions: tuple[HumanDecision, ...] = ()
    usage: UsageSummary
    agent_trace: tuple[TraceEvent, ...] = ()
    errors: tuple[NonBlankStr, ...] = ()
    """User-facing error messages, not `AppError`s.

    The technical `detail` field is for logs correlated by `thread_id`
    (CLAUDE.md rule 27); putting it in the report would leak provider
    responses and internal exception text into a document people share.
    """

    @computed_field  # type: ignore[prop-decorator]
    @property
    def completed_with_warnings(self) -> bool:
        """Whether validation failed after its one repair attempt.

        A `@computed_field` because the API's status ladder and the report
        header both read it; see `ValidationReport.passed` for why derived
        values that reach the client must be serialised rather than left as
        bare properties.

        Derived here rather than stored so that the report and the status
        cannot disagree -- see the class docstring. A report with no
        validation at all is *not* "with warnings": the run did not get far
        enough to produce a plan, which is a different and more visible
        condition already carried by `migration_plan` being `None`.
        """
        return self.validation is not None and not self.validation.passed
