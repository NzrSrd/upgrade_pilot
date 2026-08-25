"""Manifest parsing. Pure functions over text -- no workspace, no git."""

from pathlib import Path

import pytest

from tests.fixtures.repo_builder import build_sample_repo
from upgradepilot.models.enums import ManifestKind, VersionConfidence
from upgradepilot.models.inputs import DependencySpec
from upgradepilot.models.repo import Manifest
from upgradepilot.services.analysis.analyzer import analyze_repository
from upgradepilot.services.analysis.manifests import (
    classify_manifest,
    parse_declaration,
    scan_manifests,
)
from upgradepilot.services.repo.workspace import Workspace

MANIFESTS = Path(__file__).parent.parent / "fixtures" / "manifests"


@pytest.mark.parametrize(
    ("fixture", "kind", "version", "specifier", "confidence", "is_lockfile"),
    [
        (
            "pyproject_pep621.toml",
            ManifestKind.PYPROJECT,
            None,
            ">=1.10,<2",
            VersionConfidence.RANGE,
            False,
        ),
        (
            "pyproject_poetry.toml",
            ManifestKind.PYPROJECT,
            None,
            "^1.10",
            VersionConfidence.RANGE,
            False,
        ),
        (
            "requirements_pinned.txt",
            ManifestKind.REQUIREMENTS,
            "1.10.13",
            "==1.10.13",
            VersionConfidence.EXACT,
            False,
        ),
        (
            "requirements_ranged.txt",
            ManifestKind.REQUIREMENTS,
            None,
            ">=1.10,<2",
            VersionConfidence.RANGE,
            False,
        ),
        ("poetry.lock", ManifestKind.POETRY_LOCK, "1.10.13", None, VersionConfidence.EXACT, True),
        ("uv.lock", ManifestKind.UV_LOCK, "1.10.13", None, VersionConfidence.EXACT, True),
        (
            "Pipfile.lock",
            ManifestKind.PIPFILE_LOCK,
            "1.10.13",
            "==1.10.13",
            VersionConfidence.EXACT,
            True,
        ),
    ],
)
def test_each_manifest_kind_yields_the_expected_declaration(
    fixture: str,
    kind: ManifestKind,
    version: str | None,
    specifier: str | None,
    confidence: VersionConfidence,
    is_lockfile: bool,
) -> None:
    """One row per manifest kind the spec names. All five kinds, both
    pyproject dialects, and both requirements shapes."""
    text = (MANIFESTS / fixture).read_text(encoding="utf-8")
    manifest = Manifest(path=f"sub/{fixture}", kind=kind)

    declaration = parse_declaration(text, manifest=manifest, canonical_name="pydantic")

    assert declaration is not None, f"{fixture} declares pydantic but the parser missed it"
    assert declaration.version == version
    assert declaration.specifier == specifier
    assert declaration.confidence is confidence
    assert declaration.is_lockfile is is_lockfile


@pytest.mark.parametrize(
    ("fixture", "kind"),
    [
        ("pyproject_pep621.toml", ManifestKind.PYPROJECT),
        ("pyproject_poetry.toml", ManifestKind.PYPROJECT),
        ("requirements_pinned.txt", ManifestKind.REQUIREMENTS),
        ("requirements_ranged.txt", ManifestKind.REQUIREMENTS),
        ("poetry.lock", ManifestKind.POETRY_LOCK),
        ("uv.lock", ManifestKind.UV_LOCK),
        ("Pipfile.lock", ManifestKind.PIPFILE_LOCK),
    ],
)
def test_no_manifest_kind_reports_a_dependency_it_does_not_declare(
    fixture: str, kind: ManifestKind
) -> None:
    """The negative direction. Without this, a parser that returns a
    declaration for every query -- ignoring `canonical_name` entirely --
    passes every assertion above.

    `kind` is supplied explicitly rather than derived from
    `classify_manifest(f"sub/{fixture}")`: two fixture filenames
    (`pyproject_pep621.toml`, `pyproject_poetry.toml`) exist side by side to
    hold both pyproject dialects and so do not literally equal
    `pyproject.toml`, which is the only string `classify_manifest` maps to
    `ManifestKind.PYPROJECT`. `classify_manifest` itself is exercised
    directly by `test_requirements_filename_classification` and the
    `scan_manifests` tests below.
    """
    text = (MANIFESTS / fixture).read_text(encoding="utf-8")
    manifest = Manifest(path=f"sub/{fixture}", kind=kind)
    assert parse_declaration(text, manifest=manifest, canonical_name="numpy") is None


def test_poetry_python_entry_is_never_reported_as_a_dependency() -> None:
    """`[tool.poetry.dependencies]` lists `python = "^3.11"`. It is an
    interpreter constraint, not a distribution, and reporting it would make
    an upgrade of "python" analysable as if it were a package."""
    text = (MANIFESTS / "pyproject_poetry.toml").read_text(encoding="utf-8")
    manifest = Manifest(path="pyproject.toml", kind=ManifestKind.PYPROJECT)
    assert parse_declaration(text, manifest=manifest, canonical_name="python") is None


def test_poetry_table_form_dependency_reads_its_version_key() -> None:
    """Poetry spells extras, markers, and git/path sources as an inline
    table rather than a bare string: `pydantic = { version = "^1.10",
    extras = ["email"] }`. A parser that only handles the string form used
    to `continue` past this entirely, reporting `pydantic` as not declared
    at all -- a confident wrong answer. The table's own `version` key is
    read when present, since that is the overwhelmingly common case and the
    file plainly states it."""
    text = (MANIFESTS / "pyproject_poetry_table.toml").read_text(encoding="utf-8")
    manifest = Manifest(path="pyproject.toml", kind=ManifestKind.PYPROJECT)
    declaration = parse_declaration(text, manifest=manifest, canonical_name="pydantic")
    assert declaration is not None
    assert declaration.specifier == "^1.10"
    assert declaration.version is None
    assert declaration.confidence is VersionConfidence.RANGE


def test_poetry_table_form_dependency_without_a_version_key_is_still_declared() -> None:
    """A git/path source table (`requests = { git = "...", branch = "main"
    }`) has no `version` key at all. Recording `specifier=None` here means
    "declared, but this repository does not pin a version" -- a different,
    honest fact from "not declared", which a `canonical_name` mismatch
    already covers elsewhere. Reporting either a fabricated specifier or a
    silent absence would both be worse than this."""
    text = (MANIFESTS / "pyproject_poetry_table.toml").read_text(encoding="utf-8")
    manifest = Manifest(path="pyproject.toml", kind=ManifestKind.PYPROJECT)
    declaration = parse_declaration(text, manifest=manifest, canonical_name="requests")
    assert declaration is not None
    assert declaration.specifier is None
    assert declaration.version is None
    assert declaration.confidence is VersionConfidence.RANGE


def test_matching_is_on_the_canonical_name_not_the_written_one() -> None:
    """The poetry fixture writes `Pydantic`, capital P. PEP 503 says that is
    the same distribution as `pydantic`, and the corpus is keyed on the
    canonical form, so a parser matching raw text would silently find
    nothing here."""
    text = (MANIFESTS / "pyproject_poetry.toml").read_text(encoding="utf-8")
    manifest = Manifest(path="pyproject.toml", kind=ManifestKind.PYPROJECT)
    declaration = parse_declaration(text, manifest=manifest, canonical_name="pydantic")
    assert declaration is not None
    assert declaration.raw_name == "Pydantic", "the name as written is kept for display"


def test_pipfile_lock_strips_the_equals_operator_from_its_version() -> None:
    """Pipfile.lock stores `"version": "==1.10.13"`. Verified against a real
    lock file shape. A parser that does not strip it produces a
    DetectedVersion of `==1.10.13`, which the report then prints as the
    installed version and which never string-compares equal to the user's
    stated `1.10.13` -- manufacturing a version discrepancy out of a parsing
    bug."""
    text = (MANIFESTS / "Pipfile.lock").read_text(encoding="utf-8")
    manifest = Manifest(path="Pipfile.lock", kind=ManifestKind.PIPFILE_LOCK)
    declaration = parse_declaration(text, manifest=manifest, canonical_name="pydantic")
    assert declaration is not None
    assert declaration.version == "1.10.13"
    assert not declaration.version.startswith("=")


@pytest.mark.parametrize(
    "line",
    [
        "-r other-requirements.txt",
        "--index-url https://example.invalid/simple",
        "# comment line",
        "",
        "   ",
    ],
)
def test_requirements_directives_and_comments_are_not_dependencies(line: str) -> None:
    """A requirements file is not a list of packages; it is a list of pip
    arguments that mostly happen to be packages."""
    manifest = Manifest(path="requirements.txt", kind=ManifestKind.REQUIREMENTS)
    assert parse_declaration(line, manifest=manifest, canonical_name="other-requirements") is None


def test_a_bare_requirement_line_with_no_specifier_has_a_none_specifier() -> None:
    """`requests` with no version at all matches `_REQUIREMENT_LINE` with
    `specifier=''`. `Declaration.specifier` is `str | None`, and an empty
    string is not a specifier -- it must be normalised to None, not kept as
    the empty match text."""
    manifest = Manifest(path="requirements.txt", kind=ManifestKind.REQUIREMENTS)
    declaration = parse_declaration("requests", manifest=manifest, canonical_name="requests")
    assert declaration is not None
    assert declaration.specifier is None
    assert declaration.version is None
    assert declaration.confidence is VersionConfidence.RANGE


@pytest.mark.parametrize(
    ("text", "why"),
    [
        ("[project\nname = 'x'", "unterminated table header"),
        ("{not json", "truncated json"),
    ],
)
def test_an_unparseable_manifest_returns_None_rather_than_raising(text: str, why: str) -> None:
    """A corrupt manifest in a user's repository must not abort the whole
    analysis: the other manifests may still answer the question. It is
    recorded by the caller (Task 9) as a confidence reducer.

    This is the one place in this package where a broad `except` is correct,
    and CLAUDE.md rule 20 still applies -- the caller turns the None into a
    recorded reason, so nothing is swallowed silently.
    """
    for kind in (ManifestKind.PYPROJECT, ManifestKind.PIPFILE_LOCK):
        manifest = Manifest(path=f"x-{kind.value}", kind=kind)
        assert parse_declaration(text, manifest=manifest, canonical_name="pydantic") is None


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("requirements.txt", True),
        ("dev-requirements.txt", True),
        ("test-requirements.txt", True),
        ("notes-requirements.txt.bak", False),
        ("pyproject.toml.orig", False),
        ("requirements.in", False),
        ("readme.txt", False),
    ],
)
def test_requirements_filename_classification(name: str, expected: bool) -> None:
    """`_REQUIREMENTS_NAME` matches a filename *containing* "requirements"
    and ending `.txt`, not only one starting with it: `dev-requirements.txt`
    and `test-requirements.txt` are ordinary in real Python repositories, and
    under-matching them would silently miss a genuine pin. Over-matching
    (there is none here that the anchored form did not already reject) puts
    a stray, visible row in `manifests` rather than a wrong answer."""
    kind = classify_manifest(f"sub/{name}")
    assert (kind is ManifestKind.REQUIREMENTS) is expected


def test_scan_manifests_finds_both_manifests_in_the_sample_repo(tmp_path: Path) -> None:
    root = build_sample_repo(tmp_path)
    scan = scan_manifests(Workspace(root), canonical_name="pydantic")

    assert tuple(m.path for m in scan.manifests) == ("pyproject.toml", "requirements.txt")
    assert {d.manifest.kind for d in scan.declarations} == {
        ManifestKind.PYPROJECT,
        ManifestKind.REQUIREMENTS,
    }
    assert scan.unreadable == ()


def test_scan_manifests_ignores_files_that_merely_look_like_manifests(tmp_path: Path) -> None:
    """`my-requirements.txt.bak`, `pyproject.toml.orig` and a `requirements/`
    DIRECTORY are all things real repositories contain. Matching on a
    substring rather than the whole filename picks up all three."""
    root = build_sample_repo(tmp_path)
    (root / "pyproject.toml.orig").write_text("[project]\n", encoding="utf-8")
    (root / "notes-requirements.txt.bak").write_text("pydantic==9\n", encoding="utf-8")

    scan = scan_manifests(Workspace(root), canonical_name="pydantic")
    assert tuple(m.path for m in scan.manifests) == ("pyproject.toml", "requirements.txt")


def test_scan_manifests_records_a_corrupt_but_readable_manifest_as_unreadable(
    tmp_path: Path,
) -> None:
    """A manifest that reads fine as bytes but whose TOML/JSON does not
    decode must not take the same branch as one that simply does not
    declare the dependency -- that collapse is exactly what let a corrupt
    `pyproject.toml` read as "this repository does not use pydantic"
    (CLAUDE.md rule 1's failure mode). `requirements.txt` alongside it still
    parses fine and still declares pydantic, so this also checks the
    corruption of one manifest does not take the whole scan down with it.
    """
    root = build_sample_repo(tmp_path)
    (root / "pyproject.toml").write_text("[project\nname = 'broken'", encoding="utf-8")

    scan = scan_manifests(Workspace(root), canonical_name="pydantic")

    assert scan.unreadable == ("pyproject.toml",)
    assert {d.manifest.path for d in scan.declarations} == {"requirements.txt"}
    corrupt = next(m for m in scan.manifests if m.path == "pyproject.toml")
    assert corrupt.declared_specifier is None


def test_scan_manifests_does_not_mark_a_merely_irrelevant_manifest_as_unreadable(
    tmp_path: Path,
) -> None:
    """The other direction: a manifest that parses cleanly but does not
    declare the queried dependency must NOT land in `unreadable`. Without
    this, fixing the corrupt-manifest case above by marking every "no
    declaration" result as unreadable would replace one false claim
    ("this is fine" when it is corrupt) with another ("this is corrupt"
    when it plainly is not)."""
    root = build_sample_repo(tmp_path)

    scan = scan_manifests(Workspace(root), canonical_name="some-package-nobody-declares")

    assert scan.unreadable == ()
    assert scan.declarations == ()
    assert tuple(m.path for m in scan.manifests) == ("pyproject.toml", "requirements.txt")


def test_scan_manifests_records_a_manifest_that_does_not_decode_as_unreadable(
    tmp_path: Path,
) -> None:
    """The sibling branch to the corrupt-TOML case above, and a genuinely
    different one: this manifest never becomes text at all.

    `_parse` is reached only once `workspace.read_text` has returned a
    string. A manifest whose bytes are not UTF-8 -- a `pyproject.toml`
    saved as latin-1, the ordinary way this happens -- raises
    `UnicodeDecodeError` out of `read_text` and never reaches the TOML
    decoder, so the `_Unparseable` branch the test above pins cannot
    speak for it. Without its own branch the exception would either
    escape and kill the analysis, or be swallowed into "no declaration
    here", which is the specific false claim `unreadable` exists to
    prevent: a repository that pins pydantic reading as one that does not
    mention it.

    `unreadable` feeds `analyze_repository`'s confidence reducer, so a
    silent failure here does not merely lose a declaration -- it removes
    the reader's only signal that anything was lost.
    """
    root = build_sample_repo(tmp_path)
    (root / "pyproject.toml").write_bytes(
        "[project]\nname = 'caf\N{LATIN SMALL LETTER E WITH ACUTE}'\n".encode("latin-1")
    )

    scan = scan_manifests(Workspace(root), canonical_name="pydantic")

    assert scan.unreadable == ("pyproject.toml",)
    assert "pyproject.toml" in {m.path for m in scan.manifests}, (
        "an unreadable manifest must still be listed -- otherwise it vanishes entirely"
    )
    assert {d.manifest.path for d in scan.declarations} == {"requirements.txt"}


def test_an_unreadable_manifest_becomes_a_confidence_reducer(tmp_path: Path) -> None:
    """End of the same channel: the undecodable manifest reaches the report.

    `test_scan_manifests_records_a_manifest_that_does_not_decode_as_unreadable`
    proves `scan.unreadable` is populated; this proves the analyzer spends
    it. The two together are what stop an unread manifest from being a fact
    known to the scanner and invisible to the reader.
    """
    root = build_sample_repo(tmp_path)
    (root / "pyproject.toml").write_bytes(
        "[project]\nname = 'caf\N{LATIN SMALL LETTER E WITH ACUTE}'\n".encode("latin-1")
    )
    spec = DependencySpec(name="pydantic", current_version="1.10.13", target_version="2.9.0")

    analysis = analyze_repository(Workspace(root), spec)

    assert any("pyproject.toml" in reducer for reducer in analysis.confidence_reducers), (
        analysis.confidence_reducers
    )
