"""Node bodies for the skeleton graph, and the wrapper every node wears.

**These bodies are stubs.** Each real one arrives with the phase that owns it
-- `analyze_repo` and `inspect_dependency` have working services behind them
already (Phase 2), `agentic_rag` is Phase 5, `assess_risk` Phase 6,
`generate_plan` and `validate_plan` Phase 8. What is real here is everything
around them: the state channels, the trace, the usage records and the error
handling, which is the part no later phase would think to re-test.

The wrapper is the point of this module. CLAUDE.md rule 20 -- a caught
exception produces an `AppError` in state *and* a trace event, always -- is
enforced once, here, rather than in each node body. "Remember to catch" is the
kind of rule that holds for every node except the one written in a hurry, and
a node that dies unrecorded takes the run down with no explanation the user
can act on.
"""

from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel

from upgradepilot.models.enums import TraceEventKind
from upgradepilot.models.errors import AppError, ErrorCode, UpgradePilotError
from upgradepilot.models.state import MigrationState
from upgradepilot.models.trace import trace_event
from upgradepilot.services.llm.tracked import TrackedLLM

StateUpdate = dict[str, Any]
NodeBody = Callable[[MigrationState], Awaitable[StateUpdate]]


class StubNarrative(BaseModel):
    """The schema the skeleton's one model call asks for.

    A stand-in for §8.1's risk narrative, kept deliberately trivial: its job
    is to make a real structured call travel the real `TrackedLLM` path so
    the usage channel is exercised end to end, not to produce anything a
    reader would be shown.
    """

    summary: str


def traced(name: str, body: NodeBody) -> NodeBody:
    """Wrap a node body with its trace boundary and rule 20's error handling.

    Three guarantees for every node in the graph, whatever its body does:

    - a `node_started` event before, and a `node_completed` event after;
    - a domain failure becomes an `AppError` carrying its own `ErrorCode`;
    - an *unexpected* exception becomes `AppError(INTERNAL)` naming the
      exception type. It is not reported as a domain error, because a bug in
      a node body is not a condition the user caused or can act on -- but it
      still reaches state rather than escaping. Rule 20 says nothing is
      swallowed, not that everything is a known condition.

    The run continues after a failure so the report can say what *was*
    established alongside what failed; aborting would throw away evidence
    already gathered and paid for.
    """

    async def run(state: MigrationState) -> StateUpdate:
        events = [trace_event(TraceEventKind.NODE_STARTED, node=name, summary=f"{name} started")]
        try:
            update = await body(state)
        except UpgradePilotError as exc:
            return {
                "errors": [exc.to_app_error(node=name)],
                "agent_trace": [
                    *events,
                    trace_event(
                        TraceEventKind.ERROR_RECORDED,
                        node=name,
                        summary=f"{name} failed: {exc.message}",
                    ),
                    trace_event(
                        TraceEventKind.NODE_COMPLETED, node=name, summary=f"{name} finished"
                    ),
                ],
            }
        except Exception as exc:  # noqa: BLE001 - converted, never swallowed
            return {
                "errors": [
                    AppError(
                        code=ErrorCode.INTERNAL,
                        message=(
                            f"An internal error occurred while running {name}, so this "
                            "step could not complete."
                        ),
                        detail=f"{type(exc).__name__}: {exc}",
                        node=name,
                        retryable=False,
                    )
                ],
                "agent_trace": [
                    *events,
                    trace_event(
                        TraceEventKind.ERROR_RECORDED,
                        node=name,
                        summary=f"{name} failed unexpectedly",
                        detail=type(exc).__name__,
                    ),
                    trace_event(
                        TraceEventKind.NODE_COMPLETED, node=name, summary=f"{name} finished"
                    ),
                ],
            }

        trace: list[Any] = [*events, *update.pop("agent_trace", [])]
        trace.append(
            trace_event(TraceEventKind.NODE_COMPLETED, node=name, summary=f"{name} finished")
        )
        return {**update, "agent_trace": trace}

    return run


def stub_node(_state: MigrationState) -> StateUpdate:
    """A node that does nothing but exist. Replaced phase by phase."""
    return {}


def make_stub(name: str) -> NodeBody:
    async def body(state: MigrationState) -> StateUpdate:
        return stub_node(state)

    body.__name__ = name
    return body


def make_assess_risk(llm: TrackedLLM) -> NodeBody:
    """The one skeleton node that calls a model.

    Placed at `assess_risk` because that is where §8.1 puts narrative
    synthesis, so the skeleton's single call sits where a real one will. The
    prompt is a placeholder; the `LLMCall` it records is not, and it is what
    the resume test exercises.
    """

    async def body(state: MigrationState) -> StateUpdate:
        _, call = await llm.invoke_structured(
            node="assess_risk",
            prompt=(
                f"Summarise the upgrade of {state['dependency'].name} from "
                f"{state['dependency'].current_version} to "
                f"{state['dependency'].target_version} in one sentence."
            ),
            schema=StubNarrative,
        )
        return {"llm_calls": [call]}

    return body
