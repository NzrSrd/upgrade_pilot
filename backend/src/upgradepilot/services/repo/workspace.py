"""The only view of a repository the analyzer ever gets.

Local checkouts and shallow clones both resolve to a Workspace, so nothing
downstream needs to know where the code came from.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType

from upgradepilot.models.errors import RepoTooLargeError, RepoUnavailableError
from upgradepilot.models.repo import CommitRecord

SKIP_DIRECTORIES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "dist",
        "build",
        ".tox",
        "site-packages",
    }
)

_GIT_TIMEOUT_SECONDS = 30


class Workspace:
    """A readable repository rooted at `root`.

    `cleanup_dir` is set only when this process created the directory (a
    clone). A user's own checkout is used in place and is never deleted.
    """

    def __init__(
        self,
        root: Path,
        commit_sha: str | None = None,
        cleanup_dir: Path | None = None,
    ) -> None:
        self._root = root.resolve()
        self._commit_sha = commit_sha
        self._cleanup_dir = cleanup_dir.resolve() if cleanup_dir else None

    @property
    def root(self) -> Path:
        return self._root

    @property
    def commit_sha(self) -> str | None:
        return self._commit_sha

    def iter_files(self, suffix: str = ".py") -> Iterator[Path]:
        """Yield repo-relative paths, skipping vendor dirs and escaping symlinks.

        `Path.rglob` on the pinned Python 3.14.5 defaults to
        `recurse_symlinks=False`. That means it never descends into a
        symlinked *directory* -- a symlinked directory pointing outside the
        root is simply never walked, so nothing inside it is ever yielded.
        A symlinked *file*, however, IS yielded by the default glob, which
        is why the containment check below still runs unconditionally.

        Never pass `recurse_symlinks=True` here: a symlink pointing at its
        own ancestor (or at the root itself) would make this generator
        expand without bound, and the caps in `enforce_caps` would not stop
        it -- they are only checked per yielded file, and a non-terminating
        generator never finishes yielding.
        """
        for path in sorted(self._root.rglob(f"*{suffix}")):
            relative = path.relative_to(self._root)
            if any(part in SKIP_DIRECTORIES for part in relative.parts):
                continue
            try:
                real = path.resolve(strict=True)
            except (OSError, RuntimeError):
                continue
            if real != self._root and self._root not in real.parents:
                continue  # symlink (typically a file) escaping the workspace
            if not real.is_file():
                continue
            yield relative

    def read_text(self, relative: Path) -> str:
        """Read a repo-relative file as UTF-8.

        A UnicodeDecodeError is allowed to propagate: Phase 2 records the
        file as skipped rather than silently mangling its contents.
        """
        target = (self._root / relative).resolve()
        if target != self._root and self._root not in target.parents:
            raise ValueError(f"{relative} resolves outside the workspace")
        return target.read_text(encoding="utf-8")

    def enforce_caps(self, max_files: int, max_bytes: int) -> None:
        """Reject oversized repositories before any analysis begins.

        `max_files` and `max_bytes` bound the Python files and Python bytes
        that `iter_files` would yield -- NOT the repository's total size on
        disk. That is deliberate: analyzable Python source is what bounds
        analyzer cost, which is the thing worth capping. A repository with
        a 2 GB committed binary asset but 50 Python files passes this check
        on purpose.

        Both caps are inclusive maxima, not exclusive bounds: exactly
        `max_files` files, or exactly `max_bytes` of Python source, passes.
        """
        total = 0
        for count, relative in enumerate(self.iter_files(".py"), start=1):
            total += (self._root / relative).stat().st_size
            if count > max_files:
                raise RepoTooLargeError(
                    f"This repository has more than {max_files} Python files.",
                    detail=f"root={self._root}",
                )
            if total > max_bytes:
                raise RepoTooLargeError(
                    "This repository's Python sources are too large to analyze.",
                    detail=f"bytes>{max_bytes} root={self._root}",
                )

    def git_log(self, limit: int = 100) -> list[CommitRecord]:
        """Recent commits with the files each touched, newest first.

        There are two distinct empty-result cases here, kept apart
        deliberately:

        - No `.git` directory: this is not a git checkout at all, so an
          empty list is the only sensible answer.
        - `.git` exists but `git log` exits non-zero (for example, a
          repository that has been initialized but has no commits yet):
          also documented as an empty list, because this process cannot
          reliably tell "no history" apart from other non-hanging git
          failures without parsing localized git stderr text, which is
          fragile and not worth building.

        A hung git process is different from both of those and is not
        swallowed: it raises `RepoUnavailableError` rather than
        masquerading as "no history".

        One subprocess call, not one per file.
        """
        if limit < 1:
            raise ValueError(f"git_log limit must be a positive integer, got {limit}")

        if not (self._root / ".git").exists():
            return []

        try:
            completed = subprocess.run(
                [
                    "git",
                    "log",
                    f"-n{limit}",
                    "--name-only",
                    "--no-merges",
                    "--format=__commit__%H|%ct",
                ],
                cwd=self._root,
                capture_output=True,
                text=True,
                timeout=_GIT_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RepoUnavailableError(
                "Reading the repository's git history took too long and was aborted.",
                detail=f"git log timed out after {_GIT_TIMEOUT_SECONDS}s root={self._root}",
            ) from exc

        if completed.returncode != 0:
            # Documented empty result, not a silent one -- see docstring.
            return []

        records: list[CommitRecord] = []
        sha: str | None = None
        timestamp: datetime | None = None
        files: list[str] = []

        def flush() -> None:
            if sha and timestamp:
                records.append(
                    CommitRecord(sha=sha, timestamp=timestamp, files=tuple(sorted(set(files))))
                )

        for line in completed.stdout.splitlines():
            if line.startswith("__commit__"):
                flush()
                raw_sha, _, raw_ts = line.removeprefix("__commit__").partition("|")
                sha = raw_sha
                timestamp = datetime.fromtimestamp(int(raw_ts), tz=UTC)
                files = []
            elif line.strip():
                files.append(line.strip())
        flush()
        return records

    def cleanup(self) -> None:
        if self._cleanup_dir and self._cleanup_dir.exists():
            shutil.rmtree(self._cleanup_dir, ignore_errors=True)

    def __enter__(self) -> Workspace:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.cleanup()
