import subprocess
import types
from pathlib import Path

import pytest

from upgradepilot.models.errors import InvalidRepoUrlError, RepoUnavailableError
from upgradepilot.services.repo import clone as clone_module
from upgradepilot.services.repo.clone import clone_repository

FILE_SCHEME = frozenset({"file"})


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
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
    """A real git repository with three commits, served over file://."""
    source = tmp_path / "origin"
    (source / "src").mkdir(parents=True)
    _git(source, "init", "-q", "-b", "main")
    for index in range(3):
        (source / "src" / f"mod{index}.py").write_text(f"value = {index}\n")
        _git(source, "add", ".")
        _git(source, "commit", "-q", "-m", f"commit {index}")
    return source


def test_clone_produces_a_readable_workspace(origin: Path, tmp_path: Path) -> None:
    workspace = clone_repository(
        f"file://{origin}",
        tmp_path / "workspaces",
        depth=10,
        allowed_schemes=FILE_SCHEME,
    )
    try:
        files = sorted(str(p) for p in workspace.iter_files(".py"))
        assert files == ["src/mod0.py", "src/mod1.py", "src/mod2.py"]
        assert workspace.commit_sha is not None
        assert len(workspace.commit_sha) == 40
    finally:
        workspace.cleanup()


def test_clone_retains_history_for_churn_signals(origin: Path, tmp_path: Path) -> None:
    """Depth must exceed 1 or git_log yields nothing useful."""
    workspace = clone_repository(
        f"file://{origin}", tmp_path / "workspaces", depth=10, allowed_schemes=FILE_SCHEME
    )
    try:
        assert len(workspace.git_log(limit=10)) == 3
    finally:
        workspace.cleanup()


def test_clone_respects_the_requested_depth(origin: Path, tmp_path: Path) -> None:
    workspace = clone_repository(
        f"file://{origin}", tmp_path / "workspaces", depth=1, allowed_schemes=FILE_SCHEME
    )
    try:
        assert len(workspace.git_log(limit=10)) == 1
    finally:
        workspace.cleanup()


def test_cleanup_removes_the_cloned_directory(origin: Path, tmp_path: Path) -> None:
    workspace = clone_repository(
        f"file://{origin}", tmp_path / "workspaces", depth=5, allowed_schemes=FILE_SCHEME
    )
    root = workspace.root
    assert root.exists()
    workspace.cleanup()
    assert not root.exists()


def test_context_manager_cleans_up(origin: Path, tmp_path: Path) -> None:
    with clone_repository(
        f"file://{origin}", tmp_path / "workspaces", depth=5, allowed_schemes=FILE_SCHEME
    ) as workspace:
        root = workspace.root
        assert root.exists()
    assert not root.exists()


def test_missing_repository_raises_repo_unavailable(tmp_path: Path) -> None:
    with pytest.raises(RepoUnavailableError) as excinfo:
        clone_repository(
            f"file://{tmp_path / 'nonexistent'}",
            tmp_path / "workspaces",
            depth=5,
            allowed_schemes=FILE_SCHEME,
        )
    assert excinfo.value.detail is not None, "git stderr must be preserved for logs"


def test_disallowed_scheme_is_rejected_before_any_subprocess(tmp_path: Path) -> None:
    workspaces = tmp_path / "workspaces"
    with pytest.raises(InvalidRepoUrlError):
        clone_repository(
            "ssh://git@github.com/acme/repo.git",
            workspaces,
            depth=5,
            allowed_schemes=frozenset({"https"}),
        )
    # Proves ordering, not merely that an error was raised: if a subprocess
    # had been spawned, `dest_parent` (and possibly a partial clone
    # directory under it) would exist on disk.
    assert not workspaces.exists()


def test_failed_clone_leaves_no_partial_directory(tmp_path: Path) -> None:
    workspaces = tmp_path / "workspaces"
    with pytest.raises(RepoUnavailableError):
        clone_repository(
            f"file://{tmp_path / 'nonexistent'}",
            workspaces,
            depth=5,
            allowed_schemes=FILE_SCHEME,
        )
    leftovers = list(workspaces.iterdir()) if workspaces.exists() else []
    assert leftovers == []


def test_each_clone_gets_its_own_directory(origin: Path, tmp_path: Path) -> None:
    first = clone_repository(
        f"file://{origin}", tmp_path / "workspaces", depth=2, allowed_schemes=FILE_SCHEME
    )
    second = clone_repository(
        f"file://{origin}", tmp_path / "workspaces", depth=2, allowed_schemes=FILE_SCHEME
    )
    try:
        assert first.root != second.root
    finally:
        first.cleanup()
        second.cleanup()


@pytest.mark.parametrize("depth", [0, -1, -100])
def test_non_positive_depth_is_clamped_to_one(origin: Path, tmp_path: Path, depth: int) -> None:
    """depth <= 0 must be clamped to 1 rather than crash git or be passed through."""
    workspace = clone_repository(
        f"file://{origin}", tmp_path / "workspaces", depth=depth, allowed_schemes=FILE_SCHEME
    )
    try:
        assert len(workspace.git_log(limit=10)) == 1
    finally:
        workspace.cleanup()


def _make_fake_subprocess_module(run):
    """A stand-in for the `subprocess` module, scoped to `clone.py` only.

    `clone.py` calls `subprocess.run(...)` and catches
    `subprocess.TimeoutExpired`, both looked up on the module-level name
    `subprocess` at call time. Patching `clone_module.subprocess` to this
    object -- rather than mutating the real `subprocess` module's `.run`
    attribute in place -- means the fake only ever affects lookups made
    through `clone.py`'s own `subprocess` binding, so it cannot leak into
    any other code (e.g. the `_git` helper above, or Workspace's own git
    subprocess calls) that happens to run during the same test.
    `TimeoutExpired` is passed through unchanged so `except
    subprocess.TimeoutExpired` in `clone.py` still matches a real instance
    of it.
    """
    return types.SimpleNamespace(run=run, TimeoutExpired=subprocess.TimeoutExpired)


def test_failed_clone_removes_a_partially_created_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The `rmtree` on the non-zero-exit branch is untested by every other
    case in this file: for a missing `file://` source, git never creates
    `destination` in the first place, so "no leftovers" holds whether or
    not that `rmtree` call exists. Make the failure deterministic instead
    of racing a real clone against a timeout: monkeypatch `subprocess.run`
    (scoped to this module, see `_make_fake_subprocess_module`) to create a
    non-empty `destination` -- simulating git getting partway through a
    clone before failing -- and then report a non-zero exit, with no large
    origin and no wall-clock dependency.
    """
    created: list[Path] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        destination = Path(command[-1])
        destination.mkdir(parents=True)
        (destination / "partial-file").write_text("junk")
        created.append(destination)
        return subprocess.CompletedProcess(
            command, returncode=128, stdout="", stderr="fatal: simulated failure"
        )

    monkeypatch.setattr(clone_module, "subprocess", _make_fake_subprocess_module(fake_run))

    with pytest.raises(RepoUnavailableError):
        clone_repository(
            f"file://{tmp_path / 'origin'}",
            tmp_path / "workspaces",
            depth=5,
            allowed_schemes=FILE_SCHEME,
        )

    assert len(created) == 1, "the fake git clone must have run exactly once"
    assert not created[0].exists(), "rmtree on the non-zero-exit branch must remove destination"


def test_timed_out_clone_removes_a_partially_created_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same reasoning as the sibling test above, for the `TimeoutExpired`
    branch: monkeypatch `subprocess.run` to create a non-empty
    `destination` and then raise `TimeoutExpired`, deterministically,
    rather than racing a real clone of a large repository against a very
    short timeout.
    """
    created: list[Path] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        destination = Path(command[-1])
        destination.mkdir(parents=True)
        (destination / "partial-file").write_text("junk")
        created.append(destination)
        raise subprocess.TimeoutExpired(cmd=command, timeout=kwargs.get("timeout", 1))  # type: ignore[arg-type]

    monkeypatch.setattr(clone_module, "subprocess", _make_fake_subprocess_module(fake_run))

    with pytest.raises(RepoUnavailableError):
        clone_repository(
            f"file://{tmp_path / 'origin'}",
            tmp_path / "workspaces",
            depth=5,
            allowed_schemes=FILE_SCHEME,
            timeout=1,
        )

    assert len(created) == 1, "the fake git clone must have run exactly once"
    assert not created[0].exists(), "rmtree on the TimeoutExpired branch must remove destination"


def test_file_url_without_authority_separator_is_rejected(tmp_path: Path) -> None:
    """`file:./relative` parses as scheme=file under `validate_clone_url`
    (which exempts `file:` from its host check), but git's own transport
    selection only recognises `scheme://...` -- anything shaped like
    `word:path` with no `//` falls through to git's scp-style remote-alias
    parsing, so git would read this as ssh to a host literally named
    "file", not as a local-filesystem clone. `clone_repository` must reject
    this shape itself before it ever reaches git.
    """
    workspaces = tmp_path / "workspaces"
    with pytest.raises(InvalidRepoUrlError):
        clone_repository(
            "file:./relative",
            workspaces,
            depth=5,
            allowed_schemes=FILE_SCHEME,
        )
    # Same ordering guarantee as the disallowed-scheme test: rejected before
    # any subprocess could have been spawned.
    assert not workspaces.exists()


def test_a_global_insteadof_rule_cannot_redirect_the_clone(
    origin: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """url.<base>.insteadOf rewrites a URL after our allowlist approved it.

    GIT_CONFIG_GLOBAL=/dev/null is what stops that, so this asserts the
    protection directly rather than trusting HOME to stay unset. A fake
    HOME (never the real one) holds a .gitconfig that would redirect any
    file:// clone to a nonexistent host.

    `subprocess.run(env=...)` replaces the child's environment entirely, so
    `monkeypatch.setenv("HOME", ...)` on this test process would never
    actually reach the `git clone` subprocess -- the clone would "pass" for
    the wrong reason (HOME never forwarded), not because GIT_CONFIG_GLOBAL
    did anything. This directly patches the module's env dict to include
    HOME instead, simulating exactly the scenario Finding 1 warns about: a
    future change that forwards HOME into this subprocess call for some
    unrelated, legitimate reason. Even with HOME present, the clone must
    still succeed from the real origin, proving GIT_CONFIG_GLOBAL (not the
    absence of HOME) is what stops the rewrite.
    """
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    (fake_home / ".gitconfig").write_text(
        '[url "https://REWRITTEN.invalid/"]\n\tinsteadOf = file://\n'
    )
    patched_env = dict(clone_module._NON_INTERACTIVE_GIT_ENV)
    patched_env["HOME"] = str(fake_home)
    monkeypatch.setattr(clone_module, "_NON_INTERACTIVE_GIT_ENV", patched_env)

    workspace = clone_repository(
        f"file://{origin}", tmp_path / "workspaces", depth=5, allowed_schemes=FILE_SCHEME
    )
    try:
        assert workspace.commit_sha is not None
        files = sorted(str(p) for p in workspace.iter_files(".py"))
        assert files == ["src/mod0.py", "src/mod1.py", "src/mod2.py"]
    finally:
        workspace.cleanup()
