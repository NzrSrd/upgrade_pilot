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
    version=None,
    specifier=None,
    confidence=VersionConfidence.EXACT,
    is_lockfile=False,
) -> Declaration:
    return Declaration(
        manifest=Manifest(path=f"{kind.value}-manifest", kind=kind, declared_specifier=specifier),
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
    assert forward.value == backward.value
    assert forward.source_manifest.kind is backward.source_manifest.kind
