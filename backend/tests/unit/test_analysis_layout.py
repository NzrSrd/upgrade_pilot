"""Tests for `is_test_path`, `corresponding_test_paths`, and `language_shares`."""

import math
from pathlib import Path

import pytest

from tests.fixtures.repo_builder import build_sample_repo
from upgradepilot.models.errors import UpgradePilotError
from upgradepilot.models.repo import LanguageShare
from upgradepilot.services.analysis import layout
from upgradepilot.services.analysis.layout import (
    _require_shares_total_one,
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


# -- F9: the "never round" invariant, and what happens if it is broken ------


def test_three_equally_common_languages_total_exactly_one(tmp_path: Path) -> None:
    """The invariant `layout.py` argues at length for and nothing bound.
    Mutation L4 (`round(count / total, 2)`) survived the whole suite: the
    sample repo's own distribution rounds to exactly 1.00 by luck
    (0.64 + 0.18 + 0.09 + 0.09), so the existing shares-total-one test
    cannot fail on it.

    Three recognised languages with one file each is the shape that can:
    0.333... rounds to 0.33 and three of those total 0.99, which
    `RepoAnalysis`'s validator rejects. Equal counts also make the fixture
    immune to which language the table happens to name first.
    """
    root = tmp_path / "thirds"
    root.mkdir()
    (root / "a.py").write_text("x = 1\n", encoding="utf-8")
    (root / "b.md").write_text("# b\n", encoding="utf-8")
    (root / "c.ts").write_text("export const c = 1\n", encoding="utf-8")

    shares = language_shares(Workspace(root))
    assert len(shares) == 3
    assert {s.file_count for s in shares} == {1}
    assert math.fsum(s.share for s in shares) == 1.0


def test_shares_that_do_not_partition_one_are_a_recorded_error(tmp_path: Path) -> None:
    """No input can reach this today -- raw quotients always partition 1.0 --
    so it guards a future edit rather than a repository. What it changes is
    the FAILURE MODE: rounding used to surface as `RepoAnalysis`'s validator
    raising "language shares must total 1.0, got 0.99" at assembly time, far
    from the function that produced them, or as a bare pydantic
    `ValidationError` on `share=0.0`. Both are technical exceptions with no
    user-facing message. An `UpgradePilotError` is what CLAUDE.md rule 20
    means by a recorded outcome: Phase 4's node turns it into an `AppError`
    with a comprehensible `message` and the arithmetic in `detail`.
    """
    rounded = tuple(
        LanguageShare(language=language, share=0.33, file_count=1)
        for language in ("Python", "Markdown", "TypeScript")
    )
    with pytest.raises(UpgradePilotError) as caught:
        _require_shares_total_one(rounded)
    assert "language" in caught.value.message.lower()
    assert caught.value.detail is not None
    assert "0.99" in caught.value.detail


def test_shares_that_do_partition_one_pass_through_unchanged() -> None:
    """The negative direction. Without it, a guard that rejected everything
    would satisfy the test above -- and would take every ordinary repository
    down with it."""
    exact = tuple(
        LanguageShare(language=language, share=0.5, file_count=1)
        for language in ("Python", "Markdown")
    )
    assert _require_shares_total_one(exact) == exact


def test_language_shares_routes_its_result_through_the_invariant_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A structural assertion, deliberately, and the reason is worth stating:
    no repository input can make the guard fire, so its INVOCATION cannot be
    bound by any black-box test. Verified by mutation -- deleting the call
    and returning the sorted tuple directly leaves all 727 tests green, which
    is precisely the "the test asserts a result some other mechanism already
    forces" pattern this branch keeps producing.

    So this binds the wiring instead: `language_shares` must hand its result
    to the guard and return what the guard returns. Under a future rounding
    edit that is the difference between an `UpgradePilotError` naming the
    arithmetic and `RepoAnalysis`'s validator raising several steps away.
    """
    seen: list[tuple[LanguageShare, ...]] = []
    real = layout._require_shares_total_one

    def spy(shares: tuple[LanguageShare, ...]) -> tuple[LanguageShare, ...]:
        seen.append(shares)
        return real(shares)

    monkeypatch.setattr(layout, "_require_shares_total_one", spy)

    root = tmp_path / "wired"
    root.mkdir()
    (root / "a.py").write_text("x = 1\n", encoding="utf-8")
    (root / "b.md").write_text("# b\n", encoding="utf-8")

    result = language_shares(Workspace(root))
    assert seen == [result]
