"""Symbol coverage over retrieved candidates, and the deterministic gate.

Spec §7.3 puts an LLM in charge of *grading* retrieval quality and then
overrides it mechanically: if any high-confidence symbol has no candidate
chunk documenting it, retrieval is insufficient regardless of what the model
concluded. This module is that override. Nothing here consults a model, and
nothing here can be argued with -- a gate the model can talk its way past is
not a gate.

The failure being prevented is specific rather than theoretical. The analyzer
finds `validator` at three real lines; the corpus happens to document nothing
about it; the model, looking at four confident chunks about `Config`, reports
coverage as sufficient. The loop stops, and the report reads as complete while
the symbol with the most usage in the repository is the one nothing was found
for.

The asymmetry between confidence tiers is deliberate and is the spec's. Only
**high**-confidence symbols can block sufficiency: a medium- or low-confidence
symbol is one the analyzer itself is not sure the repository really uses, so
demanding corpus evidence for it would make the retrieval loop iterate against
its own uncertainty rather than against a real gap. Uncovered symbols at every
tier are still reported -- Phase 5 routes them to `unknowns` -- they just do
not force another round.
"""

from upgradepilot.models.base import HonestModel
from upgradepilot.models.enums import Confidence
from upgradepilot.models.evidence import NonBlankStr
from upgradepilot.models.knowledge import RetrievedChunk
from upgradepilot.models.repo import SymbolInventory


class SymbolCoverage(HonestModel):
    """Whether one symbol from the repository has any documentation behind it."""

    symbol: NonBlankStr
    confidence: Confidence
    covering_chunk_ids: tuple[NonBlankStr, ...] = ()
    """Every retrieved chunk whose document names this symbol.

    The ids, not a count: a coverage claim the reader cannot follow back to a
    chunk is the kind of unsourced assertion this project exists to avoid.
    """

    @property
    def covered(self) -> bool:
        """Computed, never stored (CLAUDE.md rule 21). A stored flag could
        say `True` beside an empty id list."""
        return bool(self.covering_chunk_ids)


class CoverageReport(HonestModel):
    """Coverage for every symbol in the inventory, plus the gate's verdict."""

    entries: tuple[SymbolCoverage, ...] = ()

    @property
    def uncovered(self) -> tuple[str, ...]:
        """Every symbol with no supporting chunk, at any confidence tier."""
        return tuple(entry.symbol for entry in self.entries if not entry.covered)

    @property
    def uncovered_high_confidence(self) -> tuple[str, ...]:
        return tuple(
            entry.symbol
            for entry in self.entries
            if not entry.covered and entry.confidence is Confidence.HIGH
        )

    @property
    def sufficient(self) -> bool:
        """The deterministic gate.

        Says only "no high-confidence symbol is missing evidence". It is
        **not** a claim that the upgrade is safe or that the corpus is
        adequate -- an empty inventory is trivially sufficient because there
        is nothing to cover. Whether any evidence exists at all is
        `evidence_available`'s job, and §8.1 turns that into a separate hard
        confidence ceiling. Collapsing the two would let "we asked about
        nothing" read as "we found everything".
        """
        return not self.uncovered_high_confidence


def annotate_coverage(
    inventory: SymbolInventory,
    chunks: tuple[RetrievedChunk, ...],
) -> CoverageReport:
    """Match the repository's symbols against what the retrieved documents claim.

    Coverage is read from `RetrievedChunk.affected_symbols` -- the document's
    own statement of what it is about -- and never from `matched_symbols`.
    The latter is annotated against whatever the *caller* asked about, so a
    caller that forgot to pass its symbols would leave it empty on every
    chunk; a gate reading it would then flip its verdict on a plumbing
    mistake rather than on the evidence.

    Matching is exact, mirroring Chroma's `$contains`. A document about
    `ConfigDict` does not document `Config`, and accepting a prefix here
    would let the gate pass on a symbol nothing explains -- reintroducing in
    Python the very laxity the store's operator choice avoids.

    Entries are sorted by symbol so two runs over identical input produce
    identical output, including in the trace.
    """
    covering: dict[str, list[str]] = {}
    for chunk in chunks:
        for symbol in chunk.affected_symbols:
            covering.setdefault(symbol, []).append(chunk.chunk_id)

    entries = tuple(
        SymbolCoverage(
            symbol=stat.symbol,
            confidence=stat.confidence,
            covering_chunk_ids=tuple(sorted(set(covering.get(stat.symbol, ())))),
        )
        for stat in sorted(inventory.entries, key=lambda stat: stat.symbol)
    )
    return CoverageReport(entries=entries)
