"""The agent trace: what the run did, as a reader can check it.

CLAUDE.md rule 26 draws a line that a prompt-authoring convention cannot hold
on its own -- the trace shows node boundaries, queries issued, sources
retrieved and selected, decisions and validation outcomes, and never internal
prompts or private reasoning. A single node tipping its prompt into this
channel leaks the system's own instructions into a user-facing panel, and
nothing downstream can tell that it happened.

The rule is therefore enforced by the *shape* of `TraceEvent`. There is no
`payload`, no `metadata`, no `dict[str, Any]` -- nothing structured enough for
a transcript to travel in. `summary` and `detail` are strings a node writes
deliberately, and `tests/unit/test_trace_events.py` pins the field set so that
adding a hiding place turns a test red.
"""

import uuid
from datetime import UTC, datetime

from pydantic import AwareDatetime

from upgradepilot.models.base import HonestModel
from upgradepilot.models.enums import TraceEventKind
from upgradepilot.models.evidence import NonBlankStr


class TraceEvent(HonestModel):
    """One observable thing the run did."""

    event_id: NonBlankStr
    """Stable identity for a list that grows while it is on screen. Colliding
    ids make React reuse a rendered row for a different event."""

    kind: TraceEventKind
    node: NonBlankStr
    at: AwareDatetime
    summary: NonBlankStr
    """One line, written for the reader. Non-blank because it is the only
    thing most events render, and a row that says nothing is indistinguishable
    from a rendering bug."""

    detail: str | None = None
    """Optional supporting text -- a count, a filename, a validation message.
    Still subject to rule 26: it is not somewhere to put a prompt."""


def trace_event(
    kind: TraceEventKind,
    *,
    node: str,
    summary: str,
    detail: str | None = None,
    at: datetime | None = None,
    event_id: str | None = None,
) -> TraceEvent:
    """Build a `TraceEvent`, defaulting the id and the timestamp.

    `at` and `event_id` are injectable so a test can assert on an exact event
    rather than on a shape. Defaulting `at` to an aware UTC timestamp is not
    incidental: a naive one is rendered by the browser in whatever zone it
    guesses, so two events a second apart can display out of order.
    """
    return TraceEvent(
        event_id=event_id or str(uuid.uuid4()),
        kind=kind,
        node=node,
        at=at or datetime.now(UTC),
        summary=summary,
        detail=detail,
    )
