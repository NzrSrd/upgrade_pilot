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


def probe_head_sha(root: Path, *, timeout: int) -> str | None:
    """Probe `git rev-parse --quiet --verify HEAD` at `root`.

    This is the single discriminator that `Workspace.git_log` and
    `local.read_commit_sha` both use to tell "a real repository with no
    commits yet" apart from "this repository is unusable" -- a documented
    git exit-code contract, not a heuristic, and it requires parsing no
    stderr text at all:

      0   HEAD resolves. `--verify` prints the resolved sha to stdout on
          success, which this returns directly.
      1   A real, usable repository with no commits yet. Returns None --
          a legitimate, documented empty result.
      *   Anything else (typically 128, or a killed process) means the
          repository itself is unusable: corrupted, permission denied, or
          an incompatible git. Raises RepoUnavailableError.

    A subprocess timeout is a fourth case, also never swallowed: it
    raises RepoUnavailableError rather than returning None, because a
    hang is not "no commits" and must not be read as if it were.

    Callers are expected to have already checked that `.git` exists --
    "not a git repository at all" is a distinct, subprocess-free case and
    is not this function's concern.

    The `detail` on a raised error carries only known-shape fields (the
    return code and which git subcommand ran) -- never raw stderr, which
    is untrusted, unbounded process output and must not be logged
    verbatim.
    """
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--quiet", "--verify", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RepoUnavailableError(
            "Reading the repository's current commit took too long and was aborted.",
            detail=f"git rev-parse --quiet --verify HEAD timed out after {timeout}s root={root}",
        ) from exc

    if completed.returncode == 0:
        return completed.stdout.strip() or None
    if completed.returncode == 1:
        return None
    raise RepoUnavailableError(
        "This repository's git history could not be read.",
        detail=(f"git rev-parse --quiet --verify HEAD exited {completed.returncode} root={root}"),
    )


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

        Three distinct outcomes, kept apart on purpose -- a corrupted
        repository must never read as "no history", because `git_log`
        feeds the `churn_on_affected` risk factor and a silent empty
        result would make risk get computed from evidence that was
        never actually gathered:

        - No `.git` directory: not a git checkout at all. No subprocess
          is spawned; returns `[]`.
        - `.git` exists and `probe_head_sha` reports no commits yet (a
          real, usable repository that is simply new): returns `[]`.
          This is a legitimate, documented empty result.
        - `.git` exists and the repository is otherwise unusable
          (corrupted, permission denied, an incompatible git) or a git
          subprocess hangs: raises `RepoUnavailableError`. The caller
          must be told history could not be read, not handed a zero.

        See `probe_head_sha` for how "no commits" is distinguished from
        "unusable" without parsing any git stderr text.

        One subprocess call for the log itself, not one per file.
        """
        if limit < 1:
            raise ValueError(f"git_log limit must be a positive integer, got {limit}")

        if not (self._root / ".git").exists():
            return []

        if probe_head_sha(self._root, timeout=_GIT_TIMEOUT_SECONDS) is None:
            return []  # a real, usable repository with no commits yet

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
            # probe_head_sha already confirmed HEAD resolves, so a non-zero
            # exit here is a genuine, unexpected git failure -- not a case
            # that has any legitimate reading as "no history".
            raise RepoUnavailableError(
                "This repository's git history could not be read.",
                detail=f"git log exited {completed.returncode} root={self._root}",
            )

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
