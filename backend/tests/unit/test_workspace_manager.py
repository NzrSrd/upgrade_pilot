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
from upgradepilot.services.repo.workspace import Workspace


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

    # An existing, allowlisted directory that is not a git repository, so
    # the failure comes from git itself. A *missing* path is now refused by
    # `resolve_local_path` before any subprocess runs (the file:// door and
    # the LocalRepoRef door share that check), which would make this test
    # pass without git ever having been invoked.
    not_a_repository = tmp_path / "not-a-repo"
    not_a_repository.mkdir()

    with pytest.raises(RepoUnavailableError):
        WorkspaceManager(settings).open(RemoteRepoRef(url=f"file://{not_a_repository}"))
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


def _strict_settings(tmp_path: Path) -> Settings:
    """Settings whose file cap rejects every repository, however small."""
    return Settings(
        allowed_local_roots=(tmp_path,),
        allowed_url_schemes=frozenset({"file"}),
        workspace_dir=tmp_path / "workspaces",
        max_repo_files=0,
        max_repo_bytes=1_000_000,
        clone_depth=5,
    )


def test_open_cap_rejection_removes_the_clone_it_created(origin: Path, tmp_path: Path) -> None:
    """The other half of the cap-rejection contract: a clone IS deleted.

    `test_open_cap_rejection_never_deletes_a_local_checkout` above covers
    a LocalRepoRef, and for a local ref `Workspace.cleanup()` is a no-op
    by design (`cleanup_dir=None`) -- so that test passes with `open`'s
    entire cleanup guard deleted and proves nothing about it. Only a
    RemoteRepoRef reaches a Workspace with a real `cleanup_dir`, so only
    this test can go red when the guard goes. The two together are the
    actual contract: clones are removed, checkouts never are.

    Asserting the workspace directory is empty rather than remembering
    the clone's name is deliberate: a cap-rejected clone must leave
    nothing at all behind, whatever it was called.
    """
    strict = _strict_settings(tmp_path)

    with pytest.raises(RepoTooLargeError):
        WorkspaceManager(strict).open(RemoteRepoRef(url=f"file://{origin}"))

    leftover = sorted(strict.workspace_dir.iterdir()) if strict.workspace_dir.exists() else []
    assert leftover == [], f"a cap-rejected clone was left on disk: {leftover}"


def test_a_cleanup_failure_does_not_displace_the_original_error(
    origin: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The caller must still learn *why* the workspace was rejected.

    If `cleanup()` raises while handling a cap rejection, the cleanup
    failure must not replace the RepoTooLargeError that actually explains
    what happened -- the diagnostic value is in the original. The cleanup
    failure must not vanish silently either (rule 20), so it is attached
    to the original as a note and asserted here.
    """

    def exploding_cleanup(self: Workspace) -> None:
        raise OSError("cleanup exploded")

    monkeypatch.setattr(Workspace, "cleanup", exploding_cleanup)
    strict = _strict_settings(tmp_path)

    with pytest.raises(RepoTooLargeError) as caught:
        WorkspaceManager(strict).open(RemoteRepoRef(url=f"file://{origin}"))

    notes = getattr(caught.value, "__notes__", [])
    assert any("cleanup exploded" in note for note in notes), (
        f"the cleanup failure was swallowed instead of recorded: {notes}"
    )


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


def test_sweep_stale_never_deletes_a_directory_it_does_not_own(
    tmp_path: Path, settings: Settings
) -> None:
    """The prefix half of the sweep's condition, which no other test reaches.

    `test_sweep_stale_ignores_unrelated_entries` above uses a *file*,
    which `is_dir()` already excludes -- so the name-prefix check is
    never evaluated there and that test still passes with the prefix
    check deleted. This uses a stale *directory* whose name is not
    `repo-*`: an operator's own directory that happens to live inside the
    workspace directory, which nothing but the prefix check can save. A
    stale owned directory is swept in the same run as a control, so the
    test cannot pass merely because the sweep did nothing.
    """
    settings.workspace_dir.mkdir(parents=True)
    operator_dir = settings.workspace_dir / "operator-notes"
    operator_dir.mkdir()
    keeper = operator_dir / "keep.txt"
    keeper.write_text("keep me\n")
    owned = settings.workspace_dir / "repo-abandoned"
    owned.mkdir()

    stale_time = time.time() - 7200
    os.utime(keeper, (stale_time, stale_time))
    os.utime(operator_dir, (stale_time, stale_time))
    os.utime(owned, (stale_time, stale_time))

    removed = WorkspaceManager(settings).sweep_stale(max_age_seconds=3600)

    assert removed == [owned]
    assert operator_dir not in removed
    assert operator_dir.exists()
    assert keeper.read_text() == "keep me\n", "an unowned directory's contents were touched"
    assert not owned.exists(), "the owned control directory should have been swept"


def test_sweep_stale_rejects_a_negative_age(settings: Settings) -> None:
    """A negative age puts the cutoff in the future and would delete everything.

    Rejected with `ValueError` rather than an `AppError`: passing a
    negative age is a caller bug, not an operating condition. It is not
    clamped either, which would hide that bug behind a destructive
    default.
    """
    settings.workspace_dir.mkdir(parents=True)
    fresh = settings.workspace_dir / "repo-fresh"
    fresh.mkdir()

    with pytest.raises(ValueError):
        WorkspaceManager(settings).sweep_stale(max_age_seconds=-1)

    assert fresh.exists(), "a rejected sweep must not have deleted anything"


def test_sweep_stale_with_a_zero_age_removes_every_owned_workspace(
    settings: Settings,
) -> None:
    """`0` legitimately means "regardless of age", so it is not rejected.

    This is the boundary between the two cases either side of it:
    `test_sweep_stale_rejects_a_negative_age` above covers a negative age,
    and `test_sweep_stale_removes_only_old_directories` covers a positive
    one respecting the cutoff. A just-created directory is included here
    because "regardless of age" has to include it -- and the unowned
    directory still survives, since a zero age relaxes the age check
    only, never the ownership check.
    """
    settings.workspace_dir.mkdir(parents=True)
    just_created = settings.workspace_dir / "repo-just-created"
    old = settings.workspace_dir / "repo-old"
    unowned = settings.workspace_dir / "operator-notes"
    for directory in (just_created, old, unowned):
        directory.mkdir()
    stale_time = time.time() - 7200
    os.utime(old, (stale_time, stale_time))

    removed = WorkspaceManager(settings).sweep_stale(max_age_seconds=0)

    assert removed == [just_created, old]
    assert not just_created.exists()
    assert not old.exists()
    assert unowned.exists()


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


def test_sweep_stale_continues_past_an_entry_that_vanishes_mid_sweep(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One bad directory must not abort the sweep of the rest -- which the
    docstring promised and an unguarded `entry.stat()` did not deliver.

    `iterdir()` produces the whole list before any of it is stat'ed, so an
    entry another process removes in between raises `FileNotFoundError`
    straight out of `sweep_stale` and every later entry is left untouched.
    Demonstrated before the fix: with the first entry removed mid-sweep,
    the two stale directories after it survived.

    The race is made deterministic by patching `Path.stat` to delete the
    first owned entry the moment it is asked about -- a real removal, not a
    faked exception, so the assertions below are about real directories.
    Two stale controls after it prove the sweep continued, and the return
    value must name only what is genuinely gone: the vanished entry is not
    in it, because this method's only output is a claim about removals and
    it never removed that one.
    """
    settings.workspace_dir.mkdir(parents=True)
    vanishing = settings.workspace_dir / "repo-a-vanishes"
    later_one = settings.workspace_dir / "repo-b-stale"
    later_two = settings.workspace_dir / "repo-c-stale"
    for directory in (vanishing, later_one, later_two):
        directory.mkdir()
    stale_time = time.time() - 7200
    for directory in (vanishing, later_one, later_two):
        os.utime(directory, (stale_time, stale_time))

    real_stat = Path.stat

    def stat_that_removes_the_first_entry(self: Path, **kwargs: object) -> os.stat_result:
        if self == vanishing and vanishing.exists():
            vanishing.rmdir()
        return real_stat(self, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "stat", stat_that_removes_the_first_entry)

    removed = WorkspaceManager(settings).sweep_stale(max_age_seconds=3600)

    assert removed == [later_one, later_two], (
        "the sweep must continue past a vanished entry and report only real removals"
    )
    assert not later_one.exists()
    assert not later_two.exists()
    assert not vanishing.exists()


def test_sweep_stale_continues_past_an_entry_it_cannot_stat(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other half of the same guard: an entry that is present but
    unreadable. It is left on disk for a later sweep to retry and is absent
    from the return value, so no caller is told about a removal that did
    not happen."""
    settings.workspace_dir.mkdir(parents=True)
    unreadable = settings.workspace_dir / "repo-a-unreadable"
    later = settings.workspace_dir / "repo-b-stale"
    for directory in (unreadable, later):
        directory.mkdir()
    stale_time = time.time() - 7200
    for directory in (unreadable, later):
        os.utime(directory, (stale_time, stale_time))

    real_stat = Path.stat

    def stat_that_fails_for_one_entry(self: Path, **kwargs: object) -> os.stat_result:
        if self == unreadable:
            raise PermissionError(13, "Permission denied")
        return real_stat(self, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "stat", stat_that_fails_for_one_entry)

    removed = WorkspaceManager(settings).sweep_stale(max_age_seconds=3600)

    assert removed == [later]
    assert unreadable.exists(), "an unstattable entry must be left for the next sweep"
