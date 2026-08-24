"""Error taxonomy.

`message` is user-facing and comprehensible. `detail` is technical and is
logged, correlated by thread_id. Nothing is ever swallowed: a caught
exception becomes an AppError in state plus a trace event.
"""

from enum import StrEnum
from typing import ClassVar

from pydantic import Field

from upgradepilot.models.base import HonestModel


class ErrorCode(StrEnum):
    INVALID_REPO_URL = "invalid_repo_url"
    LOCAL_PATH_FORBIDDEN = "local_path_forbidden"
    REPO_UNAVAILABLE = "repo_unavailable"
    REPO_TOO_LARGE = "repo_too_large"
    DEPENDENCY_NOT_FOUND = "dependency_not_found"
    VERSION_INVALID = "version_invalid"
    KB_UNAVAILABLE = "kb_unavailable"
    LLM_UNAVAILABLE = "llm_unavailable"
    LLM_RATE_LIMITED = "llm_rate_limited"
    THREAD_NOT_FOUND = "thread_not_found"
    THREAD_NOT_AWAITING_INPUT = "thread_not_awaiting_input"
    INVALID_DECISION = "invalid_decision"
    INTERNAL = "internal"


class AppError(HonestModel):
    """An error recorded in graph state and surfaced to the client."""

    code: ErrorCode
    message: str = Field(min_length=1)
    detail: str | None = None
    node: str | None = None
    retryable: bool = False


class UpgradePilotError(Exception):
    """Base for all domain errors. Subclasses set code and http_status."""

    code: ClassVar[ErrorCode] = ErrorCode.INTERNAL
    http_status: ClassVar[int] = 500
    retryable: ClassVar[bool] = False

    def __init__(self, message: str, *, detail: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail

    def to_app_error(self, node: str | None = None) -> AppError:
        return AppError(
            code=self.code,
            message=self.message,
            detail=self.detail,
            node=node,
            retryable=self.retryable,
        )


class InvalidRepoUrlError(UpgradePilotError):
    code = ErrorCode.INVALID_REPO_URL
    http_status = 422


class LocalPathForbiddenError(UpgradePilotError):
    code = ErrorCode.LOCAL_PATH_FORBIDDEN
    http_status = 403


class RepoUnavailableError(UpgradePilotError):
    code = ErrorCode.REPO_UNAVAILABLE
    http_status = 502
    retryable = True


class RepoTooLargeError(UpgradePilotError):
    code = ErrorCode.REPO_TOO_LARGE
    http_status = 413


class DependencyNotFoundError(UpgradePilotError):
    code = ErrorCode.DEPENDENCY_NOT_FOUND
    http_status = 422


class VersionInvalidError(UpgradePilotError):
    code = ErrorCode.VERSION_INVALID
    http_status = 422


class KnowledgeBaseUnavailableError(UpgradePilotError):
    code = ErrorCode.KB_UNAVAILABLE
    http_status = 503
    retryable = True


class ThreadNotFoundError(UpgradePilotError):
    code = ErrorCode.THREAD_NOT_FOUND
    http_status = 404


class ThreadNotAwaitingInputError(UpgradePilotError):
    code = ErrorCode.THREAD_NOT_AWAITING_INPUT
    http_status = 409


class InvalidDecisionError(UpgradePilotError):
    code = ErrorCode.INVALID_DECISION
    http_status = 422
