"""One place the whole error taxonomy becomes HTTP.

Spec 9.3. Every `UpgradePilotError` already carries its own `http_status`, so
this handler is a *dispatch* rather than a table: adding an error type sets its
status where the type is defined, and nothing here needs to know about it. A
mapping in this module would be a second list to keep in step with the first,
and the failure mode of that is a new error class quietly answering 500.

Two rules the handler holds:

- **`detail` never leaves the process.** It is technical, it is logged
  correlated by `thread_id`, and it routinely contains provider responses and
  exception text (CLAUDE.md rule 27). The client gets `code`, `message` and
  `retryable`.
- **An unexpected exception is a 500 with a generic message, and is logged in
  full.** Reflecting an unrecognised exception's text back to the caller is
  how internal paths and library versions end up in someone's browser.
"""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from upgradepilot.api.schemas import ApiError, ErrorResponse
from upgradepilot.models.errors import ErrorCode, UpgradePilotError

logger = logging.getLogger("upgradepilot.api")


def _body(error: ApiError) -> dict[str, object]:
    return ErrorResponse(error=error).model_dump(mode="json")


async def handle_domain_error(request: Request, exc: Exception) -> JSONResponse:
    """Answer with the error's own status, and log the technical half."""
    assert isinstance(exc, UpgradePilotError)  # registered for this type only
    logger.warning(
        "%s %s -> %s: %s",
        request.method,
        request.url.path,
        exc.code.value,
        exc.detail or exc.message,
    )
    return JSONResponse(
        status_code=exc.http_status,
        content=_body(ApiError(code=exc.code, message=exc.message, retryable=exc.retryable)),
    )


async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    """A bug, not a condition: generic outward, complete in the log."""
    logger.exception("%s %s -> unhandled %s", request.method, request.url.path, type(exc).__name__)
    return JSONResponse(
        status_code=500,
        content=_body(
            ApiError(
                code=ErrorCode.INTERNAL,
                message="Something went wrong handling this request.",
                retryable=False,
            )
        ),
    )


async def handle_request_validation_error(request: Request, exc: Exception) -> JSONResponse:
    """FastAPI's own body validation, in this API's error shape.

    Without this the contract has two different 422 bodies -- FastAPI's
    `{"detail": [...]}` for a malformed request and `ErrorResponse` for
    everything else -- and a client's error rendering works for one of them.
    The field paths are included in the message because they are the whole
    value of this error: "field required" without saying which field is a
    sentence nobody can act on.
    """
    assert isinstance(exc, RequestValidationError)  # registered for this type only
    problems = "; ".join(
        f"{'.'.join(str(part) for part in error['loc'][1:]) or 'body'}: {error['msg']}"
        for error in exc.errors()
    )
    logger.info("%s %s -> 422: %s", request.method, request.url.path, problems)
    return JSONResponse(
        status_code=422,
        content=_body(
            ApiError(
                code=ErrorCode.INVALID_DECISION
                if request.url.path.endswith("/resume")
                else ErrorCode.INVALID_REPO_URL,
                message=f"The request could not be accepted -- {problems}",
                retryable=False,
            )
        ),
    )


def install_error_handlers(app: FastAPI) -> None:
    """Order does not matter here: FastAPI dispatches on the exception type,
    most specific first, and these three types do not overlap."""
    app.add_exception_handler(UpgradePilotError, handle_domain_error)
    app.add_exception_handler(RequestValidationError, handle_request_validation_error)
    app.add_exception_handler(Exception, handle_unexpected_error)
