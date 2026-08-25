"""Choosing one version from several manifests that each claim one.

The precedence order is spec 7.1's table, made total so that a repository
carrying three manifests resolves the same way on every run.
"""

from __future__ import annotations

from upgradepilot.models.enums import DependencyRole, ManifestKind, VersionConfidence
from upgradepilot.models.errors import DependencyNotFoundError
from upgradepilot.models.repo import DetectedVersion
from upgradepilot.services.analysis.manifests import Declaration

_KIND_ORDER: dict[ManifestKind, int] = {
    ManifestKind.POETRY_LOCK: 0,
    ManifestKind.UV_LOCK: 1,
    ManifestKind.PIPFILE_LOCK: 2,
    ManifestKind.REQUIREMENTS: 3,
    ManifestKind.PYPROJECT: 4,
}
"""Total order over manifest kinds, tie-breaking within a confidence tier.

Locks before human-authored files because a lock records what IS installed
while a specifier records what is PERMITTED, and the report's sentence is
"you are currently on X". The three lock kinds are ordered arbitrarily but
FIXED: their relative order carries no meaning, and having one is what makes
a repository holding two of them resolve identically on every run rather
than by dict iteration order.
"""


def _rank(declaration: Declaration) -> tuple[int, int, str]:
    """Sort key: confidence tier, then kind, then path.

    The path is the final tie-break so that two `requirements*.txt` files
    declaring the same package resolve deterministically -- without it the
    order is the filesystem walk's, which differs across platforms.
    """
    exact_first = 0 if declaration.confidence is VersionConfidence.EXACT else 1
    return (exact_first, _KIND_ORDER[declaration.manifest.kind], declaration.manifest.path)


def resolve_version(
    declarations: tuple[Declaration, ...], *, canonical_name: str
) -> DetectedVersion | None:
    """Pick the most precise true statement about the installed version.

    Returns None when the best declaration specifies neither a version nor a
    specifier (e.g., dependencies = ["pydantic"] in pyproject.toml with no
    version constraint). Task 9 will record this as a confidence reducer.

    Raises DependencyNotFoundError when nothing declares it -- spec 7.1's
    "never a guess". The caller (analyzer.py) does not catch it: a run for a
    dependency the repository does not use has no honest output.

    Known over-report: A `requirements.txt` produced by `pip-compile` lists
    transitive pins in the same shape as direct ones, so a dependency that is
    genuinely transitive reads as `DIRECT` there. Distinguishing them needs the
    `# via` comments pip-compile writes, which are not part of the requirements
    format and are absent from a hand-written file. Reported as `DIRECT`, which
    is the safer error: it understates how constrained the user is rather than
    overstating it.
    """
    if not declarations:
        raise DependencyNotFoundError(
            f"{canonical_name!r} is not declared in any dependency manifest in this "
            f"repository, so there is no current version to upgrade from.",
            detail=f"canonical_name={canonical_name!r} declarations=0",
        )

    best = min(declarations, key=_rank)
    role = (
        DependencyRole.DIRECT
        if any(not d.is_lockfile for d in declarations)
        else DependencyRole.TRANSITIVE_ONLY
    )
    value = best.version if best.version is not None else best.specifier
    if value is None:
        return None

    return DetectedVersion(
        value=value,
        specifier=best.specifier,
        source_manifest=best.manifest,
        confidence=best.confidence,
        role=role,
    )
