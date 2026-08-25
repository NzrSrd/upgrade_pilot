"""Tests for `is_test_path`, `corresponding_test_paths`, and `language_shares`."""

import math
from pathlib import Path

import pytest

from tests.fixtures.repo_builder import build_sample_repo
from upgradepilot.services.analysis.layout import (
    corresponding_test_paths,
    is_test_path,
    language_shares,
)
from upgradepilot.services.repo.workspace import Workspace


@pytest.mark.parametrize(
    "path",
    [
        "tests/test_models.py",
        "tests/unit/test_x.py",
        "src/app/test_thing.py",
        "src/app/thing_test.py",
        "test/test_a.py",
        "tests/models.py",
    ],
)
def test_test_paths_are_recognised(path: str) -> None:
    """`tests/models.py` is the case only the directory-segment branch can
    classify: it sits inside a `tests/` directory but its filename matches
    neither the `test_` prefix nor the `_test.py` suffix convention. Every
    other case above already satisfies the filename check on its own, so
    without this one the directory branch could be deleted entirely and the
    suite would not notice (Ruling 53)."""
    assert is_test_path(path) is True


@pytest.mark.parametrize(
    "path",
    [
        "src/app/models.py",
        "src/app/latest.py",
        "src/contest.py",
        "src/protest_utils.py",
        "attest/main.py",
    ],
)
def test_ordinary_paths_are_not_mistaken_for_tests(path: str) -> None:
    """`latest.py`, `contest.py`, `protest_utils.py` and `attest/` all contain
    "test" as a substring. Matching on substring rather than on the path
    convention marks ordinary source as test coverage, which inflates
    `test_coverage_of_affected` -- a factor that LOWERS risk. A false
    positive here makes a risky upgrade read as safe."""
    assert is_test_path(path) is False


def test_a_source_file_finds_its_conventional_test() -> None:
    tests = ("tests/test_models.py", "tests/test_service.py")
    assert corresponding_test_paths("src/app/models.py", tests) == ("tests/test_models.py",)


def test_a_source_file_with_no_test_finds_nothing() -> None:
    assert corresponding_test_paths("src/app/util.py", ("tests/test_models.py",)) == ()


def test_a_near_miss_filename_is_not_mistaken_for_the_conventional_test() -> None:
    """`tests/test_models_extra.py` contains "models" as a substring of its
    stem, but it is not `test_models.py` or `models_test.py` -- the only two
    filenames the convention recognises. Swapping the exact-filename check
    for a substring check passes every other test here and still returns
    this near miss, which is exactly the false positive that would let an
    unrelated test file stand in for real coverage (Ruling 54)."""
    assert corresponding_test_paths("src/app/models.py", ("tests/test_models_extra.py",)) == ()


def test_language_shares_total_one_and_are_sorted_by_descending_share(tmp_path: Path) -> None:
    root = build_sample_repo(tmp_path)
    (root / "static").mkdir()
    (root / "static" / "app.ts").write_text("export const x = 1\n", encoding="utf-8")
    (root / "README.md").write_text("# hi\n", encoding="utf-8")

    shares = language_shares(Workspace(root))
    assert math.isclose(math.fsum(s.share for s in shares), 1.0, abs_tol=1e-6)
    assert [s.share for s in shares] == sorted((s.share for s in shares), reverse=True)
    assert shares[0].language == "Python"


def test_language_shares_are_empty_for_a_repository_with_no_recognised_files(
    tmp_path: Path,
) -> None:
    """RepoAnalysis's validator requires the shares to total 1.0 when the
    tuple is non-empty. An empty tuple is the only honest answer here, and it
    must not be a tuple of zeros -- LanguageShare.share is gt=0.0."""
    root = tmp_path / "opaque"
    (root / "data").mkdir(parents=True)
    (root / "data" / "blob.bin").write_bytes(b"\x00\x01")
    assert language_shares(Workspace(root)) == ()
