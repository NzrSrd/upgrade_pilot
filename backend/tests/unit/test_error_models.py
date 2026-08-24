"""The error taxonomy, and the one thing it must never do: fail while
reporting a failure."""

import pytest
from pydantic import ValidationError

from upgradepilot.models.errors import (
    BLANK_MESSAGE_FALLBACK,
    AppError,
    ErrorCode,
    InvalidRepoUrlError,
    RepoUnavailableError,
    UpgradePilotError,
)


def test_app_error_rejects_a_blank_message() -> None:
    """`message` is what the user reads. An empty one tells them nothing, and
    an AppError is not a place to be vague."""
    with pytest.raises(ValidationError) as excinfo:
        AppError(code=ErrorCode.INTERNAL, message="")
    errors = excinfo.value.errors()
    assert any(e["loc"] == ("message",) and e["type"] == "string_too_short" for e in errors)


def test_app_error_rejects_a_whitespace_only_message() -> None:
    """`Field(min_length=1)` accepted "   ", which is the whole reason
    NonBlankStr exists."""
    with pytest.raises(ValidationError) as excinfo:
        AppError(code=ErrorCode.INTERNAL, message="   ")
    errors = excinfo.value.errors()
    assert any(e["loc"] == ("message",) and e["type"] == "string_too_short" for e in errors)


def test_constructing_an_error_with_a_blank_message_does_not_raise() -> None:
    """CLAUDE.md rule 20, at the worst possible moment: a ValidationError
    raised from inside error handling replaces the original failure with an
    unrelated one. `UpgradePilotError("")` used to do exactly that via
    `to_app_error()`."""
    error = InvalidRepoUrlError("")

    app_error = error.to_app_error(node="analyze_repo")

    assert app_error.message == BLANK_MESSAGE_FALLBACK
    assert app_error.code is ErrorCode.INVALID_REPO_URL
    assert app_error.node == "analyze_repo"


def test_a_blank_message_is_recorded_in_detail_not_swallowed() -> None:
    """Rule 20 again, the other half: the substitution is a fact about the
    failure, so it goes in the technical field rather than disappearing."""
    error = InvalidRepoUrlError("   ")

    assert error.detail is not None
    assert "blank message" in error.detail
    assert "InvalidRepoUrlError" in error.detail


def test_a_blank_message_note_is_appended_to_an_existing_detail() -> None:
    """An existing `detail` is the technical evidence for the failure and must
    not be overwritten by the note about the message."""
    error = InvalidRepoUrlError("", detail="scheme='ftp'")

    assert error.detail is not None
    assert "scheme='ftp'" in error.detail
    assert "blank message" in error.detail


def test_a_whitespace_padded_message_is_normalised_not_rejected() -> None:
    """A padded message is real content with a paste slip around it, so it is
    stripped rather than replaced."""
    error = RepoUnavailableError("  Could not reach the repository.  ")

    assert error.message == "Could not reach the repository."
    assert error.detail is None  # nothing was substituted, so nothing to note
    assert str(error) == "Could not reach the repository."


def test_to_app_error_carries_code_detail_and_retryable() -> None:
    error = RepoUnavailableError("Could not reach the repository.", detail="exit=128")

    app_error = error.to_app_error(node="clone")

    assert app_error.code is ErrorCode.REPO_UNAVAILABLE
    assert app_error.detail == "exit=128"
    assert app_error.retryable is True
    assert app_error.node == "clone"


@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
def test_no_error_subclass_can_raise_while_being_converted(blank: str) -> None:
    """Swept over the whole taxonomy rather than one subclass: this must hold
    for every error the product can raise, including ones added later."""
    subclasses = [UpgradePilotError, *UpgradePilotError.__subclasses__()]
    assert len(subclasses) >= 10  # the sweep is not vacuous

    for subclass in subclasses:
        app_error = subclass(blank).to_app_error()
        assert app_error.message == BLANK_MESSAGE_FALLBACK
        assert app_error.code is subclass.code
