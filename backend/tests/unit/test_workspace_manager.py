import os
import stat
import subprocess
import time
from pathlib import Path

import pytest

from upgradepilot.config import Settings
from upgradepilot.models.errors import LocalPathForbiddenError, RepoTooLargeError
from upgradepilot.models.inputs import LocalRepoRef, RemoteRepoRef
from upgradepilot.services.repo import manager as manager_module
from upgradepilot.services.repo.manager import WorkspaceManager


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        check=True,
        env={
            "GIT_AUTHOR_NAME": "T",
            "GIT_AUTHOR_EMAIL": "t@e.com",
            "GIT_COMMITTER_NAME": "T",
            "GIT_COMMITTER_EMAIL": "t@e.com",
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "HOME": str(cwd),
        },
    )


@pytest.fixture
def origin(tmp_path: Path) -> Path:
    source = tmp_path / "origin"
    (source / "src").mkdir(parents=True)
    (source / "src" / "a.py").write_text("a = 1\n")
    _git(source, "init", "-q", "-b", "main")
    _git(source, "add", ".")
    _git(source, "commit", "-q", "-m", "initial")
    return source


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        allowed_local_roots=(tmp_path,),
        allowed_url_schemes=frozenset({"file"}),
        workspace_dir=tmp_path / "workspaces",
        max_repo_files=100,
        max_repo_bytes=1_000_000,
        clone_depth=5,
    )


def test_open_dispatches_on_a_local_ref(origin: Path, settings: Settings) -> None:
    with WorkspaceManager(settings).open(LocalRepoRef(path=str(origin))) as workspace:
        assert workspace.root == origin.resolve()
        assert sorted(str(p) for p in workspace.iter_files(".py")) == ["src/a.py"]


def test_open_dispatches_on_a_remote_ref(origin: Path, settings: Settings) -> None:
    with WorkspaceManager(settings).open(RemoteRepoRef(url=f"file://{origin}")) as workspace:
        assert workspace.root != origin.resolve(), "a clone must not alias the origin"
        assert sorted(str(p) for p in workspace.iter_files(".py")) == ["src/a.py"]


def test_open_enforces_caps_before_returning(origin: Path, tmp_path: Path) -> None:
    strict = Settings(
        allowed_local_roots=(tmp_path,),
        allowed_url_schemes=frozenset({"file"}),
        workspace_dir=tmp_path / "workspaces",
        max_repo_files=0,
        max_repo_bytes=1_000_000,
    )
    with pytest.raises(RepoTooLargeError):
        WorkspaceManager(strict).open(LocalRepoRef(path=str(origin)))


def test_open_propagates_guard_failures(tmp_path: Path, settings: Settings) -> None:
    outside = tmp_path.parent / "not-allowed"
    outside.mkdir(exist_ok=True)
    with pytest.raises(LocalPathForbiddenError):
        WorkspaceManager(settings).open(LocalRepoRef(path=str(outside)))


def test_a_failed_clone_does_not_leak_a_workspace(tmp_path: Path, settings: Settings) -> None:
    from upgradepilot.models.errors import RepoUnavailableError

    with pytest.raises(RepoUnavailableError):
        WorkspaceManager(settings).open(RemoteRepoRef(url=f"file://{tmp_path / 'missing'}"))
    workspaces = settings.workspace_dir
    assert not workspaces.exists() or list(workspaces.iterdir()) == []


def test_open_cap_rejection_never_deletes_a_local_checkout(origin: Path, tmp_path: Path) -> None:
    """The safety net for the single most destructive possible bug here.

    A cap rejection on a RemoteRepoRef must delete the clone; a cap
    rejection on a LocalRepoRef must never touch the user's own checkout.
    `WorkspaceManager.open` relies on `open_local_repository` constructing
    `Workspace(..., cleanup_dir=None)` so that `workspace.cleanup()` is a
    no-op for local workspaces -- but that invariant lives in a different
    module (`local.py`), and nothing before this test failed if it were
    ever broken there. Content, not just existence, is asserted: an
    emptied-but-present directory must fail this test too.
    """
    marker = origin / "src" / "a.py"
    original_content = marker.read_text()

    strict = Settings(
        allowed_local_roots=(tmp_path,),
        allowed_url_schemes=frozenset({"file"}),
        workspace_dir=tmp_path / "workspaces",
        max_repo_files=0,
        max_repo_bytes=1_000_000,
    )
    with pytest.raises(RepoTooLargeError):
        WorkspaceManager(strict).open(LocalRepoRef(path=str(origin)))

    assert origin.exists()
    assert marker.exists()
    assert marker.read_text() == original_content


def test_sweep_stale_removes_only_old_directories(tmp_path: Path, settings: Settings) -> None:
    settings.workspace_dir.mkdir(parents=True)
    old = settings.workspace_dir / "repo-old"
    fresh = settings.workspace_dir / "repo-fresh"
    old.mkdir()
    fresh.mkdir()
    stale_time = time.time() - 7200
    os.utime(old, (stale_time, stale_time))

    removed = WorkspaceManager(settings).sweep_stale(max_age_seconds=3600)

    assert removed == [old]
    assert not old.exists()
    assert fresh.exists()


def test_sweep_stale_is_safe_when_nothing_exists(settings: Settings) -> None:
    assert WorkspaceManager(settings).sweep_stale(max_age_seconds=60) == []


def test_sweep_stale_ignores_unrelated_entries(tmp_path: Path, settings: Settings) -> None:
    """Only directories this service created (repo-*) are ever deleted."""
    settings.workspace_dir.mkdir(parents=True)
    unrelated = settings.workspace_dir / "important.txt"
    unrelated.write_text("keep me\n")
    stale_time = time.time() - 7200
    os.utime(unrelated, (stale_time, stale_time))

    assert WorkspaceManager(settings).sweep_stale(max_age_seconds=3600) == []
    assert unrelated.exists()


def test_sweep_stale_does_not_report_a_directory_it_failed_to_remove(
    tmp_path: Path, settings: Settings
) -> None:
    """The return value is a claim of what is genuinely gone.

    `shutil.rmtree(..., ignore_errors=True)` swallows the removal failure
    itself (a sweep is best-effort and one bad directory must not abort
    the rest) but the returned list must never include a path that is
    still on disk. One stale directory is made unremovable by stripping
    write permission from it (removing an entry requires write access on
    its *containing* directory) so `rmtree` cannot unlink the file inside
    it; a second stale directory is left removable as a control.
    """
    settings.workspace_dir.mkdir(parents=True)
    stuck = settings.workspace_dir / "repo-stuck"
    removable = settings.workspace_dir / "repo-removable"
    stuck.mkdir()
    removable.mkdir()
    (stuck / "file.txt").write_text("data\n")

    stale_time = time.time() - 7200
    os.utime(stuck, (stale_time, stale_time))
    os.utime(removable, (stale_time, stale_time))

    # Strip write permission from `stuck` itself: removing its own child
    # entry requires write+execute on `stuck`, not on `workspace_dir`.
    stuck.chmod(stat.S_IRUSR | stat.S_IXUSR)

    try:
        removed = WorkspaceManager(settings).sweep_stale(max_age_seconds=3600)
        assert removed == [removable]
        assert stuck.exists()
        assert not removable.exists()
    finally:
        # Restore permissions so pytest's tmp_path teardown can clean up.
        stuck.chmod(stat.S_IRWXU)


def test_sweep_stale_prefix_matches_what_clone_repository_actually_produces(
    origin: Path, settings: Settings
) -> None:
    """Guards the coupling between clone.py's naming and this module's prefix.

    `manager.OWNED_WORKSPACE_PREFIX` must match the literal `clone_repository`
    uses to name its destination directory. Rather than duplicating that
    literal in this test (which would drift exactly as silently as the two
    modules would), this performs a real clone and asserts the directory it
    actually produced is matched by the constant `sweep_stale` uses. If
    `clone.py`'s naming ever changes, this test fails immediately instead of
    `sweep_stale` silently matching nothing.
    """
    with WorkspaceManager(settings).open(RemoteRepoRef(url=f"file://{origin}")) as workspace:
        assert workspace.root.name.startswith(manager_module.OWNED_WORKSPACE_PREFIX)
