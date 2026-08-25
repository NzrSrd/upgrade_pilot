"""Per-path commit churn, aggregated from `Workspace.git_log`.

This module never calls `git` itself -- `ChurnIndex.from_records` takes the
`CommitRecord` tuple `Workspace.git_log` already produced, so it is a pure
function over data, testable without a real repository at all (see
`test_a_path_in_no_commit_returns_None_while_history_is_still_available` and
`test_no_records_means_history_was_not_available`).

The three states `AffectedFile.commit_count` documents in `models/repo.py`
begin here: `available` distinguishes "history was read" from "no history
was read", and `for_path` returning None within an available index is the
"history was read; this file was simply not touched" state. Task 9 is the
consumer that turns those two into `None` versus `0`.
"""

from __future__ import annotations

from typing import Self

from pydantic import AwareDatetime, Field

from upgradepilot.models.base import HonestModel
from upgradepilot.models.evidence import RepoRelativePath
from upgradepilot.models.repo import CommitRecord


class ChurnEntry(HonestModel):
    """One path's aggregate churn within the commits `from_records` saw."""

    path: RepoRelativePath
    commit_count: int = Field(ge=1)
    """At least 1: an entry is only ever created because some commit's
    `files` named this path. A zero-count entry would be a record with no
    commit behind it -- there is no such thing as "zero commits, but here is
    an entry anyway"; that case is simply the absence of an entry, which
    `ChurnIndex.for_path` reports as None."""
    last_modified: AwareDatetime
    """The newest timestamp among the commits that touched this path."""


class ChurnIndex(HonestModel):
    """Commit counts and last-modified times, indexed by repo-relative path."""

    entries: tuple[ChurnEntry, ...] = ()
    available: bool
    """Whether git history was read at all.

    `Workspace.git_log` returns `[]` for two different reasons: no `.git`
    directory, and a real, usable repository that simply has no commits yet.
    Neither is churn data. `available` collapses both into one signal
    deliberately -- both mean "we cannot say anything about how often a
    given file changes" -- rather than adding a second `Workspace` API to
    tell them apart, which no caller of this module needs to.

    Set from `bool(records)` at construction, not derived from `entries`:
    a batch of commits whose `files` all happen to be empty tuples would
    leave `entries` empty too, but history genuinely WAS read, so
    `available` must still be True. Records, not entries, are the source of
    truth for this field.
    """

    def for_path(self, path: str) -> ChurnEntry | None:
        """This path's churn entry, or None if no commit in this index
        touched it.

        None is ambiguous only if you ignore `available`: within an
        available index it means "not touched" (a real, low-churn signal);
        when `available` is False no path has an entry, because there was
        no history to build one from.
        """
        for entry in self.entries:
            if entry.path == path:
                return entry
        return None

    @classmethod
    def from_records(cls, records: tuple[CommitRecord, ...]) -> Self:
        """Group `records` by the paths in `CommitRecord.files`, counting
        commits per path and keeping the newest `timestamp`."""
        grouped: dict[str, list[CommitRecord]] = {}
        for record in records:
            for path in record.files:
                grouped.setdefault(path, []).append(record)

        entries = tuple(
            sorted(
                (
                    ChurnEntry(
                        path=path,
                        commit_count=len(path_records),
                        last_modified=max(r.timestamp for r in path_records),
                    )
                    for path, path_records in grouped.items()
                ),
                key=lambda entry: entry.path,
            )
        )
        return cls(entries=entries, available=bool(records))
