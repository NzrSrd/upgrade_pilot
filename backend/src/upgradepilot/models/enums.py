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
