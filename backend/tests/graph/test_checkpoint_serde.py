"""What survives a checkpoint round trip, and what silently would not.

LangGraph 1.2.11 warns on deserializing a type it has not been told about:
"Deserializing unregistered type ... This will be blocked in a future
version." The word "blocked" undersells what actually happens, which is why
this file exists.

Measured against the pinned version: with strict msgpack enabled and a type
not on the allowlist, deserialization does **not** raise. It returns a plain
`dict`. So a resumed run would carry dictionaries everywhere it expects
Pydantic models -- `BreakingChange.source` no longer required, `RiskFactor`'s
`min_length=1` on evidence no longer enforced, `LLMCall`'s cost/basis
agreement no longer checked. Every honesty invariant this project encodes in
its types would be gone from a resumed run, with nothing raised and nothing
logged at the point of use.

Registering the allowlist is therefore a correctness requirement, not noise
suppression, and the allowlist is *derived* rather than hand-listed so a model
added in a later phase cannot be forgotten.
"""

from datetime import UTC, datetime

import pytest
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from pydantic import BaseModel

from upgradepilot.graph.checkpointer import checkpoint_serializer, serializable_state_types
from upgradepilot.models.enums import CostBasis, LLMCallKind, TraceEventKind
from upgradepilot.models.inputs import DependencySpec, LocalRepoRef, UserConstraints
from upgradepilot.models.state import initial_state
from upgradepilot.models.trace import trace_event
from upgradepilot.models.usage import LLMCall, UsageSummary


class NotOurModel(BaseModel):
    """A Pydantic model defined outside `upgradepilot.models`.

    Module-level on purpose. Defined inside a test function it would be
    unimportable by module-and-name, so msgpack could not reconstruct it
    whatever the allowlist said -- and the test below would pass against a
    serializer that registered nothing at all.
    """

    value: str


def a_state() -> dict[str, object]:
    state = dict(
        initial_state(
            thread_id="t-1",
            repo_ref=LocalRepoRef(path="/tmp/repo"),
            dependency=DependencySpec(
                name="pydantic", current_version="1.10.13", target_version="2.9.0"
            ),
            constraints=UserConstraints(),
        )
    )
    state["llm_calls"] = [
        LLMCall(
            call_id="call-1",
            node="assess_risk",
            model="gpt-4.1-mini",
            kind=LLMCallKind.CHAT,
            input_tokens=100,
            output_tokens=20,
            cost_usd=0.0001,
            cost_basis=CostBasis.PROVIDER_REPORTED,
            started_at=datetime(2026, 8, 25, 12, 0, tzinfo=UTC),
        )
    ]
    state["agent_trace"] = [
        trace_event(TraceEventKind.NODE_STARTED, node="analyze_repo", summary="started")
    ]
    return state


def test_state_models_survive_a_round_trip_as_models() -> None:
    serde = checkpoint_serializer()

    restored = serde.loads_typed(serde.dumps_typed(a_state()))

    assert isinstance(restored["dependency"], DependencySpec)
    assert isinstance(restored["repo_ref"], LocalRepoRef)
    assert isinstance(restored["constraints"], UserConstraints)
    assert isinstance(restored["llm_calls"][0], LLMCall)
    assert restored["llm_calls"][0].cost_basis is CostBasis.PROVIDER_REPORTED
    assert restored["agent_trace"][0].kind is TraceEventKind.NODE_STARTED


def test_our_serializer_actually_restricts_rather_than_allowing_everything() -> None:
    """Proves the allowlist is applied, without depending on a log line.

    The obvious way to build this serializer is a silent no-op:
    `JsonPlusSerializer().with_msgpack_allowlist(types)` returns `self`
    unchanged, because the default allowlist is the sentinel `True` and the
    method declines to narrow it. Everything kept working -- permissive mode
    allows everything -- so the test above passed while registering nothing.

    A warning-based check would not have caught it either: LangGraph warns
    once per type per process, so whether the line appears depends on which
    test ran first. This asserts the behaviour instead: `NotOurModel` lives
    outside `upgradepilot.models`, so it is not on the allowlist and must
    degrade -- which is only true if the allowlist is real.
    """

    serde = checkpoint_serializer()

    restored = serde.loads_typed(
        serde.dumps_typed({"ours": a_state()["dependency"], "theirs": NotOurModel(value="x")})
    )

    assert isinstance(restored["ours"], DependencySpec), "our own type was not registered"
    assert isinstance(restored["theirs"], dict), (
        "an unregistered type survived, so the allowlist is still permissive"
    )


def test_without_the_allowlist_the_same_state_degrades_to_dictionaries() -> None:
    """The discriminating half, and the reason the test above is not merely
    asserting that Python works.

    This is what a future LangGraph does by default. Note it does not raise:
    the models come back as `dict`, so every invariant they carry is gone and
    the first symptom is an `AttributeError` somewhere far from here -- or, for
    code that happens to use subscripting, no symptom at all.
    """
    unregistered = JsonPlusSerializer(allowed_msgpack_modules=None)

    restored = unregistered.loads_typed(unregistered.dumps_typed(a_state()))

    assert isinstance(restored["dependency"], dict)
    assert not isinstance(restored["dependency"], DependencySpec)


def test_a_degraded_state_breaks_the_usage_derivation_that_depends_on_it() -> None:
    """Naming the consequence rather than only the shape.

    `UsageSummary.from_calls` reads attributes off each record. Handed the
    dictionaries the unregistered path produces, it fails -- which is the good
    case. The bad case is code that reads a dict just as happily as a model
    and carries the loss forward silently.
    """
    unregistered = JsonPlusSerializer(allowed_msgpack_modules=None)
    restored = unregistered.loads_typed(unregistered.dumps_typed(a_state()))

    with pytest.raises((AttributeError, TypeError)):
        UsageSummary.from_calls(restored["llm_calls"])


def test_every_model_in_the_package_is_registered() -> None:
    """The allowlist is derived by walking `upgradepilot.models`, not written
    out by hand, so a model added in Phase 5 or later is registered by
    existing.

    A hand-written list is precisely the thing a new model gets forgotten
    from, and the symptom of forgetting -- a silent downgrade to `dict` on
    resume only -- is one of the hardest kinds of bug to trace back to its
    cause.
    """
    registered = {(cls.__module__, cls.__qualname__) for cls in serializable_state_types()}

    for cls in (DependencySpec, LocalRepoRef, UserConstraints, LLMCall, CostBasis):
        assert (cls.__module__, cls.__qualname__) in registered, cls

    assert len(registered) > 20, "the package walk found suspiciously few types"


def test_the_walk_registers_enums_as_well_as_models() -> None:
    """`CostBasis` and `TraceEventKind` are `StrEnum`s, and they appeared in
    LangGraph's own warning output alongside the models -- a walk that
    collected only `BaseModel` subclasses would leave them to degrade to bare
    strings, so `cost_basis is CostBasis.UNKNOWN` would quietly stop being
    true after a resume."""
    registered = {(cls.__module__, cls.__qualname__) for cls in serializable_state_types()}

    assert (CostBasis.__module__, "CostBasis") in registered
    assert (TraceEventKind.__module__, "TraceEventKind") in registered
