"""The phase's exit criterion, executable.

Spec: "given the fixture repository and `pydantic`, the analyzer returns
structured evidence with real file/line usage sites and honest confidence
labels."

Every assertion here is an EQUALITY against a constant in `repo_builder.py`.
The fixture tests in `tests/unit/test_fixture_repo.py` bind those constants
one way -- everything listed must exist. These bind the other way: nothing
unlisted may appear, and nothing listed may go missing. Together they mean
shortening a tuple turns a test red instead of quietly narrowing what this
project claims to detect.
"""

from pathlib import Path

from tests.fixtures.repo_builder import (
    EXPECTED_DECLARED_SPECIFIER,
    EXPECTED_HIGH_CONFIDENCE_SYMBOLS,
    EXPECTED_LOW_CONFIDENCE_SITE,
    EXPECTED_MEDIUM_CONFIDENCE_SYMBOLS,
    EXPECTED_PINNED_VERSION,
    build_sample_repo,
)
from upgradepilot.models.enums import Confidence, DependencyRole, UsageKind, VersionConfidence
from upgradepilot.models.inputs import DependencySpec
from upgradepilot.models.repo import RepoAnalysis
from upgradepilot.services.analysis.analyzer import analyze_repository
from upgradepilot.services.analysis.layout import corresponding_test_paths
from upgradepilot.services.repo.workspace import Workspace


def _analysis(tmp_path: Path) -> RepoAnalysis:
    workspace = Workspace(build_sample_repo(tmp_path))
    spec = DependencySpec(name="pydantic", current_version="1.10.13", target_version="2.9.0")
    return analyze_repository(workspace, spec)


def test_high_confidence_symbols_equal_the_documented_set(tmp_path: Path) -> None:
    analysis = _analysis(tmp_path)
    assert analysis.symbol_inventory.high_confidence_symbols() == EXPECTED_HIGH_CONFIDENCE_SYMBOLS


def test_medium_confidence_symbols_equal_the_documented_set(tmp_path: Path) -> None:
    """Deviation 1's acceptance test. Under spec 7.1's literal module-level
    rule these four would be LOW, because `service.py` imports `app.models`
    rather than `pydantic` and defines no models. They are MEDIUM because the
    receiver of each call resolves to an indexed model."""
    inventory = _analysis(tmp_path).symbol_inventory
    medium = tuple(sorted(s.symbol for s in inventory.entries if s.confidence is Confidence.MEDIUM))
    assert medium == EXPECTED_MEDIUM_CONFIDENCE_SYMBOLS


def test_the_low_confidence_site_is_where_the_fixture_says_it_is(tmp_path: Path) -> None:
    path, symbol = EXPECTED_LOW_CONFIDENCE_SITE
    analysis = _analysis(tmp_path)
    affected = next(a for a in analysis.affected_files if a.path == path)
    low = [
        s
        for s in affected.usage_sites
        if s.confidence is Confidence.LOW and s.kind is UsageKind.METHOD_CALL
    ]
    assert [s.symbol for s in low] == [symbol]


def test_util_py_is_never_reported(tmp_path: Path) -> None:
    """The fixture's deliberate false-positive trap. `util.py` defines a
    `dict()` method on a plain class and calls it, with no model library
    anywhere in scope. Reporting it would be a fabricated finding -- the
    thing CLAUDE.md rule 1 exists to prevent -- and it is the one file whose
    ABSENCE from the output is the assertion."""
    analysis = _analysis(tmp_path)
    assert "src/app/util.py" not in {a.path for a in analysis.affected_files}
    assert "src/app/util.py" not in {s.path for s in analysis.skipped_files}


def test_the_detected_version_equals_the_documented_pin(tmp_path: Path) -> None:
    detected = _analysis(tmp_path).detected_version
    assert detected is not None
    assert detected.value == EXPECTED_PINNED_VERSION
    assert detected.confidence is VersionConfidence.EXACT
    assert detected.role is DependencyRole.DIRECT
    assert detected.source_manifest.path == "requirements.txt"


def test_the_declared_specifier_survives_into_the_manifest_record(tmp_path: Path) -> None:
    analysis = _analysis(tmp_path)
    pyproject = next(m for m in analysis.manifests if m.path == "pyproject.toml")
    assert pyproject.declared_specifier == EXPECTED_DECLARED_SPECIFIER


def test_the_affected_file_set_equals_the_documented_set(tmp_path: Path) -> None:
    """Equality on the file set, not containment. A future change that makes
    the analyzer report every Python file would satisfy every other test
    here."""
    analysis = _analysis(tmp_path)
    assert tuple(a.path for a in analysis.affected_files) == (
        "src/app/consumer.py",
        "src/app/models.py",
        "src/app/service.py",
    )


def test_the_test_file_is_marked_as_a_test(tmp_path: Path) -> None:
    analysis = _analysis(tmp_path)
    assert analysis.test_paths == ("tests/test_models.py",)
    assert corresponding_test_paths("src/app/models.py", analysis.test_paths) == (
        "tests/test_models.py",
    )


def test_every_citation_in_the_analysis_resolves(tmp_path: Path) -> None:
    """The product's central promise, asserted over the whole output at once:
    every file exists, every line is in range, and every snippet is the line
    it claims to quote."""
    root = build_sample_repo(tmp_path)
    spec = DependencySpec(name="pydantic", current_version="1.10.13", target_version="2.9.0")
    analysis = analyze_repository(Workspace(root), spec)

    cited = 0
    for affected in analysis.affected_files:
        lines = (root / affected.path).read_text(encoding="utf-8").splitlines()
        for site in affected.usage_sites:
            assert site.file == affected.path
            assert 1 <= site.line <= len(lines)
            assert site.snippet == lines[site.line - 1]
            cited += 1
    assert cited >= 8, "too few citations for this assertion to be discriminating"


def test_commit_sha_propagates_from_the_workspace(tmp_path: Path) -> None:
    """`Workspace(root)` built directly always has `commit_sha=None` -- the
    sha is a constructor parameter supplied by the clone and local-path
    factories, never derived from the tree. Every other test in this module
    builds the workspace with no sha, so this is the only place
    `RepoAnalysis.commit_sha` is exercised in its non-None state -- proving
    the analyzer actually reads `workspace.commit_sha` rather than passing a
    literal `None` through. The commit sha is part of the citation chain:
    "these findings are at these lines" is only checkable against a stated
    revision.
    """
    root = build_sample_repo(tmp_path)
    workspace = Workspace(root, commit_sha="abc1234")
    spec = DependencySpec(name="pydantic", current_version="1.10.13", target_version="2.9.0")
    analysis = analyze_repository(workspace, spec)
    assert analysis.commit_sha == "abc1234"
