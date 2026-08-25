"""The agent trace: observable events, and nothing else.

CLAUDE.md rule 26 draws a line the model cannot be trusted to respect on its
own -- the trace shows node boundaries, queries issued, sources retrieved and
selected, decisions and validation outcomes; it does **not** show internal
prompts or private reasoning. A trace carrying a prompt is a leak of the
system's own instructions into a user-facing panel, and once one node does it
nothing else in the system can tell.

So the rule is enforced by the *shape* of `TraceEvent` rather than by review:
there is no field a prompt fits in, and the tests below assert that absence
directly rather than trusting that nobody adds one.
"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from upgradepilot.models.enums import TraceEventKind
from upgradepilot.models.trace import TraceEvent, trace_event


def test_a_trace_event_records_what_a_reader_can_check() -> None:
    event = trace_event(
        TraceEventKind.NODE_STARTED,
        node="analyze_repo",
        summary="Analysing repository at commit deadbee",
        at=datetime(2026, 8, 25, 12, 0, tzinfo=UTC),
    )

    assert event.kind is TraceEventKind.NODE_STARTED
    assert event.node == "analyze_repo"
    assert event.summary == "Analysing repository at commit deadbee"
    assert event.at.tzinfo is not None


def test_trace_events_carry_no_field_a_prompt_could_travel_in() -> None:
    """Rule 26, enforced structurally.

    A `dict[str, Any]` payload, a `raw` field, or a free `metadata` mapping
    would each be a place a well-meaning node could tip a prompt or a chain of
    reasoning into the user-facing panel. Naming the forbidden fields here
    means adding one turns this test red rather than silently opening the
    channel.
    """
    fields = set(TraceEvent.model_fields)

    assert not fields & {"prompt", "messages", "reasoning", "thought", "raw", "payload", "metadata"}


def test_every_field_is_a_scalar_a_reader_could_be_shown() -> None:
    """The complement of the test above, and the one that survives a field
    being added under a name nobody thought to forbid: the trace may hold
    strings, an enum, a timestamp and an id, and nothing structured enough to
    hide a transcript in."""
    allowed = {"event_id", "kind", "node", "at", "summary", "detail"}

    assert set(TraceEvent.model_fields) == allowed


def test_a_blank_summary_is_refused() -> None:
    """The summary is the only thing the panel renders for most events. An
    event that says nothing is a row the reader cannot act on and cannot
    distinguish from a rendering bug."""
    with pytest.raises(ValidationError):
        trace_event(TraceEventKind.NODE_STARTED, node="analyze_repo", summary="   ")


def test_two_events_get_distinct_ids() -> None:
    """Ids exist so the frontend can key a list that grows while it is on
    screen. Colliding ids make React reuse a row for a different event."""
    first = trace_event(TraceEventKind.NODE_STARTED, node="a", summary="one")
    second = trace_event(TraceEventKind.NODE_STARTED, node="a", summary="one")

    assert first.event_id != second.event_id


def test_the_timestamp_defaults_to_now_and_is_timezone_aware() -> None:
    """A naive timestamp is rendered by the browser in whatever zone it
    guesses, so two events recorded a second apart can display out of
    order."""
    event = trace_event(TraceEventKind.NODE_COMPLETED, node="a", summary="done")

    assert event.at.tzinfo is not None
    assert (datetime.now(UTC) - event.at).total_seconds() < 60


def test_the_kinds_cover_exactly_what_rule_26_permits() -> None:
    """Pinning the enum's membership is what makes rule 26 checkable. A new
    kind is a deliberate decision about what the product exposes, not an
    incidental addition."""
    assert {kind.value for kind in TraceEventKind} == {
        "node_started",
        "node_completed",
        "query_issued",
        "sources_retrieved",
        "sources_selected",
        "retrieval_evaluated",
        "agent_decision",
        "decision_required",
        "decision_applied",
        "validation_outcome",
        "error_recorded",
    }
