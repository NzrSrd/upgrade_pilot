"""Error taxonomy.

`message` is user-facing and comprehensible. `detail` is technical and is
logged, correlated by thread_id. Nothing is ever swallowed: a caught
exception becomes an AppError in state plus a trace event.

Error handling is the one place that must not be able to fail. `AppError`
validates `message`, so `UpgradePilotError("")` used to raise a
`ValidationError` from `to_app_error()` -- a CLAUDE.md rule 20 violation at
the worst possible moment, replacing whatever went wrong with a second,
unrelated failure. `UpgradePilotError` therefore normalises its own message
at construction time and records the substitution in `detail`, so
`to_app_error()` can never raise on it. The blank message is not swallowed;
it moves to the technical field where it belongs.
"""

from enum import StrEnum
from typing import ClassVar

from upgradepilot.models.base import HonestModel
from upgradepilot.models.evidence import NonBlankStr

BLANK_MESSAGE_FALLBACK = "An unexpected internal error occurred."
"""Stands in for a blank `UpgradePilotError` message.

Deliberately generic: a blank message means the raising code told us nothing
about what happened, so anything more specific here would be invented. It is
still comprehensible to a user, which is what `message` is for.
"""


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
    # NonBlankStr, not Field(min_length=1): the latter accepts "   ", and an
    # error whose user-facing message is three spaces is an error the user
    # cannot act on. UpgradePilotError.to_app_error never reaches this
    # rejection -- see the module docstring.
    message: NonBlankStr
    detail: str | None = None
    node: str | None = None
    retryable: bool = False


class UpgradePilotError(Exception):
    """Base for all domain errors. Subclasses set code and http_status."""

    code: ClassVar[ErrorCode] = ErrorCode.INTERNAL
    http_status: ClassVar[int] = 500
    retryable: ClassVar[bool] = False

    def __init__(self, message: str, *, detail: str | None = None) -> None:
        """Normalise `message` here so that raising, and later converting,
        can never itself fail.

        `AppError.message` is a validated `NonBlankStr`, so a blank message
        would make `to_app_error()` raise a `ValidationError` from inside
        error handling -- the original failure lost and replaced by a
        confusing second one. Rather than let that happen, or drop the fact
        silently (rule 20), the blank is substituted in the user-facing field
        and reported in the technical one.
        """
        normalised = message.strip()
        if not normalised:
            normalised = BLANK_MESSAGE_FALLBACK
            note = f"blank message supplied to {type(self).__name__}"
            detail = f"{detail}; {note}" if detail else note
        super().__init__(normalised)
        self.message = normalised
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


class LLMUnavailableError(UpgradePilotError):
    """The model could not be reached, or returned nothing usable.

    Carried into Phase 4 from PLANNING.md's Phase 2 deferrals. `retryable` is
    true because every condition it covers is transient in principle -- a
    dropped connection, a gateway 502, a response that did not match the
    requested schema.

    That last case is the one worth naming, because the code reads oddly for
    it: a model that answers with unparseable output *is* available. It is
    classified here anyway rather than given a code of its own, because the
    code is machine-facing -- it selects an HTTP status and a retry policy,
    and "retry, this may succeed next time" is exactly right. The user-facing
    `message` says what actually happened; the technical `detail` carries the
    parser's complaint.
    """

    code = ErrorCode.LLM_UNAVAILABLE
    http_status = 502
    retryable = True


class LLMRateLimitedError(UpgradePilotError):
    """The provider refused the call for rate reasons.

    Separate from `LLMUnavailableError` because the remedy differs: waiting
    helps here and does not help a misconfigured endpoint, and a run that
    reports both as one condition cannot tell an operator which they have.
    """

    code = ErrorCode.LLM_RATE_LIMITED
    http_status = 429
    retryable = True
