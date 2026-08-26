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


def _rank(declaration: Declaration) -> tuple[int, int, int, str]:
    """Sort key: says-something, then confidence tier, then kind, then path.

    `has_value` is first, ahead of both confidence and kind, because a
    declaration that names neither a version nor a specifier makes no
    statement about the version at all -- and without this term, kind order
    alone let it win. A bare `pydantic` in `requirements.txt` (kind 3)
    outranked `pydantic>=1.10,<2` in `pyproject.toml` (kind 4): both are
    RANGE, so the tier tied, and `resolve_version` returned None while the
    same `RepoAnalysis` carried `declared_specifier='>=1.10,<2'`. The
    product contradicted itself inside one object. Nothing carrying a value
    can be EXACT-but-valueless (EXACT is only ever set once a version has
    been parsed), so this term never disagrees with the two below it.

    The confidence tier is NOT dead code, contrary to the review's finding.
    Its argument -- lockfiles are always EXACT and already rank 0-2 by kind
    -- covers lock-versus-human only. Two `requirements*.txt` files are the
    case it misses: kind ties there, so the tier is what decides, and the
    path tie-break below would otherwise pick whichever sorts first
    regardless of whether it pins anything. See
    `test_an_exact_pin_outranks_a_range_of_the_same_kind_whatever_the_path`,
    written to fail if this term is removed.

    The path is the final tie-break so that two `requirements*.txt` files
    declaring the same package resolve deterministically -- without it the
    order is the filesystem walk's, which differs across platforms.
    """
    has_value = 0 if (declaration.version is not None or declaration.specifier is not None) else 1
    exact_first = 0 if declaration.confidence is VersionConfidence.EXACT else 1
    return (
        has_value,
        exact_first,
        _KIND_ORDER[declaration.manifest.kind],
        declaration.manifest.path,
    )


def resolve_version(
    declarations: tuple[Declaration, ...],
    *,
    canonical_name: str,
    unread: tuple[str, ...] = (),
) -> DetectedVersion | None:
    """Pick the most precise true statement about the installed version.

    Returns None only when NO declaration specifies a version or a specifier
    (e.g. `dependencies = ["pydantic"]` in pyproject.toml, and nothing else
    in the repository constrains it). Task 9 records that as a confidence
    reducer -- and because `_rank`'s first term is "carries a value", the
    reducer's claim that the version "could not be determined from this
    repository" is now true of the whole repository rather than merely of
    whichever declaration `min()` happened to land on.

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
        # The message says what was READ, not what exists. It used to claim
        # absence from "any dependency manifest in this repository" on the
        # strength of five Python filenames, which is false the moment the
        # repository is a JavaScript one -- `react` declared in
        # `package.json`, three lines from where the scan stopped. An error
        # message is a claim like any other (CLAUDE.md rule 1), and
        # `scan.unread` is what lets this one distinguish "not declared"
        # from "not looked at".
        read = "pyproject.toml, requirements*.txt, poetry.lock, uv.lock, Pipfile.lock"
        message = (
            f"{canonical_name!r} is not declared in any Python dependency manifest in "
            f"this repository ({read}), so there is no current version to upgrade from."
        )
        if unread:
            message += (
                f" This repository does have {', '.join(unread)}, which this analyzer "
                "does not read: it analyses Python only."
            )
        raise DependencyNotFoundError(
            message,
            detail=(
                f"canonical_name={canonical_name!r} declarations=0 "
                f"unread={','.join(unread) if unread else '-'}"
            ),
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
