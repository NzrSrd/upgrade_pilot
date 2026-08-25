"""Token and cost records, and the summary derived from them.

Spec §6.1 and §9.4. The shape here is the whole design decision: usage is an
**append-only list of `LLMCall` records**, and every total the product prints
is derived from that list by a pure function.

The alternative -- a running counter incremented as calls happen -- fails in a
specific and invisible way. LangGraph replays the interrupted node when a
thread resumes, so a node that called a model once before an interrupt calls
it again on resume; an incrementing counter then reports roughly double, and
the number stays entirely plausible. Records keyed by `call_id` make
aggregation idempotent, so the correctness of the total does not depend on any
node remembering anything, and it is testable with no graph involved at all.

Two flags travel with every summary because a total without them can be read
as something it is not. `estimated` says at least one token count came from a
local tokenizer rather than the provider. `pricing_complete` says every call
had a price; when it is false the printed cost is a **lower bound**, and the
flag is the only thing that says so.
"""

from collections.abc import Iterable, Sequence
from typing import Self

from pydantic import AwareDatetime, Field, model_validator

from upgradepilot.models.base import HonestModel
from upgradepilot.models.enums import CostBasis, LLMCallKind
from upgradepilot.models.evidence import NonBlankStr


class LLMCall(HonestModel):
    """One model call, recorded once and never updated.

    `call_id` is the idempotency key. It is what lets the same record survive
    being appended twice by a replayed node without inflating any total.
    """

    call_id: NonBlankStr
    node: NonBlankStr
    model: NonBlankStr
    kind: LLMCallKind = LLMCallKind.CHAT

    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    tokens_estimated: bool = False
    """True when the counts came from a local tokenizer because the provider
    reported none. Surfaced as estimated, never passed off as exact (§9.4)."""

    cost_usd: float | None = Field(default=None, ge=0.0)
    cost_basis: CostBasis = CostBasis.UNKNOWN
    started_at: AwareDatetime

    @property
    def total_tokens(self) -> int:
        """Computed, never stored (CLAUDE.md rule 21): a stored total can
        disagree with the parts the report itemises beside it."""
        return self.input_tokens + self.output_tokens

    @model_validator(mode="after")
    def _a_cost_and_its_basis_must_agree(self) -> Self:
        """A cost the reader cannot qualify is worse than no cost.

        Both directions are refused. A number under `unknown` is a figure with
        no stated origin; a basis that names an origin while carrying no
        number claims a measurement that was never made.
        """
        if self.cost_basis is CostBasis.UNKNOWN and self.cost_usd is not None:
            raise ValueError(
                f"cost_usd={self.cost_usd} was recorded with cost_basis='unknown': a cost "
                "must say where it came from, since a provider-reported charge and a "
                "price-table lookup are different facts"
            )
        if self.cost_basis is not CostBasis.UNKNOWN and self.cost_usd is None:
            raise ValueError(
                f"cost_basis={self.cost_basis.value!r} promises a cost but cost_usd is None; "
                "use cost_basis='unknown' when no price is available"
            )
        return self

    @model_validator(mode="after")
    def _an_embedding_produces_no_output_tokens(self) -> Self:
        """Spec §9.4 records embeddings with zero output tokens. An embedding
        returns a vector, not a completion, so any non-zero figure here was
        invented somewhere -- and would then be priced at the completion rate,
        which is typically four times the input rate."""
        if self.kind is LLMCallKind.EMBEDDING and self.output_tokens != 0:
            raise ValueError(
                f"an embedding call cannot have output_tokens={self.output_tokens}: "
                "embeddings produce a vector, not completion tokens"
            )
        return self


class ModelUsage(HonestModel):
    """Usage attributed to one model."""

    model: NonBlankStr
    calls: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cost_usd: float | None = Field(default=None, ge=0.0)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class NodeUsage(HonestModel):
    """Usage attributed to one graph node.

    Kept because "where did the tokens actually go" is the second question a
    developer asks (§9.4), and answering it from the call list afterwards is
    impossible once the calls have been summed away.
    """

    node: NonBlankStr
    calls: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cost_usd: float | None = Field(default=None, ge=0.0)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


def _deduplicate(calls: Iterable[LLMCall]) -> list[LLMCall]:
    """First record of each `call_id` wins.

    Keeping the first rather than the last makes a written record immutable:
    a replayed node re-emitting the same `call_id` with different figures
    cannot silently rewrite what was already reported. The two should agree
    anyway -- if they ever do not, the discrepancy is a bug worth finding, and
    last-write-wins would hide it.
    """
    seen: dict[str, LLMCall] = {}
    for call in calls:
        if call.call_id not in seen:
            seen[call.call_id] = call
    return list(seen.values())


def _totalled(costs: list[float | None]) -> float | None:
    """Sum the known costs, or `None` when none are known.

    Returning `0.0` for a run whose cost is entirely unknown would print
    `$0.00` -- the fabrication §9.4 forbids per call, reintroduced at the
    aggregate level. A partial sum is returned when *some* costs are known,
    because it is a real lower bound; `pricing_complete` is what tells the
    reader it is only that.
    """
    known = [cost for cost in costs if cost is not None]
    return sum(known) if known else None


class UsageSummary(HonestModel):
    """Totals derived from `llm_calls`. Never stored -- see the module docstring."""

    calls: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)

    by_model: tuple[ModelUsage, ...] = ()
    by_node: tuple[NodeUsage, ...] = ()

    estimated: bool = False
    """At least one call's tokens came from a local tokenizer."""

    pricing_complete: bool = True
    """Every counted call carried a cost. When false, `estimated_cost_usd` is
    a lower bound rather than the cost."""

    estimated_cost_usd: float | None = Field(default=None, ge=0.0)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @classmethod
    def from_calls(cls, calls: Sequence[LLMCall]) -> Self:
        """Aggregate call records into totals. Pure, and idempotent by `call_id`.

        Both breakdowns are sorted by name so that two polls of an unchanged
        run render identically -- the metrics panel repolls continuously, and
        ordering by whatever a dict happened to yield would make it reshuffle
        between identical responses.
        """
        unique = _deduplicate(calls)

        by_model: dict[str, list[LLMCall]] = {}
        by_node: dict[str, list[LLMCall]] = {}
        for call in unique:
            by_model.setdefault(call.model, []).append(call)
            by_node.setdefault(call.node, []).append(call)

        return cls(
            calls=len(unique),
            input_tokens=sum(call.input_tokens for call in unique),
            output_tokens=sum(call.output_tokens for call in unique),
            by_model=tuple(
                ModelUsage(
                    model=model,
                    calls=len(group),
                    input_tokens=sum(call.input_tokens for call in group),
                    output_tokens=sum(call.output_tokens for call in group),
                    cost_usd=_totalled([call.cost_usd for call in group]),
                )
                for model, group in sorted(by_model.items())
            ),
            by_node=tuple(
                NodeUsage(
                    node=node,
                    calls=len(group),
                    input_tokens=sum(call.input_tokens for call in group),
                    output_tokens=sum(call.output_tokens for call in group),
                    cost_usd=_totalled([call.cost_usd for call in group]),
                )
                for node, group in sorted(by_node.items())
            ),
            estimated=any(call.tokens_estimated for call in unique),
            pricing_complete=all(call.cost_usd is not None for call in unique),
            estimated_cost_usd=_totalled([call.cost_usd for call in unique]),
        )
