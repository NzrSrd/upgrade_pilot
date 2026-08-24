"""Finding and reading the five dependency manifest kinds.

Pure over text. `scan_manifests` is the only function that touches a
Workspace; every parser below takes a string, which is what makes them
testable against committed fixture files with no git repository.

No `packaging` (CLAUDE.md rule 12): this module classifies a specifier as
exact-or-range and reports its raw text. It never compares two versions, so
a specifier grammar is not needed and a dependency for one is not justified.
"""

from __future__ import annotations

import json
import re
import tomllib
from typing import TYPE_CHECKING, Protocol

from upgradepilot.models.base import HonestModel
from upgradepilot.models.enums import ManifestKind, VersionConfidence
from upgradepilot.models.evidence import NonBlankStr, RepoRelativePath
from upgradepilot.models.repo import Manifest

if TYPE_CHECKING:
    from upgradepilot.services.repo.workspace import Workspace

_PEP503_SEPARATORS = re.compile(r"[-_.]+")

_REQUIREMENT_LINE = re.compile(
    r"""
    ^\s*
    (?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)   # distribution name
    \s*
    (?P<extras>\[[^\]]*\])?                # optional extras, discarded
    \s*
    (?P<specifier>[^;#]*?)                 # everything up to a marker or comment
    \s*
    (?:;.*)?                               # environment marker, discarded
    (?:\#.*)?                              # trailing comment, discarded
    $
    """,
    re.VERBOSE,
)

_EXACT_PIN = re.compile(r"^==\s*(?P<version>[^\s,=<>!~]+)$")

_FILENAMES: tuple[tuple[str, ManifestKind], ...] = (
    ("pyproject.toml", ManifestKind.PYPROJECT),
    ("poetry.lock", ManifestKind.POETRY_LOCK),
    ("uv.lock", ManifestKind.UV_LOCK),
    ("Pipfile.lock", ManifestKind.PIPFILE_LOCK),
)

_REQUIREMENTS_NAME = re.compile(r"^[A-Za-z0-9._-]*requirements[A-Za-z0-9._-]*\.txt$")
"""A filename *containing* "requirements" and ending `.txt`.

Measured both candidates against 11 real filenames. The anchored form
`^requirements.*\\.txt$` and this one both reject the two required
exclusions (`notes-requirements.txt.bak`, `pyproject.toml.orig`), but the
anchored form also misses `dev-requirements.txt` and `test-requirements.txt`,
which are ordinary in real Python repositories. Under-matching silently
misses a genuine pin, so the report would print a version read from a
different manifest -- a wrong answer that looks right. Over-matching (there
is no real case among the 11 measured filenames where this form incorrectly
accepts) at worst puts a stray row in `manifests` with
`declared_specifier=None`, which is visible and changes no version. Given
that asymmetry, this module accepts the broader form.
"""


def canonicalize(name: str) -> str:
    """PEP 503. The same transform `DependencySpec.canonical_name` applies --
    matching here and keying the corpus there must agree exactly, or a
    manifest hit never reaches the documents that describe it."""
    return _PEP503_SEPARATORS.sub("-", name.strip()).lower()


class Declaration(HonestModel):
    """One dependency, as one manifest declares it."""

    manifest: Manifest
    raw_name: NonBlankStr
    """The name exactly as written. Kept for display: `Pydantic` in a
    pyproject is what the user sees in their own file, and echoing back a
    canonicalised `pydantic` makes the report look like it read a different
    file than the one on screen."""
    version: str | None = None
    """A concrete version, present only when the manifest pins one."""
    specifier: str | None = None
    """The raw specifier text, e.g. `>=1.10,<2` or `^1.10`. Never parsed
    into a grammar -- see the module docstring."""
    confidence: VersionConfidence
    is_lockfile: bool
    """Whether this manifest is machine-generated. Task 3 uses it for both
    precedence and `DependencyRole`: a dependency appearing ONLY in lock
    files is one the user does not directly control."""


class ManifestScan(HonestModel):
    manifests: tuple[Manifest, ...] = ()
    """Every manifest found, with `declared_specifier` filled in on those
    that declare the dependency. Becomes `RepoAnalysis.manifests`, which is
    how a reader sees two manifests disagreeing without this module needing
    a field to announce it."""
    declarations: tuple[Declaration, ...] = ()
    unreadable: tuple[RepoRelativePath, ...] = ()
    """Manifests that exist but could not be parsed. Task 9 turns each into
    a confidence reducer."""


def classify_manifest(path: str) -> ManifestKind | None:
    """Map a repo-relative path to its kind, or None if it is not a manifest."""
    name = path.rsplit("/", 1)[-1]
    for filename, kind in _FILENAMES:
        if name == filename:
            return kind
    if _REQUIREMENTS_NAME.match(name):
        return ManifestKind.REQUIREMENTS
    return None


def _parse_pyproject(text: str, *, manifest: Manifest, canonical_name: str) -> Declaration | None:
    """`[project].dependencies` (PEP 508 strings) and
    `[tool.poetry.dependencies]` (a name -> specifier table). Confidence is
    always RANGE: a pyproject entry is a declared constraint, not a resolved
    install, even when the constraint happens to read `==1.2.3`.

    `[project.optional-dependencies]` is deliberately not read: an optional
    extra is not a dependency this repository has.
    """
    try:
        data = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, ValueError):
        return None

    project = data.get("project")
    if isinstance(project, dict):
        for entry in project.get("dependencies", []):
            if not isinstance(entry, str):
                continue
            match = _REQUIREMENT_LINE.match(entry)
            if match is None:
                continue
            if canonicalize(match.group("name")) != canonical_name:
                continue
            specifier = match.group("specifier").strip() or None
            return Declaration(
                manifest=manifest,
                raw_name=match.group("name"),
                version=None,
                specifier=specifier,
                confidence=VersionConfidence.RANGE,
                is_lockfile=False,
            )

    tool = data.get("tool")
    poetry = tool.get("poetry") if isinstance(tool, dict) else None
    dependencies = poetry.get("dependencies") if isinstance(poetry, dict) else None
    if isinstance(dependencies, dict):
        for raw_name, spec in dependencies.items():
            if raw_name == "python":
                continue  # an interpreter constraint, not a distribution
            if canonicalize(raw_name) != canonical_name:
                continue
            if not isinstance(spec, str):
                continue  # a table form (e.g. {version = ..., extras = [...]})
            return Declaration(
                manifest=manifest,
                raw_name=raw_name,
                version=None,
                specifier=spec.strip() or None,
                confidence=VersionConfidence.RANGE,
                is_lockfile=False,
            )

    return None


def _parse_requirements(
    text: str, *, manifest: Manifest, canonical_name: str
) -> Declaration | None:
    """Line-oriented. Skip blanks, `#` comments, and any line starting with
    `-` (covers `-r`, `-e`, `--index-url`, `--find-links`)."""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("-"):
            continue
        match = _REQUIREMENT_LINE.match(stripped)
        if match is None:
            continue
        if canonicalize(match.group("name")) != canonical_name:
            continue
        specifier = match.group("specifier").strip() or None
        version = None
        confidence = VersionConfidence.RANGE
        if specifier is not None:
            exact = _EXACT_PIN.match(specifier)
            if exact is not None:
                version = exact.group("version")
                confidence = VersionConfidence.EXACT
        return Declaration(
            manifest=manifest,
            raw_name=match.group("name"),
            version=version,
            specifier=specifier,
            confidence=confidence,
            is_lockfile=False,
        )
    return None


def _parse_toml_lock(text: str, *, manifest: Manifest, canonical_name: str) -> Declaration | None:
    """Shared by `poetry.lock` and `uv.lock`: both expose `package` as a list
    of tables with a bare `version` (verified against real lock shapes)."""
    try:
        data = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, ValueError):
        return None

    packages = data.get("package")
    if not isinstance(packages, list):
        return None
    for entry in packages:
        if not isinstance(entry, dict):
            continue
        raw_name = entry.get("name")
        version = entry.get("version")
        if not isinstance(raw_name, str) or not isinstance(version, str):
            continue
        if canonicalize(raw_name) != canonical_name:
            continue
        return Declaration(
            manifest=manifest,
            raw_name=raw_name,
            version=version,
            specifier=None,
            confidence=VersionConfidence.EXACT,
            is_lockfile=True,
        )
    return None


def _parse_pipfile_lock(
    text: str, *, manifest: Manifest, canonical_name: str
) -> Declaration | None:
    """`json.loads`; search `default` then `develop`. Strips the leading
    `==` from `version` -- Pipfile.lock stores it as part of the string, and
    a parser that keeps it produces a version that never string-compares
    equal to the user's stated one."""
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None

    for section in ("default", "develop"):
        entries = data.get(section)
        if not isinstance(entries, dict):
            continue
        for raw_name, info in entries.items():
            if not isinstance(raw_name, str) or canonicalize(raw_name) != canonical_name:
                continue
            if not isinstance(info, dict):
                continue
            raw_version = info.get("version")
            if not isinstance(raw_version, str):
                continue
            return Declaration(
                manifest=manifest,
                raw_name=raw_name,
                version=raw_version.removeprefix("=="),
                specifier=raw_version,
                confidence=VersionConfidence.EXACT,
                is_lockfile=True,
            )
    return None


class _ManifestParser(Protocol):
    """The shape every per-kind parser above shares. Named so the dispatch
    table below can be typed as something other than `dict[str, Any]`
    (CLAUDE.md rule 17)."""

    def __call__(
        self, text: str, *, manifest: Manifest, canonical_name: str
    ) -> Declaration | None: ...


_PARSERS: dict[ManifestKind, _ManifestParser] = {
    ManifestKind.PYPROJECT: _parse_pyproject,
    ManifestKind.REQUIREMENTS: _parse_requirements,
    ManifestKind.POETRY_LOCK: _parse_toml_lock,
    ManifestKind.UV_LOCK: _parse_toml_lock,
    ManifestKind.PIPFILE_LOCK: _parse_pipfile_lock,
}


def parse_declaration(text: str, *, manifest: Manifest, canonical_name: str) -> Declaration | None:
    """Dispatch to the parser for `manifest.kind` and return what it declares
    for `canonical_name`, or None if the manifest does not declare it or
    could not be parsed at all."""
    parser = _PARSERS[manifest.kind]
    return parser(text, manifest=manifest, canonical_name=canonical_name)


def scan_manifests(workspace: Workspace, canonical_name: str) -> ManifestScan:
    """Find every manifest in the workspace and read this dependency out of it.

    `iter_files("")` rather than a per-suffix walk: the five kinds span three
    extensions, and the Workspace's single walk already applies the vendor
    skip list and the symlink containment check. Filtering the result is
    cheaper and safer than three walks that each need those rules reapplied.
    """
    manifests: list[Manifest] = []
    declarations: list[Declaration] = []
    unreadable: list[str] = []

    for relative in workspace.iter_files(""):
        path = relative.as_posix()
        kind = classify_manifest(path)
        if kind is None:
            continue
        try:
            text = workspace.read_text(relative)
        except (OSError, UnicodeDecodeError):
            unreadable.append(path)
            manifests.append(Manifest(path=path, kind=kind))
            continue

        bare = Manifest(path=path, kind=kind)
        declaration = parse_declaration(text, manifest=bare, canonical_name=canonical_name)
        if declaration is None:
            manifests.append(bare)
            continue
        filled = Manifest(path=path, kind=kind, declared_specifier=declaration.specifier)
        manifests.append(filled)
        declarations.append(declaration.model_copy(update={"manifest": filled}))

    return ManifestScan(
        manifests=tuple(sorted(manifests, key=lambda m: m.path)),
        declarations=tuple(sorted(declarations, key=lambda d: d.manifest.path)),
        unreadable=tuple(sorted(unreadable)),
    )
