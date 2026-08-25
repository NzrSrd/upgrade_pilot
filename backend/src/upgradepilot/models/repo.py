"""Repository analysis outputs. Pure data; no I/O."""

import math
from typing import Annotated, Self

from pydantic import AwareDatetime, Field, StringConstraints, computed_field, model_validator

from upgradepilot.models.base import HonestModel
from upgradepilot.models.enums import (
    Confidence,
    DependencyRole,
    ManifestKind,
    UsageKind,
    VersionConfidence,
)
from upgradepilot.models.evidence import NonBlankStr, RepoRelativePath

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
    path: RepoRelativePath
    kind: ManifestKind
    declared_specifier: str | None = None


class DetectedVersion(HonestModel):
    value: NonBlankStr
    specifier: str | None
    source_manifest: Manifest
    confidence: VersionConfidence
    role: DependencyRole


class UsageSite(HonestModel):
    file: RepoRelativePath
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
    path: RepoRelativePath
    reason: NonBlankStr


class SymbolStat(HonestModel):
    symbol: NonBlankStr
    count: int = Field(ge=1)
    files: tuple[RepoRelativePath, ...] = Field(min_length=1)
    confidence: Confidence

    @model_validator(mode="after")
    def _count_cannot_contradict_its_own_files(self) -> Self:
        """`count` is usage *sites*, `files` is the distinct files they are
        in, so `count >= len(files)` always. The reverse -- a count of 1
        claiming three files -- is a stat that contradicts its own evidence,
        and `count` feeds the blast-radius figures the report quotes.

        Deliberately not `count == len(files)`: several sites in one file is
        the normal case, and `from_sites` produces exactly that.
        """
        if self.count < len(self.files):
            raise ValueError(
                "count must be at least the number of files it spans: "
                f"symbol={self.symbol!r}, count={self.count}, files={len(self.files)}"
            )
        return self


class SymbolInventory(HonestModel):
    entries: tuple[SymbolStat, ...] = ()

    @property
    def by_symbol(self) -> dict[str, SymbolStat]:
        """Lookup view. Computed, never stored -- see CLAUDE.md rule 21:
        a stored `dict[str, SymbolStat]` would let the key and `.symbol`
        disagree; this property can't drift because it is derived fresh
        from `.symbol` every time."""
        return {stat.symbol: stat for stat in self.entries}

    @model_validator(mode="after")
    def _symbols_are_unique(self) -> Self:
        """Two entries for one symbol make `by_symbol` silently drop one and
        make `high_confidence_symbols` double-count it. Both feed
        evidence_coverage, so a duplicate turns a coverage figure the report
        quotes into a number that matches no set of entries.
        """
        symbols = [stat.symbol for stat in self.entries]
        if len(symbols) != len(set(symbols)):
            duplicated = sorted({s for s in symbols if symbols.count(s) > 1})
            raise ValueError(f"duplicate symbols in inventory: {duplicated}")
        return self

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
    path: RepoRelativePath
    usage_sites: tuple[UsageSite, ...] = Field(min_length=1)
    is_test: bool = False
    commit_count: int | None = Field(default=None, ge=0)
    """Commits touching this file within the history window, or None.

    The three states are genuinely different and Phase 6's `churn_on_affected`
    factor reads all three:

      None  git history was not available -- the workspace has no `.git`, or
            the repository has no commits yet. Churn is UNKNOWN, and a factor
            computed from it must lower confidence rather than report calm.
      0     history WAS read and this file was not touched in the window.
            A real, low-churn signal.
      n>0   touched n times in the window.

    The default is None, not 0: a caller that omits it has supplied no
    history, and defaulting to 0 would let "we did not look" print as
    "this file is stable".
    """
    last_modified: AwareDatetime | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def symbols(self) -> tuple[NonBlankStr, ...]:
        """The distinct symbols used in this file, sorted.

        Derived, never stored -- CLAUDE.md rule 21, and the same argument
        `SymbolInventory.by_symbol` makes just above. As a field it could
        disagree with `usage_sites`, which is the one thing that must never
        happen here: `usage_sites` carries the file and line the report
        cites, and `symbols` is what the corpus is queried with, so drift
        between them means citing evidence for a symbol nobody uses.

        `@computed_field` rather than a bare property so it still appears in
        `model_dump()`, keeping the serialised shape it had as a field. It
        recomputes on access rather than caching: the input is a frozen
        tuple, so there is nothing to invalidate and nothing to go stale.
        """
        return tuple(sorted({site.symbol for site in self.usage_sites}))

    @model_validator(mode="after")
    def _sites_belong_to_this_file(self) -> Self:
        """Every usage site must be in the file this object names.

        An AffectedFile whose `path` disagrees with its sites' `file` reports
        a finding against one file while citing lines from another -- an
        exact instance of the failure CLAUDE.md rule 1 exists to prevent,
        and structurally valid without this check.
        """
        foreign = sorted({site.file for site in self.usage_sites if site.file != self.path})
        if foreign:
            raise ValueError(
                f"usage_sites must all belong to path={self.path!r}; found sites in {foreign}"
            )
        return self

    @classmethod
    def from_sites(
        cls,
        path: RepoRelativePath,
        sites: list[UsageSite],
        *,
        is_test: bool = False,
        commit_count: int | None = None,
        last_modified: AwareDatetime | None = None,
    ) -> Self:
        return cls(
            path=path,
            usage_sites=tuple(sites),
            is_test=is_test,
            commit_count=commit_count,
            last_modified=last_modified,
        )


class CommitRecord(HonestModel):
    sha: ShaStr
    timestamp: AwareDatetime
    files: tuple[RepoRelativePath, ...] = ()


class LanguageShare(HonestModel):
    """One language's share of the repository's recognised source files.

    Defined here in `models/`, not in `services/analysis/layout.py` where it is
    produced: `RepoAnalysis.languages` is typed as a tuple of these, and
    `models/` must never import `services/` (CLAUDE.md rule 16).
    """

    language: NonBlankStr
    share: float = Field(gt=0.0, le=1.0)
    file_count: int = Field(ge=1)


class RepoAnalysis(HonestModel):
    commit_sha: ShaStr | None
    """`ShaStr`, not `str`: `commit_sha=""` used to construct, and this sha is
    what every file-and-line citation in the report is resolved against.
    `CommitRecord.sha` was already `ShaStr`; these two name the same kind of
    thing and must be validated the same way."""

    languages: tuple[LanguageShare, ...] = ()
    manifests: tuple[Manifest, ...] = ()
    detected_version: DetectedVersion | None
    total_python_files: int = Field(ge=0)
    analyzed_files: int = Field(ge=0)
    skipped_files: tuple[SkippedFile, ...] = ()
    affected_files: tuple[AffectedFile, ...] = ()
    symbol_inventory: SymbolInventory
    commit_records: tuple[CommitRecord, ...] = ()
    test_paths: tuple[RepoRelativePath, ...] = ()
    confidence_reducers: tuple[NonBlankStr, ...] = ()
    """Reasons this analysis is less complete than its counts suggest.

    Each entry is one user-facing sentence, consumed by Phase 6's confidence
    ceilings (spec 8.1) and printed in the report.

    Deliberately NOT `skipped_files`: that tuple is divided by
    `total_python_files` to produce `skipped_ratio`, so a `.gitmodules` entry
    there would corrupt the analysis_coverage factor and could trip
    `_analyzed_and_skipped_fit_within_total`. These are a different kind of
    fact -- "something outside the Python files we counted was not analysed" --
    and they need their own channel.
    """

    @model_validator(mode="after")
    def _language_shares_are_unique_and_total_one(self) -> Self:
        """Two constraints the `dict` could not express.

        Uniqueness: a duplicate language made the old dict silently drop one
        entry; as a tuple it would instead be double-counted by any consumer
        that sums.

        Sum: the shares are computed over files with a RECOGNISED extension,
        so they partition that set and must total 1.0. The old field
        deliberately declined to require this, because the analyzer that
        populates it did not exist and the denominator was undecided. It
        exists now (`services/analysis/layout.py`) and the denominator is
        recognised files, so the constraint is checkable rather than invented.

        `math.fsum`, not `sum`: the shares are floats summed over up to a few
        dozen languages, and `fsum` avoids the rounding error a plain running
        sum would accumulate before it is compared against 1.0.
        """
        names = [entry.language for entry in self.languages]
        if len(names) != len(set(names)):
            duplicated = sorted({n for n in names if names.count(n) > 1})
            raise ValueError(f"duplicate languages: {duplicated}")
        if self.languages:
            total = math.fsum(entry.share for entry in self.languages)
            if not math.isclose(total, 1.0, abs_tol=1e-6):
                raise ValueError(f"language shares must total 1.0, got {total}")
        return self

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

    def citable_paths(self) -> frozenset[str]:
        """Every repository path this analysis is entitled to name.

        **The workspace is gone by the time anything needs this.**
        `analyze_repo` opens and closes it inside its own node, because a run
        pauses at `human_review` and may be resumed days later by a different
        process -- a workspace handle cannot survive that, and a remote clone
        re-opened on resume is a different checkout of a branch that may have
        moved. So spec 8.4's checks 2 and 3, written as "the file exists in
        the workspace", resolve against this record instead.

        That is a deliberate strengthening rather than a weakening. "Exists on
        disk" would accept any path in the repository, including one no part
        of this analysis ever looked at; this set is the paths the analysis
        actually read, so a citation outside it is one nothing here produced.
        The paths are `Path.relative_to(root).as_posix()` by construction, and
        they were verified to exist at the moment they were recorded.
        """
        return frozenset(
            {file.path for file in self.affected_files}
            | {manifest.path for manifest in self.manifests}
            | {skipped.path for skipped in self.skipped_files}
            | set(self.test_paths)
        )

    def citable_lines(self) -> frozenset[tuple[str, int]]:
        """Every `(file, line)` pair a `RepoEvidence` may name.

        Usage sites carry real, parsed line numbers. A manifest and an
        unparseable file do not -- an unparseable file has no lines by
        definition -- so both are citable at line 1 only, which points at the
        file rather than claiming a location inside it. `analysis_coverage`
        says so in its own detail text, and check 2 accepts it here so that
        the factor's own citations validate.
        """
        pairs = {
            (site.file, site.line) for file in self.affected_files for site in file.usage_sites
        }
        pairs |= {(manifest.path, 1) for manifest in self.manifests}
        pairs |= {(skipped.path, 1) for skipped in self.skipped_files}
        return frozenset(pairs)

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
