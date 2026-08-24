"""Repository analysis outputs. Pure data; no I/O."""

from datetime import datetime
from typing import Annotated, Self

from pydantic import Field, StringConstraints, model_validator

from upgradepilot.models.base import HonestModel
from upgradepilot.models.enums import (
    Confidence,
    DependencyRole,
    ManifestKind,
    UsageKind,
    VersionConfidence,
)
from upgradepilot.models.evidence import NonBlankStr

_CONFIDENCE_ORDER: dict[Confidence, int] = {
    Confidence.LOW: 0,
    Confidence.MEDIUM: 1,
    Confidence.HIGH: 2,
}

ShaStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=7)]
"""A commit SHA prefix. `Field(min_length=7)` alone accepts seven spaces
(verified against pydantic 2.13.4) because length is counted before any
whitespace is considered; stripping first closes that gap."""


class Manifest(HonestModel):
    path: NonBlankStr
    kind: ManifestKind
    declared_specifier: str | None = None


class DetectedVersion(HonestModel):
    value: NonBlankStr
    specifier: str | None
    source_manifest: Manifest
    confidence: VersionConfidence
    role: DependencyRole


class UsageSite(HonestModel):
    file: NonBlankStr
    line: int = Field(ge=1)
    column: int = Field(ge=0)
    symbol: NonBlankStr
    kind: UsageKind
    confidence: Confidence
    # Deliberately NOT `NonBlankStr`: this is a verbatim quote of the source
    # line, and leading whitespace is the file's own indentation. Stripping
    # it would corrupt the evidence (mirrors `RepoEvidence.snippet` in
    # `models/evidence.py`).
    snippet: str | None = None


class SkippedFile(HonestModel):
    path: NonBlankStr
    reason: NonBlankStr


class SymbolStat(HonestModel):
    symbol: NonBlankStr
    count: int = Field(ge=1)
    files: tuple[NonBlankStr, ...] = Field(min_length=1)
    confidence: Confidence


class SymbolInventory(HonestModel):
    entries: tuple[SymbolStat, ...] = ()

    @property
    def by_symbol(self) -> dict[str, SymbolStat]:
        """Lookup view. Computed, never stored -- see CLAUDE.md rule 21:
        a stored `dict[str, SymbolStat]` would let the key and `.symbol`
        disagree; this property can't drift because it is derived fresh
        from `.symbol` every time."""
        return {stat.symbol: stat for stat in self.entries}

    @classmethod
    def from_sites(cls, sites: list[UsageSite]) -> Self:
        """Aggregate sites per symbol.

        A symbol's confidence is the best of its sites (spec 7.1): one
        high-confidence site makes the symbol high-confidence, because the
        retrieval sufficiency gate and evidence_coverage are both defined
        over high-confidence symbols.
        """
        grouped: dict[str, list[UsageSite]] = {}
        for site in sites:
            grouped.setdefault(site.symbol, []).append(site)

        stats: list[SymbolStat] = []
        for symbol, symbol_sites in grouped.items():
            best = max(symbol_sites, key=lambda s: _CONFIDENCE_ORDER[s.confidence]).confidence
            stats.append(
                SymbolStat(
                    symbol=symbol,
                    count=len(symbol_sites),
                    files=tuple(sorted({s.file for s in symbol_sites})),
                    confidence=best,
                )
            )
        return cls(entries=tuple(sorted(stats, key=lambda s: s.symbol)))

    def high_confidence_symbols(self) -> tuple[str, ...]:
        return tuple(
            sorted(stat.symbol for stat in self.entries if stat.confidence is Confidence.HIGH)
        )


class AffectedFile(HonestModel):
    path: NonBlankStr
    usage_sites: tuple[UsageSite, ...] = Field(min_length=1)
    symbols: tuple[str, ...] = ()
    is_test: bool = False
    commit_count: int = Field(default=0, ge=0)
    last_modified: datetime | None = None

    @classmethod
    def from_sites(
        cls,
        path: str,
        sites: list[UsageSite],
        *,
        is_test: bool = False,
        commit_count: int = 0,
        last_modified: datetime | None = None,
    ) -> Self:
        return cls(
            path=path,
            usage_sites=tuple(sites),
            symbols=tuple(sorted({s.symbol for s in sites})),
            is_test=is_test,
            commit_count=commit_count,
            last_modified=last_modified,
        )


class CommitRecord(HonestModel):
    sha: ShaStr
    timestamp: datetime
    files: tuple[str, ...] = ()


class RepoAnalysis(HonestModel):
    commit_sha: str | None
    languages: dict[str, float] = Field(default_factory=dict)
    manifests: tuple[Manifest, ...] = ()
    detected_version: DetectedVersion | None
    total_python_files: int = Field(ge=0)
    analyzed_files: int = Field(ge=0)
    skipped_files: tuple[SkippedFile, ...] = ()
    affected_files: tuple[AffectedFile, ...] = ()
    symbol_inventory: SymbolInventory
    commit_records: tuple[CommitRecord, ...] = ()
    test_paths: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _analyzed_and_skipped_fit_within_total(self) -> Self:
        """Bounds `skipped_ratio` at 1.0 so it cannot corrupt the
        analysis_coverage risk factor or the confidence ceiling that reads it.

        Deliberately `<=`, not `==`: files excluded by the `max_repo_files` /
        `max_repo_bytes` caps are attempted by neither the analyze path nor
        the skip path, so they legitimately fall into neither bucket. `<=` is
        what we can actually guarantee from these three counts alone.
        """
        skipped = len(self.skipped_files)
        if self.analyzed_files + skipped > self.total_python_files:
            raise ValueError(
                "analyzed_files + len(skipped_files) must not exceed total_python_files: "
                f"analyzed_files={self.analyzed_files}, skipped_files={skipped}, "
                f"total_python_files={self.total_python_files}"
            )
        return self

    @property
    def skipped_ratio(self) -> float:
        """Share of Python files that could not be parsed. Feeds the
        analysis_coverage risk factor and the confidence ceiling."""
        if self.total_python_files == 0:
            return 0.0
        return len(self.skipped_files) / self.total_python_files

    def version_discrepancy(self, stated: str) -> tuple[str, str] | None:
        """Return (stated, detected) when they disagree, else None.

        Surfaced in the report rather than silently overridden in either
        direction (spec 7.1).
        """
        if self.detected_version is None:
            return None
        # `stated` is raw caller input, not a validated model field, so it
        # still needs stripping here. `self.detected_version.value` is
        # already a stripped NonBlankStr; stripping it again would be
        # redundant and would wrongly imply it is equally untrusted.
        stated = stated.strip()
        if stated == self.detected_version.value:
            return None
        return (stated, self.detected_version.value)
