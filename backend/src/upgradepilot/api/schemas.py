"""The HTTP contract: what goes in, and the one shape that comes out.

Spec 9.1. `RunSnapshot` serves every state a run can be in, so the frontend
renders one shape and never branches on which endpoint replied. That is worth
the handful of nullable fields: the alternative is a client that has to know
which response type each endpoint returns in each state, and gets it wrong on
the state nobody tested.

**Errors are reshaped on the way out.** `AppError` carries `detail`, which is
technical and belongs in logs correlated by `thread_id` (CLAUDE.md rule 27).
`ApiError` is the same error minus that field. It is a separate model rather
than an exclusion rule because an exclusion is a thing someone forgets when
they add a field, and this way the API's shape is written down in one place
that a reader can check against the rule.
"""

from typing import Literal

from pydantic import BaseModel, Field

from upgradepilot.models.decision import HumanDecision, InterruptPayload
from upgradepilot.models.enums import RiskLevel, RunStatus
from upgradepilot.models.errors import AppError, ErrorCode
from upgradepilot.models.evidence import BreakingChange, SourceRef
from upgradepilot.models.inputs import UserConstraints
from upgradepilot.models.knowledge import RagContext
from upgradepilot.models.plan import FinalReport, MigrationPlan, ValidationReport
from upgradepilot.models.repo import AffectedFile
from upgradepilot.models.risk import RiskAnalysis
from upgradepilot.models.trace import TraceEvent
from upgradepilot.models.usage import UsageSummary


class ApiError(BaseModel):
    """One error, as a client is allowed to see it. See the module docstring."""

    code: ErrorCode
    message: str
    retryable: bool = False
    node: str | None = None

    @classmethod
    def of(cls, error: AppError) -> "ApiError":
        return cls(
            code=error.code,
            message=error.message,
            retryable=error.retryable,
            node=error.node,
        )


class ErrorResponse(BaseModel):
    """The body of every non-2xx response from this API.

    One shape for every failure, so a client's error handling is written once.
    The status code carries the class of problem; this carries what to say.
    """

    error: ApiError


class DependencyInput(BaseModel):
    name: str
    current_version: str
    target_version: str


class RepoInput(BaseModel):
    """Where the repository is. Exactly one of `url` or `path`.

    Two nullable fields with a validator rather than a discriminated union,
    because this is the shape a form posts: a UI with a radio button and two
    text inputs sends the one the user filled in, and requiring a `kind`
    discriminator alongside would be asking the client to restate what the
    populated field already says.
    """

    url: str | None = None
    path: str | None = None


class StartRunRequest(BaseModel):
    repo: RepoInput
    dependency: DependencyInput
    constraints: UserConstraints = Field(default_factory=UserConstraints)


class DecisionInput(BaseModel):
    """A human's answer. Validated again inside the graph, deliberately.

    This model checks the *shape*; `human_review` checks that the option
    exists on the question being asked, because only the graph knows which
    question that is. Two layers, and the inner one is the one that matters --
    it is reachable however the resume value arrives.
    """

    question_id: str
    selected_option_id: str
    rationale: str | None = None


class ResumeRequest(BaseModel):
    """Continue a run.

    `decision` is optional, and its absence means something specific rather
    than nothing: it is how an `ORPHANED` run -- one whose checkpoint survived
    a restart its task did not -- is picked back up. A run awaiting a human
    needs an answer; a run that lost its process needs only to be driven
    again, and asking the client to invent a decision for it would be asking
    for a lie.
    """

    thread_id: str
    decision: DecisionInput | None = None


class StartResponse(BaseModel):
    """202 from both `start` and `resume`: accepted, not finished."""

    thread_id: str
    status: RunStatus
    poll_url: str


class UsageView(BaseModel):
    """Usage, with the two flags that stop a total being misread.

    `estimated` says at least one token count came from a local tokenizer.
    `pricing_complete` says every counted call had a price -- when it is false
    the cost is a **lower bound**, and this flag is the only thing that says
    so. Both are surfaced at the top level of the API rather than left inside
    the summary, because a UI that has to dig for them is a UI that will print
    the number alone.
    """

    calls: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    estimated: bool
    pricing_complete: bool
    estimated_cost_usd: float | None
    by_node: tuple[tuple[str, int], ...]
    """`(node, total tokens)` pairs. "Where did the tokens go" is the second
    question a developer asks (spec 9.4)."""

    by_model: tuple[tuple[str, int], ...]
    """`(model, total tokens)` pairs. READINESS.md 2.5 dropped the model and
    temperature dropdowns because configuration lives in environment variables
    (CLAUDE.md rule 14) and there is no configuration endpoint -- so the model
    actually in use can only be read off calls that happened, not guessed at
    or hardcoded (DESIGN.md's Telemetry section, "Model in use, from
    `UsageSummary.by_model`")."""

    @classmethod
    def of(cls, usage: UsageSummary) -> "UsageView":
        return cls(
            calls=usage.calls,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            total_tokens=usage.total_tokens,
            estimated=usage.estimated,
            pricing_complete=usage.pricing_complete,
            estimated_cost_usd=usage.estimated_cost_usd,
            by_node=tuple((entry.node, entry.total_tokens) for entry in usage.by_node),
            by_model=tuple((entry.model, entry.total_tokens) for entry in usage.by_model),
        )


class RunSnapshot(BaseModel):
    """One response model for every state a run can be in. Spec 9.1."""

    thread_id: str
    status: RunStatus
    current_step: str | None = None
    completed_steps: tuple[str, ...] = ()

    trace: tuple[TraceEvent, ...] = ()
    usage: UsageView

    # Evidence so far -- populated progressively, so a client polling a
    # running job can show what has been established rather than a spinner.
    affected_files: tuple[AffectedFile, ...] = ()
    breaking_changes: tuple[BreakingChange, ...] = ()
    retrieved_sources: tuple[SourceRef, ...] = ()
    rag_context: RagContext | None = None
    risk_analysis: RiskAnalysis | None = None
    migration_plan: MigrationPlan | None = None
    validation: ValidationReport | None = None
    human_decisions: tuple[HumanDecision, ...] = ()

    pending_decision: InterruptPayload | None = None
    """The question currently awaiting an answer, or `None`.

    Derived from the checkpoint's interrupts rather than from a stored
    pointer, because a stored one drifts the moment a resume lands on the
    `human_decisions` channel without it.
    """

    final_report: FinalReport | None = None
    errors: tuple[ApiError, ...] = ()


class HealthChecks(BaseModel):
    chroma_dir: bool
    checkpoint_dir: bool
    llm_configured: bool


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    checks: HealthChecks


RISK_LEVELS: tuple[RiskLevel, ...] = tuple(RiskLevel)
"""Exported so the generated TypeScript carries the enum rather than `string`.

`openapi-typescript` only emits a union for an enum something in the schema
actually references, and `RiskLevel` reaches the schema only through nested
models. Naming it here keeps the frontend's exhaustiveness checks working.
"""
