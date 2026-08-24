"""Repository analysis outputs. Pure data; no I/O."""

from datetime import datetime
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

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


class Manifest(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: NonBlankStr
    kind: ManifestKind
    declared_specifier: str | None = None


class DetectedVersion(BaseModel):
    model_config = ConfigDict(frozen=True)

    value: NonBlankStr
    specifier: str | None
    source_manifest: Manifest
    confidence: VersionConfidence
    role: DependencyRole


class UsageSite(BaseModel):
    model_config = ConfigDict(frozen=True)

    file: NonBlankStr
    line: int = Field(ge=1)
    column: int = Field(ge=0)
    symbol: NonBlankStr
    kind: UsageKind
    confidence: Confidence
    snippet: str | None = None


class SkippedFile(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: NonBlankStr
    reason: NonBlankStr


class SymbolStat(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: NonBlankStr
    count: int = Field(ge=1)
    files: tuple[NonBlankStr, ...] = Field(min_length=1)
    confidence: Confidence


class SymbolInventory(BaseModel):
    model_config = ConfigDict(frozen=True)

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


class AffectedFile(BaseModel):
    model_config = ConfigDict(frozen=True)

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


class CommitRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    sha: ShaStr
    timestamp: datetime
    files: tuple[str, ...] = ()


class RepoAnalysis(BaseModel):
    model_config = ConfigDict(frozen=True)

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
        if stated.strip() == self.detected_version.value.strip():
            return None
        return (stated.strip(), self.detected_version.value)
