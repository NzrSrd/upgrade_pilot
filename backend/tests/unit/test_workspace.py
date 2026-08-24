import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from upgradepilot.models.errors import RepoTooLargeError, RepoUnavailableError
from upgradepilot.services.repo.local import open_local_repository, read_commit_sha
from upgradepilot.services.repo.workspace import Workspace


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "demo"
    (root / "src" / "app").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "src" / "app" / "models.py").write_text("from pydantic import BaseModel\n")
    (root / "src" / "app" / "service.py").write_text("x = 1\n")
    (root / "tests" / "test_models.py").write_text("def test_x(): pass\n")
    (root / "README.md").write_text("# demo\n")
    return root


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
        env={
            "GIT_AUTHOR_NAME": "T",
            "GIT_AUTHOR_EMAIL": "t@e.com",
            "GIT_COMMITTER_NAME": "T",
            "GIT_COMMITTER_EMAIL": "t@e.com",
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "HOME": str(root),
        },
    )
    return result.stdout.strip()


@pytest.fixture
def git_repo(repo: Path) -> Path:
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "initial")
    (repo / "src" / "app" / "models.py").write_text("from pydantic import BaseModel, validator\n")
    _git(repo, "add", "src/app/models.py")
    _git(repo, "commit", "-q", "-m", "add validator import")
    return repo


@pytest.fixture
def empty_git_repo(tmp_path: Path) -> Path:
    """A real, initialized git repository with no commits yet.

    Distinct from `repo` (no `.git` at all) and from `broken_git_repo`
    (a `.git` that exists but is unusable) -- this is the "usable
    repository, simply new" case that must return an empty result, not
    raise."""
    root = tmp_path / "empty"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    return root


@pytest.fixture
def broken_git_repo(git_repo: Path) -> Path:
    """A git repository that is unusable: `.git` exists but `HEAD` is gone.

    This must never be read as "no history" -- it is a real failure."""
    (git_repo / ".git" / "HEAD").unlink()
    return git_repo


# --- iteration ------------------------------------------------------------


def test_iter_files_returns_relative_python_paths(repo: Path) -> None:
    workspace = Workspace(root=repo)
    found = sorted(str(p) for p in workspace.iter_files(".py"))
    assert found == ["src/app/models.py", "src/app/service.py", "tests/test_models.py"]


def test_iter_files_skips_the_git_directory(git_repo: Path) -> None:
    workspace = Workspace(root=git_repo)
    assert not any(".git" in str(p) for p in workspace.iter_files(".py"))


def test_iter_files_skips_vendor_directories(repo: Path) -> None:
    (repo / "node_modules" / "pkg").mkdir(parents=True)
    (repo / "node_modules" / "pkg" / "x.py").write_text("y = 2\n")
    (repo / ".venv" / "lib").mkdir(parents=True)
    (repo / ".venv" / "lib" / "z.py").write_text("z = 3\n")

    workspace = Workspace(root=repo)
    found = {str(p) for p in workspace.iter_files(".py")}
    assert not any(p.startswith(("node_modules", ".venv")) for p in found)


def test_iter_files_does_not_descend_into_symlinked_directories(repo: Path, tmp_path: Path) -> None:
    """This asserts a library guarantee we depend on, not the escape guard.

    `Path.rglob` on the pinned Python 3.14.5 defaults to
    `recurse_symlinks=False`, so it never descends into a symlinked
    *directory* in the first place -- `secret.py` below is never reached
    by the glob, and the escape check inside `iter_files` is never
    exercised by this test. It is locked in here in case that default
    ever changes upstream; the guard that actually matters for a
    symlinked *file* is exercised separately below.
    """
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.py").write_text("token = 'leak'\n")
    (repo / "linked").symlink_to(outside, target_is_directory=True)

    workspace = Workspace(root=repo)
    found = {str(p) for p in workspace.iter_files(".py")}
    assert "linked/secret.py" not in found


def test_iter_files_skips_a_symlinked_file_escaping_the_root(repo: Path, tmp_path: Path) -> None:
    """A symlinked FILE is yielded by rglob (unlike a symlinked directory), so
    this is the case the escape guard exists for. Reading it would pull code
    from outside the workspace into the analyzer and cite it as repository
    evidence."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.py").write_text("SECRET = 1\n")
    (repo / "src" / "link.py").symlink_to(outside / "secret.py")

    assert not any(p.name == "link.py" for p in Workspace(root=repo).iter_files(".py"))


# --- reading --------------------------------------------------------------


def test_read_text_returns_file_contents(repo: Path) -> None:
    workspace = Workspace(root=repo)
    assert "BaseModel" in workspace.read_text(Path("src/app/models.py"))


def test_read_text_rejects_escaping_the_root(repo: Path) -> None:
    workspace = Workspace(root=repo)
    with pytest.raises(ValueError, match="outside the workspace"):
        workspace.read_text(Path("../outside.py"))


# --- caps -----------------------------------------------------------------


def test_enforce_caps_passes_within_limits(repo: Path) -> None:
    Workspace(root=repo).enforce_caps(max_files=100, max_bytes=1_000_000)


def test_enforce_caps_rejects_too_many_files(repo: Path) -> None:
    with pytest.raises(RepoTooLargeError, match="files"):
        Workspace(root=repo).enforce_caps(max_files=2, max_bytes=1_000_000)


def test_enforce_caps_rejects_too_many_bytes(repo: Path) -> None:
    with pytest.raises(RepoTooLargeError, match="large"):
        Workspace(root=repo).enforce_caps(max_files=100, max_bytes=10)


def test_enforce_caps_accepts_exactly_max_files(repo: Path) -> None:
    """repo has exactly 3 .py files; the cap is a maximum, not an exclusive
    bound, so max_files=3 must be accepted, not rejected."""
    Workspace(root=repo).enforce_caps(max_files=3, max_bytes=1_000_000)


def test_enforce_caps_accepts_exactly_max_bytes(repo: Path) -> None:
    total = sum((repo / p).stat().st_size for p in Workspace(root=repo).iter_files(".py"))
    Workspace(root=repo).enforce_caps(max_files=100, max_bytes=total)


# --- git ------------------------------------------------------------------


def test_git_log_returns_commits_newest_first_with_touched_files(git_repo: Path) -> None:
    workspace = Workspace(root=git_repo)
    commits = workspace.git_log(limit=10)

    assert len(commits) == 2
    assert commits[0].files == ("src/app/models.py",)
    assert commits[0].timestamp >= commits[1].timestamp
    assert len(commits[0].sha) == 40


def test_git_log_is_empty_when_there_is_no_git_history(repo: Path) -> None:
    assert Workspace(root=repo).git_log() == []


def test_git_log_rejects_a_non_positive_limit(git_repo: Path) -> None:
    workspace = Workspace(root=git_repo)
    with pytest.raises(ValueError, match="limit"):
        workspace.git_log(limit=0)
    with pytest.raises(ValueError, match="limit"):
        workspace.git_log(limit=-5)


def test_git_log_raises_on_a_hung_git_process(git_repo: Path) -> None:
    """A timeout must never be swallowed into an empty list that looks like
    'no history' -- it means something is genuinely wrong."""
    workspace = Workspace(root=git_repo)
    with (
        patch(
            "upgradepilot.services.repo.workspace.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["git", "log"], timeout=30),
        ),
        pytest.raises(RepoUnavailableError),
    ):
        workspace.git_log(limit=10)


def test_commit_sha_is_read_for_a_git_repository(git_repo: Path) -> None:
    workspace = open_local_repository(str(git_repo), allowed_roots=[git_repo.parent])
    assert workspace.commit_sha is not None
    assert len(workspace.commit_sha) == 40


def test_commit_sha_is_none_without_git(repo: Path) -> None:
    workspace = open_local_repository(str(repo), allowed_roots=[repo.parent])
    assert workspace.commit_sha is None


def test_read_commit_sha_raises_on_a_hung_git_process(git_repo: Path) -> None:
    with (
        patch(
            "upgradepilot.services.repo.workspace.subprocess.run",
            side_effect=subprocess.TimeoutExpired(
                cmd=["git", "rev-parse", "--quiet", "--verify", "HEAD"], timeout=15
            ),
        ),
        pytest.raises(RepoUnavailableError),
    ):
        read_commit_sha(git_repo)


# --- git failure modes: no commits vs. broken vs. no .git at all ----------


def test_git_log_and_commit_sha_are_empty_for_a_repo_with_no_commits(
    empty_git_repo: Path,
) -> None:
    """A real, usable repository that simply has no history yet is a
    legitimate empty result, not an error."""
    assert Workspace(root=empty_git_repo).git_log() == []
    assert read_commit_sha(empty_git_repo) is None


def test_git_log_raises_when_the_repository_is_broken(broken_git_repo: Path) -> None:
    """`.git` exists but `HEAD` is gone: this must not read as "no history"."""
    with pytest.raises(RepoUnavailableError):
        Workspace(root=broken_git_repo).git_log()


def test_read_commit_sha_raises_when_the_repository_is_broken(
    broken_git_repo: Path,
) -> None:
    with pytest.raises(RepoUnavailableError):
        read_commit_sha(broken_git_repo)


def test_no_git_directory_is_a_distinct_no_subprocess_path(repo: Path) -> None:
    """No `.git` at all must not spawn git and must not share a code path
    with "no commits yet" -- it is checked and returned before any
    subprocess call."""
    with patch("upgradepilot.services.repo.workspace.subprocess.run") as run:
        assert Workspace(root=repo).git_log() == []
        assert read_commit_sha(repo) is None
        run.assert_not_called()


# --- lifecycle ------------------------------------------------------------


def test_local_workspace_is_never_deleted_on_cleanup(repo: Path) -> None:
    """A user's own checkout is used in place and must survive cleanup."""
    workspace = open_local_repository(str(repo), allowed_roots=[repo.parent])
    workspace.cleanup()
    assert repo.exists()


def test_cleanup_removes_an_owned_temp_directory(tmp_path: Path) -> None:
    owned = tmp_path / "cloned"
    (owned / "src").mkdir(parents=True)
    (owned / "src" / "a.py").write_text("a = 1\n")

    workspace = Workspace(root=owned, cleanup_dir=owned)
    workspace.cleanup()
    assert not owned.exists()


def test_context_manager_cleans_up_owned_directories(tmp_path: Path) -> None:
    owned = tmp_path / "cloned"
    owned.mkdir()
    with Workspace(root=owned, cleanup_dir=owned) as workspace:
        assert workspace.root.exists()
    assert not owned.exists()
