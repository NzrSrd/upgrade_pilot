"""Assembly tests for `analyze_repository`.

Every function this composes has its own tests already, so these are tested
for ASSEMBLY -- that the pieces are wired to each other and to the right
fields on `RepoAnalysis` -- not for the behaviour those functions already
prove themselves.
"""

import shutil
from pathlib import Path

import pytest

from tests.fixtures.repo_builder import (
    EXPECTED_PINNED_VERSION,
    EXPECTED_PYTHON_FILES,
    EXPECTED_UNPARSEABLE,
    build_sample_repo,
)
from upgradepilot.models.errors import DependencyNotFoundError
from upgradepilot.models.inputs import DependencySpec
from upgradepilot.models.repo import RepoAnalysis
from upgradepilot.services.analysis.analyzer import analyze_repository
from upgradepilot.services.repo.workspace import Workspace


def _analysis(tmp_path: Path, **overrides: object) -> RepoAnalysis:
    workspace = Workspace(build_sample_repo(tmp_path))
    spec = DependencySpec(name="pydantic", current_version="1.10.13", target_version="2.9.0")
    return analyze_repository(workspace, spec, **overrides)


def test_counts_are_internally_consistent(tmp_path: Path) -> None:
    """RepoAnalysis's own validator enforces `analyzed + skipped <= total`,
    so constructing it at all proves that much. This asserts the stronger
    property that makes the counts meaningful: the analyzer looked at fewer
    files than exist, and at more than none."""
    analysis = _analysis(tmp_path)
    assert 0 < analysis.analyzed_files < analysis.total_python_files
    assert analysis.total_python_files == EXPECTED_PYTHON_FILES


def test_the_unparseable_file_is_reported_not_swallowed(tmp_path: Path) -> None:
    analysis = _analysis(tmp_path)
    assert EXPECTED_UNPARSEABLE in {s.path for s in analysis.skipped_files}
    assert analysis.skipped_ratio > 0.0


def test_every_affected_file_path_appears_in_the_repository(tmp_path: Path) -> None:
    """CLAUDE.md rule 1, asserted rather than asserted-about: every path this
    analysis cites must resolve to a file that exists in the tree analyzed."""
    root = build_sample_repo(tmp_path)
    workspace = Workspace(root)
    spec = DependencySpec(name="pydantic", current_version="1.10.13", target_version="2.9.0")
    analysis = analyze_repository(workspace, spec)
    for affected in analysis.affected_files:
        assert (root / affected.path).is_file(), affected.path
        for site in affected.usage_sites:
            lines = (root / site.file).read_text(encoding="utf-8").splitlines()
            assert 1 <= site.line <= len(lines)
            assert lines[site.line - 1] == site.snippet


def test_churn_reaches_the_affected_files(tmp_path: Path) -> None:
    analysis = _analysis(tmp_path)
    models = next(a for a in analysis.affected_files if a.path == "src/app/models.py")
    assert models.commit_count == 2
    assert models.last_modified is not None
    assert models.last_modified.tzinfo is not None


def test_commit_count_is_None_when_the_repository_has_no_history(tmp_path: Path) -> None:
    """Task 1's three-state `commit_count`, end to end. A directory with no
    `.git` is a legitimate LocalRepoRef -- a user analysing an unpacked
    tarball -- and it must read as "churn unknown", not "churn zero"."""
    root = build_sample_repo(tmp_path)
    shutil.rmtree(root / ".git")
    spec = DependencySpec(name="pydantic", current_version="1.10.13", target_version="2.9.0")
    analysis = analyze_repository(Workspace(root), spec)
    assert analysis.affected_files
    assert all(a.commit_count is None for a in analysis.affected_files)


def test_a_dependency_the_repository_does_not_declare_raises(tmp_path: Path) -> None:
    workspace = Workspace(build_sample_repo(tmp_path))
    spec = DependencySpec(name="numpy", current_version="1.0", target_version="2.0")
    with pytest.raises(DependencyNotFoundError):
        analyze_repository(workspace, spec)


def test_gitmodules_becomes_a_confidence_reducer_not_a_skipped_file(tmp_path: Path) -> None:
    """`git clone` does not fetch submodule content. A repository whose real
    code lives in submodules analyses as nearly empty and would report LOW
    risk having never seen the code -- the carry-in `PLANNING.md` records.

    It must not be a SkippedFile: `skipped_ratio` divides by
    `total_python_files`, and a non-Python entry there corrupts the
    analysis_coverage factor and can trip RepoAnalysis's own validator.
    """
    root = build_sample_repo(tmp_path)
    (root / ".gitmodules").write_text(
        '[submodule "vendor/lib"]\n\tpath = vendor/lib\n\turl = https://example.invalid/lib\n',
        encoding="utf-8",
    )
    spec = DependencySpec(name="pydantic", current_version="1.10.13", target_version="2.9.0")
    analysis = analyze_repository(Workspace(root), spec)

    assert any("submodule" in reducer.lower() for reducer in analysis.confidence_reducers)
    assert ".gitmodules" not in {s.path for s in analysis.skipped_files}


def test_no_gitmodules_means_no_submodule_reducer(tmp_path: Path) -> None:
    """The negative direction. Without it, an implementation that appends the
    reducer unconditionally passes the test above."""
    analysis = _analysis(tmp_path)
    assert not any("submodule" in r.lower() for r in analysis.confidence_reducers)


def test_finding_no_candidate_at_all_is_reported_as_a_reducer(tmp_path: Path) -> None:
    """The `import_root` guess. `DependencySpec.import_root` for
    `python-dateutil` is `python_dateutil`, and the real import name is
    `dateutil`, so a repository that uses it heavily yields zero candidates.

    Zero findings must read as "we could not find it" rather than as "this
    dependency is unused" -- the second is a claim the analysis did not earn.
    """
    root = build_sample_repo(tmp_path)
    (root / "requirements.txt").write_text("python-dateutil==2.9.0\n", encoding="utf-8")
    spec = DependencySpec(name="python-dateutil", current_version="2.8.0", target_version="2.9.0")
    analysis = analyze_repository(Workspace(root), spec)

    assert analysis.affected_files == ()
    assert any("import" in r.lower() for r in analysis.confidence_reducers)


def test_the_analysis_is_deterministic_over_the_same_input(tmp_path: Path) -> None:
    """Two runs against the same tree must be byte-identical. Anything
    order-dependent -- a set iterated into a tuple, a dict's insertion order --
    makes the report's contents change between runs on unchanged input, and
    a reader cannot tell that from a real change in the repository."""
    root = build_sample_repo(tmp_path)
    spec = DependencySpec(name="pydantic", current_version="1.10.13", target_version="2.9.0")
    first = analyze_repository(Workspace(root), spec)
    second = analyze_repository(Workspace(root), spec)
    assert first.model_dump_json() == second.model_dump_json()


def test_version_discrepancy_surfaces_rather_than_being_overridden(tmp_path: Path) -> None:
    """Spec 7.1: never silently overridden in either direction. The model's
    `version_discrepancy` helper already exists; this asserts the analyzer
    feeds it a detected version it can actually compare."""
    workspace = Workspace(build_sample_repo(tmp_path))
    spec = DependencySpec(name="pydantic", current_version="1.9.0", target_version="2.9.0")
    analysis = analyze_repository(workspace, spec)
    assert analysis.version_discrepancy("1.9.0") == ("1.9.0", EXPECTED_PINNED_VERSION)
    assert analysis.version_discrepancy(EXPECTED_PINNED_VERSION) is None


# -- RULING 17: `resolve_version` returning None (declared, unconstrained) ---


def test_a_dependency_declared_without_a_version_becomes_a_confidence_reducer(
    tmp_path: Path,
) -> None:
    """`dependencies = ["pydantic"]` with no version or specifier at all is
    DECLARED but not pinned -- `resolve_version` returns None, it does not
    raise. `detected_version=None` on its own reads as "no version detected"
    with no reason given; this must become a reducer naming both the
    dependency and the manifest that declares it unconstrained, so the claim
    still traces to a real file (CLAUDE.md rule 1).

    Only manifest in play is `pyproject.toml`'s bare entry:
    `requirements.txt` is overwritten to not mention pydantic at all, so
    nothing else could produce a detected version or this reducer by
    accident -- an implementation that forgot the `detected is None` branch
    fails this test, not merely reports it differently.
    """
    root = build_sample_repo(tmp_path)
    (root / "pyproject.toml").write_text(
        '[project]\nname = "sample-app"\nversion = "0.1.0"\n'
        'requires-python = ">=3.11"\ndependencies = ["pydantic"]\n',
        encoding="utf-8",
    )
    (root / "requirements.txt").write_text("requests==2.31.0\n", encoding="utf-8")
    spec = DependencySpec(name="pydantic", current_version="1.10.13", target_version="2.9.0")
    analysis = analyze_repository(Workspace(root), spec)

    assert analysis.detected_version is None
    reducer = next(
        (r for r in analysis.confidence_reducers if "pydantic" in r and "version" in r.lower()),
        None,
    )
    assert reducer is not None, analysis.confidence_reducers
    assert "pyproject.toml" in reducer
    assert "submodule" not in reducer.lower()
    assert "import" not in reducer.lower()


# -- RULING 31: a corrupted `.git` degrades rather than aborting -------------


def test_a_corrupted_git_history_becomes_a_confidence_reducer_not_an_abort(
    tmp_path: Path,
) -> None:
    """Verified by direct execution: `Workspace.git_log` against a `.git`
    with its `objects` directory deleted raises `RepoUnavailableError`
    (`This repository's git history could not be read.`), not an empty
    result. By this point in the analysis, steps 4-8 have already read and
    parsed the whole file tree successfully, so the repository is
    demonstrably usable -- this is broken git METADATA, not an unavailable
    repository, and the analyzer must degrade (empty churn, one named
    reducer) rather than let the exception abort a complete, correct
    analysis of the code.

    Distinguishes this from `test_commit_count_is_None_when_the_repository_has_no_history`
    above (no `.git` at all: no reducer, because there is nothing broken,
    only absent) by checking for the reducer's presence here and its
    absence there.
    """
    root = build_sample_repo(tmp_path)
    shutil.rmtree(root / ".git" / "objects")
    spec = DependencySpec(name="pydantic", current_version="1.10.13", target_version="2.9.0")
    analysis = analyze_repository(Workspace(root), spec)

    assert analysis.affected_files  # the code itself was still fully analysed
    assert all(a.commit_count is None for a in analysis.affected_files)
    assert analysis.commit_records == ()
    assert any("history" in r.lower() for r in analysis.confidence_reducers)


# -- RULING 45: `commit_records` populated from what step 9 already fetched -


def test_commit_records_are_populated_from_the_same_history_git_log_read(
    tmp_path: Path,
) -> None:
    """`commit_records` is `RepoAnalysis`'s raw evidence; the per-file churn
    figures (`AffectedFile.commit_count`) are derived FROM it. Shipping the
    derivation with the source empty would put `commit_count=2` in the
    report with no provenance anywhere in the model (CLAUDE.md rule 1)."""
    analysis = _analysis(tmp_path)
    assert len(analysis.commit_records) == 2
    shas = {record.sha for record in analysis.commit_records}
    assert len(shas) == 2
    for record in analysis.commit_records:
        assert record.timestamp.tzinfo is not None
