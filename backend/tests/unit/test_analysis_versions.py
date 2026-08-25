"""Version precedence. Pure over Declaration records -- no files, no workspace."""

import pytest

from upgradepilot.models.enums import (
    DependencyRole,
    ManifestKind,
    VersionConfidence,
)
from upgradepilot.models.errors import DependencyNotFoundError
from upgradepilot.models.repo import Manifest
from upgradepilot.services.analysis.manifests import Declaration
from upgradepilot.services.analysis.versions import resolve_version


def _declaration(
    kind: ManifestKind,
    *,
    version: str | None = None,
    specifier: str | None = None,
    confidence: VersionConfidence = VersionConfidence.EXACT,
    is_lockfile: bool = False,
    path: str | None = None,
) -> Declaration:
    if path is None:
        path = f"{kind.value}-manifest"
    return Declaration(
        manifest=Manifest(path=path, kind=kind, declared_specifier=specifier),
        raw_name="pydantic",
        version=version,
        specifier=specifier,
        confidence=confidence,
        is_lockfile=is_lockfile,
    )


def test_no_declaration_raises_rather_than_guessing() -> None:
    """Spec 7.1: "absent -> DependencyNotFound error -- never a guess."

    The alternative -- returning None and letting the graph continue -- would
    produce a migration plan for a dependency the repository does not use,
    every claim in it correctly cited and the whole thing about the wrong
    subject.
    """
    with pytest.raises(DependencyNotFoundError) as caught:
        resolve_version((), canonical_name="pydantic")
    assert "pydantic" in caught.value.args[0]


def test_a_lockfile_pin_beats_a_pyproject_range() -> None:
    """The lock file records what is actually installed; the pyproject
    records what is permitted. The report says "you are on 1.10.13", which
    is only true of the former."""
    detected = resolve_version(
        (
            _declaration(
                ManifestKind.PYPROJECT, specifier=">=1.10,<2", confidence=VersionConfidence.RANGE
            ),
            _declaration(ManifestKind.POETRY_LOCK, version="1.10.13", is_lockfile=True),
        ),
        canonical_name="pydantic",
    )
    assert detected is not None
    assert detected.value == "1.10.13"
    assert detected.confidence is VersionConfidence.EXACT
    assert detected.source_manifest.kind is ManifestKind.POETRY_LOCK


def test_an_exact_requirements_pin_beats_a_pyproject_range() -> None:
    detected = resolve_version(
        (
            _declaration(
                ManifestKind.PYPROJECT, specifier="^1.10", confidence=VersionConfidence.RANGE
            ),
            _declaration(ManifestKind.REQUIREMENTS, version="1.10.13", specifier="==1.10.13"),
        ),
        canonical_name="pydantic",
    )
    assert detected is not None
    assert detected.value == "1.10.13"
    assert detected.confidence is VersionConfidence.EXACT


def test_a_range_only_declaration_reports_the_specifier_as_the_value() -> None:
    """There is no pin to report, so the specifier IS the most precise true
    statement available. Reporting a resolved-looking version here would be
    the guess spec 7.1 forbids."""
    detected = resolve_version(
        (
            _declaration(
                ManifestKind.PYPROJECT, specifier=">=1.10,<2", confidence=VersionConfidence.RANGE
            ),
        ),
        canonical_name="pydantic",
    )
    assert detected is not None
    assert detected.value == ">=1.10,<2"
    assert detected.specifier == ">=1.10,<2"
    assert detected.confidence is VersionConfidence.RANGE


def test_lockfile_only_means_the_user_does_not_control_the_pin() -> None:
    """Spec 7.1's TRANSITIVE_ONLY case, and the reason it exists: pydantic is
    usually pulled in by FastAPI rather than declared. "You do not directly
    control this pin" changes the migration story and caps confidence."""
    detected = resolve_version(
        (_declaration(ManifestKind.POETRY_LOCK, version="1.10.13", is_lockfile=True),),
        canonical_name="pydantic",
    )
    assert detected is not None
    assert detected.role is DependencyRole.TRANSITIVE_ONLY


def test_a_human_authored_manifest_means_direct() -> None:
    for kind in (ManifestKind.PYPROJECT, ManifestKind.REQUIREMENTS):
        detected = resolve_version(
            (
                _declaration(kind, version="1.10.13", specifier="==1.10.13"),
                _declaration(ManifestKind.POETRY_LOCK, version="1.10.13", is_lockfile=True),
            ),
            canonical_name="pydantic",
        )
        assert detected is not None
        assert detected.role is DependencyRole.DIRECT, kind


def test_two_lockfiles_disagreeing_resolve_deterministically() -> None:
    """A repository mid-migration between package managers really does carry
    both. Precedence must not depend on iteration order, because the version
    the report prints would otherwise change between runs on the same input."""
    forward = resolve_version(
        (
            _declaration(ManifestKind.POETRY_LOCK, version="1.10.13", is_lockfile=True),
            _declaration(ManifestKind.UV_LOCK, version="1.9.0", is_lockfile=True),
        ),
        canonical_name="pydantic",
    )
    backward = resolve_version(
        (
            _declaration(ManifestKind.UV_LOCK, version="1.9.0", is_lockfile=True),
            _declaration(ManifestKind.POETRY_LOCK, version="1.10.13", is_lockfile=True),
        ),
        canonical_name="pydantic",
    )
    assert forward is not None
    assert backward is not None
    assert forward.value == backward.value
    assert forward.source_manifest.kind is backward.source_manifest.kind


def test_same_kind_different_paths_resolve_by_path() -> None:
    """Two manifests of the same kind with same confidence tier but differing
    in path must resolve deterministically by alphabetical path order. This
    test exercises the path tie-break in _rank()."""
    forward = resolve_version(
        (
            _declaration(
                ManifestKind.REQUIREMENTS,
                version="1.10.13",
                path="requirements-a.txt",
            ),
            _declaration(
                ManifestKind.REQUIREMENTS,
                version="1.9.0",
                path="requirements-b.txt",
            ),
        ),
        canonical_name="pydantic",
    )
    backward = resolve_version(
        (
            _declaration(
                ManifestKind.REQUIREMENTS,
                version="1.9.0",
                path="requirements-b.txt",
            ),
            _declaration(
                ManifestKind.REQUIREMENTS,
                version="1.10.13",
                path="requirements-a.txt",
            ),
        ),
        canonical_name="pydantic",
    )
    # Both orders must select the same manifest (by path) and thus same version
    assert forward is not None
    assert backward is not None
    assert forward.source_manifest.path == "requirements-a.txt"
    assert backward.source_manifest.path == "requirements-a.txt"
    assert forward.value == "1.10.13"
    assert backward.value == "1.10.13"


def test_bare_declaration_returns_none() -> None:
    """A dependency declared by name with no version or specifier (common in
    pyproject.toml: dependencies = ["pydantic"]) returns None rather than
    raising. Task 9 will record this as a confidence reducer."""
    result = resolve_version(
        (_declaration(ManifestKind.PYPROJECT, version=None, specifier=None),),
        canonical_name="pydantic",
    )
    assert result is None


def test_bare_declaration_alongside_real_version_returns_real_version() -> None:
    """A bare declaration does not override one that specifies a version."""
    detected = resolve_version(
        (
            _declaration(ManifestKind.PYPROJECT, version=None, specifier=None),
            _declaration(ManifestKind.REQUIREMENTS, version="1.10.13", specifier="==1.10.13"),
        ),
        canonical_name="pydantic",
    )
    assert detected is not None
    assert detected.value == "1.10.13"
    assert detected.confidence is VersionConfidence.EXACT


# -- F4: a declaration carrying nothing must not outrank one that does ------


def test_a_bare_declaration_never_outranks_one_that_carries_a_specifier() -> None:
    """`pydantic` bare in `requirements.txt` (kind 3) alongside
    `pydantic>=1.10,<2` in `pyproject.toml` (kind 4). Both are RANGE, so the
    confidence tier ties and kind order alone decided -- handing `min()` the
    declaration that says nothing, and making `resolve_version` return None
    while the same `RepoAnalysis` carried `declared_specifier='>=1.10,<2'`.
    The product contradicted itself inside one object.

    `_rank`'s first term is now "does this declaration actually carry a
    value", ahead of both confidence and kind: a statement about the version
    always beats the absence of one, whatever file it came from.
    """
    detected = resolve_version(
        (
            _declaration(
                ManifestKind.REQUIREMENTS,
                version=None,
                specifier=None,
                confidence=VersionConfidence.RANGE,
                path="requirements.txt",
            ),
            _declaration(
                ManifestKind.PYPROJECT,
                version=None,
                specifier=">=1.10,<2",
                confidence=VersionConfidence.RANGE,
                path="pyproject.toml",
            ),
        ),
        canonical_name="pydantic",
    )
    assert detected is not None
    assert detected.value == ">=1.10,<2"
    assert detected.source_manifest.path == "pyproject.toml"


def test_an_exact_pin_outranks_a_range_of_the_same_kind_whatever_the_path() -> None:
    """Binds `_rank`'s confidence tier, which nothing did before: the review
    called it provably inert on the grounds that lockfiles are always EXACT
    and already rank ahead by kind. That argument covers lock-versus-human
    only. Two `requirements*.txt` files are the case it misses -- kind ties,
    so the tier is what decides, and the path tie-break below it would
    otherwise pick the RANGE one.

    `requirements-a.txt` sorts BEFORE `requirements-b.txt`, so the EXACT
    declaration is deliberately the alphabetically later of the two: without
    the tier, the path tie-break selects `-a` and this test fails.
    """
    detected = resolve_version(
        (
            _declaration(
                ManifestKind.REQUIREMENTS,
                specifier=">=1.10,<2",
                confidence=VersionConfidence.RANGE,
                path="requirements-a.txt",
            ),
            _declaration(
                ManifestKind.REQUIREMENTS,
                version="1.10.13",
                specifier="==1.10.13",
                confidence=VersionConfidence.EXACT,
                path="requirements-b.txt",
            ),
        ),
        canonical_name="pydantic",
    )
    assert detected is not None
    assert detected.value == "1.10.13"
    assert detected.source_manifest.path == "requirements-b.txt"
    assert detected.confidence is VersionConfidence.EXACT
