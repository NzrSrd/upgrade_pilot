"""Shared enumerations. Str-valued so they serialize readably over the API."""

from enum import StrEnum


class Severity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Confidence(StrEnum):
    """Confidence in a detected usage site or symbol."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class SourceType(StrEnum):
    MIGRATION_GUIDE = "migration_guide"
    CHANGELOG = "changelog"
    ADR = "adr"
    UPGRADE_REPORT = "upgrade_report"
    COMPAT_NOTE = "compat_note"


class RiskCategory(StrEnum):
    BREAKING_CHANGE_EXPOSURE = "breaking_change_exposure"
    BLAST_RADIUS = "blast_radius"
    TEST_COVERAGE_OF_AFFECTED = "test_coverage_of_affected"
    CHURN_ON_AFFECTED = "churn_on_affected"
    ANALYSIS_COVERAGE = "analysis_coverage"
    EVIDENCE_COVERAGE = "evidence_coverage"
    CONSTRAINT_PRESSURE = "constraint_pressure"


class UsageKind(StrEnum):
    IMPORT = "import"
    MODEL_DEFINITION = "model_definition"
    DECORATOR = "decorator"
    NESTED_CONFIG = "nested_config"
    OPTIONAL_FIELD = "optional_field"
    METHOD_CALL = "method_call"


class DependencyRole(StrEnum):
    DIRECT = "direct"
    TRANSITIVE_ONLY = "transitive_only"


class VersionConfidence(StrEnum):
    EXACT = "exact"
    RANGE = "range"


class ManifestKind(StrEnum):
    PYPROJECT = "pyproject"
    REQUIREMENTS = "requirements"
    POETRY_LOCK = "poetry_lock"
    UV_LOCK = "uv_lock"
    PIPFILE_LOCK = "pipfile_lock"


class LLMCallKind(StrEnum):
    """What kind of model call a `LLMCall` records.

    Embeddings are recorded alongside chat calls rather than in a separate
    channel (spec §9.4) so that estimated cost includes retrieval spend
    instead of quietly omitting it -- a run whose cost is dominated by
    ingestion should not report only its chat tokens.
    """

    CHAT = "chat"
    EMBEDDING = "embedding"


class CostBasis(StrEnum):
    """Where a recorded cost came from.

    A charge the provider reported and a figure computed from our own price
    table are different facts, and the report must not print them as though
    they were the same one. Measured 2026-08-25: OpenRouter returns a real
    per-call charge in `response_metadata["token_usage"]["cost"]`; OpenAI
    direct returns no such field, which is why both paths exist.
    """

    PROVIDER_REPORTED = "provider_reported"
    PRICE_TABLE = "price_table"
    UNKNOWN = "unknown"


class TraceEventKind(StrEnum):
    """What the agent trace is allowed to report.

    CLAUDE.md rule 26 draws the line: the trace shows *observable* events --
    node boundaries, queries issued, sources retrieved and selected,
    decisions, validation outcomes -- and never internal prompts or private
    reasoning. Enumerating the kinds is what makes that rule checkable:
    adding one is a deliberate decision about what the product exposes rather
    than an incidental addition somewhere in a node.
    """

    NODE_STARTED = "node_started"
    NODE_COMPLETED = "node_completed"
    QUERY_ISSUED = "query_issued"
    SOURCES_RETRIEVED = "sources_retrieved"
    SOURCES_SELECTED = "sources_selected"
    RETRIEVAL_EVALUATED = "retrieval_evaluated"
    """One round of the retrieval loop graded: what the model concluded and
    what the deterministic gate concluded. Added in Phase 5 rather than
    folded into `validation_outcome`, because a reader following a loop that
    ran three rounds needs to see the override happening -- "the model said
    sufficient, the gate disagreed" is the single most informative line the
    trace can carry about a retrieval loop, and it is invisible if the two
    verdicts are reported as one."""

    AGENT_DECISION = "agent_decision"
    """A decision the agent took on its own, with its reason.

    Distinct from `decision_required` / `decision_applied`, which are about
    the *human* review interrupt. This kind is for the choices that never
    reach a human: skipping retrieval because the repository uses nothing to
    retrieve about, or resolving a strategy question by the stated
    constraints. Recording them is what keeps a skip from reading as a
    silent no-op -- an absent step and a deliberately-omitted one look
    identical in a timeline that only shows what ran."""

    DECISION_REQUIRED = "decision_required"
    DECISION_APPLIED = "decision_applied"
    VALIDATION_OUTCOME = "validation_outcome"
    ERROR_RECORDED = "error_recorded"


class QueryOrigin(StrEnum):
    """Who wrote a retrieval query.

    The distinction is reported rather than smoothed over. A query the model
    composed and a query this system fell back to are different facts about
    the run: the second one means the model returned nothing usable, which is
    a condition an operator should be able to see in the trace rather than
    infer from a suspiciously generic query text.
    """

    MODEL = "model"
    FALLBACK = "fallback"


class RagStopReason(StrEnum):
    """Why the retrieval loop stopped.

    Spec 7.3 bounds the loop three ways and skips it entirely a fourth, and
    the four outcomes are not interchangeable: `SUFFICIENT` says the evidence
    answered the question, `ITERATION_LIMIT` says we ran out of budget while
    still short, `NOT_NECESSARY` says there was nothing to ask about, and
    `KB_UNAVAILABLE` says we could not ask at all. Collapsing any two of them
    would let a run that found nothing read like a run that needed nothing.
    """

    SUFFICIENT = "sufficient"
    ITERATION_LIMIT = "iteration_limit"
    NOT_NECESSARY = "not_necessary"
    KB_UNAVAILABLE = "kb_unavailable"


class DecisionKind(StrEnum):
    """The four questions this system is willing to put to a human.

    Spec 8.2 fixes the list, and fixing it is the design: an open-ended
    "ask the user something" would degenerate into the ceremonial dialog the
    brief's conditional edge exists to prevent. Each kind below has a
    deterministic trigger -- a condition in the evidence, not a judgement call
    -- so a run either has a real question or has none.
    """

    STRATEGY_CHOICE = "strategy_choice"
    RISK_ACCEPTANCE = "risk_acceptance"
    SCOPE_TRADEOFF = "scope_tradeoff"
    DISCREPANCY_RESOLUTION = "discrepancy_resolution"


class EffortLevel(StrEnum):
    """How much work a strategy is. Three levels, like every other scale here."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class StrategyId(StrEnum):
    """The candidate migration strategies of spec 8.2.

    A closed enum rather than free strings, because `MigrationPlan.strategy_id`
    is what the decision-flip test compares: resuming the same checkpoint with
    the opposite option must yield a *different* strategy, and comparing two
    free-form strings would pass on a typo.
    """

    DIRECT_MIGRATION = "direct_migration"
    COMPATIBILITY_LAYER = "compatibility_layer"
    STAGED_ROLLOUT = "staged_rollout"


class DecisionAxis(StrEnum):
    """The dimensions two strategies can differ on.

    Named because spec 8.2's interrupt predicate is stated in terms of them:
    the graph interrupts only when two viable strategies differ on an axis the
    stated constraints do not already settle. Without an enumerated set of
    axes, "differ on an axis" is a phrase rather than a predicate.
    """

    RISK = "risk"
    EFFORT = "effort"
    DOWNTIME = "downtime"
