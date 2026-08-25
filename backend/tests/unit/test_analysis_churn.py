"""Tests for `ChurnIndex`: per-path commit counts and the three churn states
`AffectedFile.commit_count` documents in `models/repo.py`.
"""

from datetime import UTC, datetime
from pathlib import Path

from tests.fixtures.repo_builder import build_sample_repo
from upgradepilot.models.repo import CommitRecord
from upgradepilot.services.analysis.churn import ChurnIndex
from upgradepilot.services.repo.workspace import Workspace


def test_churn_counts_commits_per_path_and_keeps_the_newest_timestamp() -> None:
    records = (
        CommitRecord(
            sha="bbbbbbb",
            timestamp=datetime(2026, 8, 2, tzinfo=UTC),
            files=("src/app/models.py",),
        ),
        CommitRecord(
            sha="aaaaaaa",
            timestamp=datetime(2026, 8, 1, tzinfo=UTC),
            files=("src/app/models.py", "README.md"),
        ),
    )
    index = ChurnIndex.from_records(records)
    entry = index.for_path("src/app/models.py")
    assert entry is not None
    assert entry.commit_count == 2
    assert entry.last_modified == datetime(2026, 8, 2, tzinfo=UTC)
    readme_entry = index.for_path("README.md")
    assert readme_entry is not None
    assert readme_entry.commit_count == 1


def test_a_path_in_no_commit_returns_None_while_history_is_still_available() -> None:
    """The distinction Task 1 built `commit_count: int | None` for. History
    WAS read; this file simply was not touched. Task 9 turns this into
    `commit_count=0`, a real low-churn signal -- NOT into None."""
    index = ChurnIndex.from_records(
        (
            CommitRecord(
                sha="aaaaaaa",
                timestamp=datetime(2026, 8, 1, tzinfo=UTC),
                files=("other.py",),
            ),
        )
    )
    assert index.available is True
    assert index.for_path("src/app/models.py") is None


def test_available_is_true_even_when_the_only_commit_touched_no_files() -> None:
    """`available` must reflect that history WAS read, not whether any
    entries happened to result from it. A record with an empty `files`
    tuple (e.g. an empty commit) produces zero entries -- if `available`
    were derived from `entries` rather than from `records` directly, this
    case would be indistinguishable from `test_no_records_means_history_
    was_not_available` below, even though a commit genuinely was read."""
    index = ChurnIndex.from_records(
        (CommitRecord(sha="aaaaaaa", timestamp=datetime(2026, 8, 1, tzinfo=UTC), files=()),)
    )
    assert index.available is True
    assert index.for_path("anything.py") is None


def test_no_records_means_history_was_not_available() -> None:
    """`Workspace.git_log` returns [] both for "no .git directory" and for "a
    real repository with no commits yet". Neither is churn data, and Task 9
    must report `commit_count=None` -- unknown -- rather than zero.

    `available` is derived from the records rather than from a new Workspace
    API, because both of git_log's empty cases mean the same thing here.
    """
    index = ChurnIndex.from_records(())
    assert index.available is False
    assert index.for_path("anything.py") is None


def test_churn_over_the_sample_repo_sees_the_second_commit(tmp_path: Path) -> None:
    """`build_sample_repo` makes two commits on purpose: the second touches
    only `models.py`. This binds that intent to a real assertion."""
    workspace = Workspace(build_sample_repo(tmp_path))
    index = ChurnIndex.from_records(tuple(workspace.git_log(limit=100)))
    assert index.available is True
    models_entry = index.for_path("src/app/models.py")
    assert models_entry is not None
    assert models_entry.commit_count == 2
    util_entry = index.for_path("src/app/util.py")
    assert util_entry is not None
    assert util_entry.commit_count == 1
