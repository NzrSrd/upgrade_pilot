"""Guards the fixture repository's shape.

Phase 2's analyzer tests assert exact counts against this tree. If someone
edits a fixture file without updating the expectations, this fails here
rather than producing a confusing analyzer failure later.
"""

import ast
import tomllib
from pathlib import Path

from tests.fixtures.repo_builder import (
    EXPECTED_DECLARED_SPECIFIER,
    EXPECTED_HIGH_CONFIDENCE_SYMBOLS,
    EXPECTED_MEDIUM_CONFIDENCE_SYMBOLS,
    EXPECTED_PINNED_VERSION,
    EXPECTED_PYTHON_FILES,
    EXPECTED_UNPARSEABLE,
    build_sample_repo,
)
from upgradepilot.services.repo.workspace import Workspace

_MODEL_IMPORT_PREFIXES = ("pydantic", "app.models")
"""A module has a model type in scope if it imports from one of these.

This is what excludes `src/app/util.py` -- the deliberate low-confidence
trap, which calls `.dict()` with no model library anywhere in scope -- from
the medium-confidence assertion below, without that assertion naming the
trap's filename. If the trap were included, gutting `service.py` of every
real model method call would still leave `dict` "found" via the trap and the
assertion would quietly stop binding.
"""


def _built_modules(root: Path) -> list[ast.Module]:
    """Parse every parseable Python file in the *built* tree.

    Built, not `SAMPLE_REPO_DIR`: `build_sample_repo`'s copy-and-rename step
    is part of what these assertions have to cover. The deliberately
    unparseable file is skipped -- `test_the_broken_file_is_genuinely_unparseable`
    owns it.
    """
    workspace = Workspace(root=root)
    return [
        ast.parse(workspace.read_text(relative))
        for relative in workspace.iter_files(".py")
        if str(relative) != EXPECTED_UNPARSEABLE
    ]


def _names_used(tree: ast.Module) -> set[str]:
    """Every name a module declares, imports, references or accesses.

    An AST walk rather than a substring search, on purpose: `models.py`
    carries v2-migration comments mentioning `ConfigDict` and
    `model_config`, so a substring search for "Config" would still succeed
    against a `models.py` with every real `class Config` deleted. Only names
    that are genuinely code count as the fixture containing the idiom.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            names.add(node.name)
        elif isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.alias):
            names.add(node.asname or node.name.rpartition(".")[2])
    return names


def _imports_a_model_type(tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module and node.module.startswith(_MODEL_IMPORT_PREFIXES):
                return True
        elif isinstance(node, ast.Import) and any(
            alias.name.startswith(_MODEL_IMPORT_PREFIXES) for alias in node.names
        ):
            return True
    return False


def _methods_called(tree: ast.Module) -> set[str]:
    """Attribute names invoked as calls, e.g. `dict` from `invoice.dict()`."""
    called: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            called.add(node.func.attr)
    return called


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


def test_expected_high_confidence_symbols_are_present_in_the_built_tree(tmp_path: Path) -> None:
    """Binds EXPECTED_HIGH_CONFIDENCE_SYMBOLS to the tree it describes.

    Phase 2's analyzer tests will trust this constant blindly, as the
    documented claim that this fixture contains the Pydantic v1 idioms the
    analyzer is supposed to find. Nothing kept it true before: deleting
    `Config` and `validator` from `models.py` outright left every fixture
    test green, which would have silently invalidated Phase 2's entire test
    basis. The tuple is iterated rather than restated here, so adding a
    symbol to it cannot pass unless the fixture actually gains that symbol.
    """
    declared: set[str] = set()
    for tree in _built_modules(build_sample_repo(tmp_path)):
        declared |= _names_used(tree)

    assert EXPECTED_HIGH_CONFIDENCE_SYMBOLS, "an empty expectation would assert nothing"
    for symbol in EXPECTED_HIGH_CONFIDENCE_SYMBOLS:
        assert symbol in declared, (
            f"{symbol!r} is documented as a high-confidence idiom in this "
            f"fixture but no built module uses that name"
        )


def test_expected_medium_confidence_symbols_are_called_on_models(tmp_path: Path) -> None:
    """Binds EXPECTED_MEDIUM_CONFIDENCE_SYMBOLS to the tree it describes.

    Same defect as the high-confidence case: gutting `service.py` of
    `.dict()`, `.copy()`, `.parse_obj()` and `.schema()` left every fixture
    test green. These are graded medium precisely because the names are
    generic, so the assertion looks for them as *calls* in modules that have
    a model type in scope -- not as bare substrings anywhere in the tree,
    which `util.py`'s deliberate `.dict()` trap would satisfy on its own.
    """
    called: set[str] = set()
    for tree in _built_modules(build_sample_repo(tmp_path)):
        if _imports_a_model_type(tree):
            called |= _methods_called(tree)

    assert EXPECTED_MEDIUM_CONFIDENCE_SYMBOLS, "an empty expectation would assert nothing"
    for symbol in EXPECTED_MEDIUM_CONFIDENCE_SYMBOLS:
        assert symbol in called, (
            f"{symbol!r} is documented as a medium-confidence idiom in this "
            f"fixture but is never called in a module with a model in scope"
        )


def test_expected_declared_specifier_matches_the_built_manifest(tmp_path: Path) -> None:
    """Binds EXPECTED_DECLARED_SPECIFIER to the built pyproject.toml.

    Compared for equality against the parsed requirement, not searched for
    as a substring: the constant is the fixture's claim about which pydantic
    range this project declares, and `>=2.0` must not be able to sit in the
    manifest while the constant still says `>=1.10,<2`. The `<2` bound is
    load-bearing -- it is what makes this fixture a v1 project at all.
    """
    root = build_sample_repo(tmp_path)
    manifest = tomllib.loads((root / "pyproject.toml").read_text())
    requirements = [
        requirement
        for requirement in manifest["project"]["dependencies"]
        if requirement.startswith("pydantic")
    ]

    assert len(requirements) == 1, f"expected exactly one pydantic requirement, got {requirements}"
    assert requirements[0].removeprefix("pydantic") == EXPECTED_DECLARED_SPECIFIER


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
