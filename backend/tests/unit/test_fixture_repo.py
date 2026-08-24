"""Guards the fixture repository's shape.

Phase 2's analyzer tests assert exact counts against this tree. If someone
edits a fixture file without updating the expectations, this fails here
rather than producing a confusing analyzer failure later.
"""

import ast
from pathlib import Path

from tests.fixtures.repo_builder import (
    EXPECTED_PINNED_VERSION,
    EXPECTED_PYTHON_FILES,
    EXPECTED_UNPARSEABLE,
    build_sample_repo,
)
from upgradepilot.services.repo.workspace import Workspace


def test_build_produces_the_expected_python_files(tmp_path: Path) -> None:
    root = build_sample_repo(tmp_path)
    files = sorted(str(p) for p in Workspace(root=root).iter_files(".py"))

    assert len(files) == EXPECTED_PYTHON_FILES
    assert "src/app/models.py" in files
    assert "src/app/util.py" in files
    assert EXPECTED_UNPARSEABLE in files
    assert not any(f.endswith(".py.txt") for f in files)


def test_the_broken_file_is_genuinely_unparseable(tmp_path: Path) -> None:
    root = build_sample_repo(tmp_path)
    source = Workspace(root=root).read_text(Path(EXPECTED_UNPARSEABLE))

    try:
        ast.parse(source)
    except SyntaxError:
        return
    raise AssertionError("fixture broken.py must not parse")


def test_every_other_file_parses(tmp_path: Path) -> None:
    root = build_sample_repo(tmp_path)
    workspace = Workspace(root=root)

    for relative in workspace.iter_files(".py"):
        if str(relative) == EXPECTED_UNPARSEABLE:
            continue
        ast.parse(workspace.read_text(relative))


def test_manifests_declare_a_pydantic_v1_dependency(tmp_path: Path) -> None:
    root = build_sample_repo(tmp_path)

    assert "pydantic" in (root / "pyproject.toml").read_text()
    assert EXPECTED_PINNED_VERSION in (root / "requirements.txt").read_text()


def test_history_has_two_commits_and_recent_churn_on_models(tmp_path: Path) -> None:
    root = build_sample_repo(tmp_path)
    commits = Workspace(root=root).git_log(limit=10)

    assert len(commits) == 2
    assert commits[0].files == ("src/app/models.py",)


def test_util_module_does_not_import_pydantic(tmp_path: Path) -> None:
    """The low-confidence trap: a .dict() call with no pydantic in scope."""
    root = build_sample_repo(tmp_path)
    source = Workspace(root=root).read_text(Path("src/app/util.py"))

    assert "pydantic" not in source
    assert ".dict()" in source


def test_builds_are_independent(tmp_path: Path) -> None:
    first = build_sample_repo(tmp_path / "a")
    second = build_sample_repo(tmp_path / "b")

    (first / "src" / "app" / "models.py").write_text("mutated = True\n")
    assert "mutated" not in (second / "src" / "app" / "models.py").read_text()
