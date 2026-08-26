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
from upgradepilot.models.inputs import canonicalize_name
from upgradepilot.models.repo import Manifest

if TYPE_CHECKING:
    from upgradepilot.services.repo.workspace import Workspace

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

_UNREAD_MANIFESTS: frozenset[str] = frozenset({"package.json"})
"""Manifests this analyzer knows exist and deliberately does not read.

ADR-001 records the analyzer as Python-only, and that is not only the manifest
reader: `layout.py` collects `.py` and `.pyi`, and `imports.py` parses with
Python's `ast`. Reading `package.json` for meaning would remove the only
honest signal a JavaScript repository produces and replace it with a report of
zeroes carrying no error at all -- worse than the error, because it would look
like an answer.

So this records presence and nothing else. It is what lets
`resolve_version` say "not read" instead of "not declared", which are
different claims and only one of them is true of a repository whose
dependencies all live in a file this scan skips. `package.json` alone because
it is the one ecosystem the product is actually pointed at by mistake; the set
is the place to add another when that changes.
"""

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
    unread: tuple[RepoRelativePath, ...] = ()
    """Manifests for an ecosystem this analyzer does not read, found but never
    opened -- see `_UNREAD_MANIFESTS`. Not a confidence reducer: it does not
    make the Python analysis less certain, it explains an absence."""


def classify_manifest(path: str) -> ManifestKind | None:
    """Map a repo-relative path to its kind, or None if it is not a manifest."""
    name = path.rsplit("/", 1)[-1]
    for filename, kind in _FILENAMES:
        if name == filename:
            return kind
    if _REQUIREMENTS_NAME.match(name):
        return ManifestKind.REQUIREMENTS
    return None


class _Unparseable:
    """Singleton marking "this manifest's text could not be decoded at all".

    Distinct from `None`, which the parsers below use to mean "decoded fine;
    this dependency is just not declared here". Both used to collapse onto
    the same `None`, which is how a corrupt `pyproject.toml` and a merely
    irrelevant one became indistinguishable to `scan_manifests` -- see the
    module-level note on `_parse` for the fix this enables.
    """

    __slots__ = ()

    def __repr__(self) -> str:
        return "UNPARSEABLE"


UNPARSEABLE = _Unparseable()


def _parse_pyproject(
    text: str, *, manifest: Manifest, canonical_name: str
) -> Declaration | None | _Unparseable:
    """`[project].dependencies` (PEP 508 strings) and
    `[tool.poetry.dependencies]` (a name -> specifier table, or a name ->
    inline-table for extras/markers/git/path sources). Confidence is always
    RANGE: a pyproject entry is a declared constraint, not a resolved
    install, even when the constraint happens to read `==1.2.3`.

    `[project.optional-dependencies]` is deliberately not read: an optional
    extra is not a dependency this repository has.
    """
    try:
        data = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, ValueError):
        return UNPARSEABLE

    project = data.get("project")
    if isinstance(project, dict):
        for entry in project.get("dependencies", []):
            if not isinstance(entry, str):
                continue
            match = _REQUIREMENT_LINE.match(entry)
            if match is None:
                continue
            if canonicalize_name(match.group("name")) != canonical_name:
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
            if canonicalize_name(raw_name) != canonical_name:
                continue
            specifier = _poetry_specifier(spec)
            return Declaration(
                manifest=manifest,
                raw_name=raw_name,
                version=None,
                specifier=specifier,
                confidence=VersionConfidence.RANGE,
                is_lockfile=False,
            )

    return None


def _poetry_specifier(spec: object) -> str | None:
    """The raw specifier text for one `[tool.poetry.dependencies]` entry.

    Poetry spells a plain version constraint as a bare string
    (`pydantic = "^1.10"`), but extras, environment markers, and git/path
    sources are all spelled as an inline table instead
    (`pydantic = { version = "^1.10", extras = ["email"] }`,
    `requests = { git = "...", branch = "main" }`). A parser that only
    handled the string form used to `continue` past a table-form entry
    entirely, silently reporting the dependency as not declared at all --
    a confident wrong answer, not missing data.

    The table form's own `version` key is read when present, since that is
    the overwhelmingly common case and the file plainly states it. Only a
    table with no `version` key at all (a pure git/path source pins nothing
    at this layer) falls back to `specifier=None` -- "declared, but this
    repository does not pin a version here" is the honest reading, not
    "not declared".
    """
    if isinstance(spec, str):
        return spec.strip() or None
    if isinstance(spec, dict):
        version = spec.get("version")
        if isinstance(version, str):
            return version.strip() or None
        return None
    return None  # some other shape poetry does not actually produce


def _parse_requirements(
    text: str, *, manifest: Manifest, canonical_name: str
) -> Declaration | None | _Unparseable:
    """Line-oriented. Skip blanks, `#` comments, and any line starting with
    `-` (covers `-r`, `-e`, `--index-url`, `--find-links`).

    Never returns UNPARSEABLE: there is no decode step here to fail -- every
    line is either a recognised shape or is skipped, so a requirements file
    genuinely cannot be "unreadable" in the sense the other four kinds can.
    """
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("-"):
            continue
        match = _REQUIREMENT_LINE.match(stripped)
        if match is None:
            continue
        if canonicalize_name(match.group("name")) != canonical_name:
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


def _parse_toml_lock(
    text: str, *, manifest: Manifest, canonical_name: str
) -> Declaration | None | _Unparseable:
    """Shared by `poetry.lock` and `uv.lock`: both expose `package` as a list
    of tables with a bare `version` (verified against real lock shapes)."""
    try:
        data = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, ValueError):
        return UNPARSEABLE

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
        if canonicalize_name(raw_name) != canonical_name:
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
) -> Declaration | None | _Unparseable:
    """`json.loads`; search `default` then `develop`. Strips the leading
    `==` from `version` -- Pipfile.lock stores it as part of the string, and
    a parser that keeps it produces a version that never string-compares
    equal to the user's stated one."""
    try:
        data = json.loads(text)
    except ValueError:
        return UNPARSEABLE
    if not isinstance(data, dict):
        # Valid JSON, but not shaped like a Pipfile.lock at all (e.g. a bare
        # JSON array). The *decode* step succeeded, so this is "does not
        # declare anything", not "could not be parsed" -- the same
        # distinction `_parse_toml_lock` draws for TOML that decodes fine
        # but has no `package` list.
        return None

    for section in ("default", "develop"):
        entries = data.get(section)
        if not isinstance(entries, dict):
            continue
        for raw_name, info in entries.items():
            if not isinstance(raw_name, str) or canonicalize_name(raw_name) != canonical_name:
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
    ) -> Declaration | None | _Unparseable: ...


_PARSERS: dict[ManifestKind, _ManifestParser] = {
    ManifestKind.PYPROJECT: _parse_pyproject,
    ManifestKind.REQUIREMENTS: _parse_requirements,
    ManifestKind.POETRY_LOCK: _parse_toml_lock,
    ManifestKind.UV_LOCK: _parse_toml_lock,
    ManifestKind.PIPFILE_LOCK: _parse_pipfile_lock,
}


def _parse(
    text: str, *, manifest: Manifest, canonical_name: str
) -> Declaration | None | _Unparseable:
    """Dispatch to the parser for `manifest.kind`. Three states, kept apart
    on purpose:

    - a `Declaration`: the manifest parses and declares `canonical_name`.
    - `None`: the manifest parses cleanly but does not declare it.
    - `UNPARSEABLE`: the manifest's text could not be decoded at all.

    `parse_declaration` below collapses the last two into one `None` for
    callers that only need "is there a Declaration". `scan_manifests` needs
    the three-way distinction to populate `unreadable` correctly, and calls
    this directly rather than through that collapsed view.
    """
    parser = _PARSERS[manifest.kind]
    return parser(text, manifest=manifest, canonical_name=canonical_name)


def parse_declaration(text: str, *, manifest: Manifest, canonical_name: str) -> Declaration | None:
    """Dispatch to the parser for `manifest.kind` and return what it declares
    for `canonical_name`, or None if the manifest does not declare it or
    could not be parsed at all.

    This two-state view is what every caller other than `scan_manifests`
    wants, and what the parser-level tests in this package's test suite
    exercise directly.
    """
    result = _parse(text, manifest=manifest, canonical_name=canonical_name)
    return result if isinstance(result, Declaration) else None


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
    unread: list[str] = []

    for relative in workspace.iter_files(""):
        path = relative.as_posix()
        kind = classify_manifest(path)
        if kind is None:
            # Presence only, and only for the ones we can name. The file is
            # never opened: see `_UNREAD_MANIFESTS`.
            if path.rsplit("/", 1)[-1] in _UNREAD_MANIFESTS:
                unread.append(path)
            continue
        try:
            text = workspace.read_text(relative)
        except (OSError, UnicodeDecodeError):
            unreadable.append(path)
            manifests.append(Manifest(path=path, kind=kind))
            continue

        bare = Manifest(path=path, kind=kind)
        result = _parse(text, manifest=bare, canonical_name=canonical_name)
        if isinstance(result, _Unparseable):
            # Read fine as bytes, but the manifest FORMAT itself did not
            # decode: a corrupt pyproject.toml must not read the same as one
            # that simply does not mention this dependency (CLAUDE.md rule
            # 20 -- a caught decode error always produces a recorded
            # outcome, never a silent "not found").
            unreadable.append(path)
            manifests.append(bare)
            continue
        if result is None:
            manifests.append(bare)
            continue
        filled = Manifest(path=path, kind=kind, declared_specifier=result.specifier)
        manifests.append(filled)
        declarations.append(result.model_copy(update={"manifest": filled}))

    return ManifestScan(
        manifests=tuple(sorted(manifests, key=lambda m: m.path)),
        declarations=tuple(sorted(declarations, key=lambda d: d.manifest.path)),
        unreadable=tuple(sorted(unreadable)),
        unread=tuple(sorted(unread)),
    )
