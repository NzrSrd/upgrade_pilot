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
    DECISION_REQUIRED = "decision_required"
    DECISION_APPLIED = "decision_applied"
    VALIDATION_OUTCOME = "validation_outcome"
    ERROR_RECORDED = "error_recorded"
