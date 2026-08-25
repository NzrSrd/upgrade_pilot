import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from upgradepilot.models.errors import RepoTooLargeError, RepoUnavailableError
from upgradepilot.services.repo.local import open_local_repository, read_commit_sha
from upgradepilot.services.repo.workspace import Workspace, probe_head_sha


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


# --- A directory named like a Python file is not a file (item 5.4) --


def test_iter_files_skips_a_directory_whose_name_ends_in_py(repo: Path) -> None:
    """Deleting the `not real.is_file()` check turned zero tests red.

    `rglob("*.py")` matches by name, so `mkdir pkg.py` is yielded like any
    other match -- and `read_text`, whose docstring says the only error it
    can raise is a `UnicodeDecodeError`, then raises `IsADirectoryError`
    instead. That is not hypothetical: `foo.py/` appears in real trees
    (an accidental `mkdir`, a stale build output, an `__init__.py`
    directory from a botched refactor).

    Both halves are asserted, because only the pair pins the contract: the
    directory is not yielded, AND the ordinary sibling file still is, so
    the test cannot pass by yielding nothing at all.
    """
    (repo / "src" / "app" / "pkg.py").mkdir()
    (repo / "src" / "app" / "pkg.py" / "inner.txt").write_text("not python\n")

    yielded = sorted(str(p) for p in Workspace(repo).iter_files(".py"))

    assert "src/app/pkg.py" not in yielded
    assert "src/app/service.py" in yielded


def test_read_text_never_receives_a_directory_from_iter_files(repo: Path) -> None:
    """The consequence the guard prevents, stated as the property
    `read_text`'s docstring claims: every path `iter_files` yields can be
    read as text, so a `UnicodeDecodeError` is the only failure a caller
    has to consider."""
    (repo / "src" / "app" / "pkg.py").mkdir()
    workspace = Workspace(repo)

    for relative in workspace.iter_files(".py"):
        workspace.read_text(relative)


# --- All three git invocations run under the hardened environment (item 8) --


@pytest.fixture
def fake_git_on_path(tmp_path: Path) -> Path:
    """A directory containing a stand-in `git`, for putting on a PATH.

    `subprocess.run(env=...)` resolves the executable using the *child's*
    PATH, so a call site that passes `HARDENED_GIT_ENV` finds this fake and
    a call site that inherits the ambient environment finds the real git.
    That difference is what gives the two tests below teeth: they go red
    the moment a call site stops passing the shared environment.
    """
    bindir = tmp_path / "fake-bin"
    bindir.mkdir()
    script = bindir / "git"
    script.write_text(
        "#!/bin/sh\n"
        # Skip any leading `-c key=value` pair so this stays a stand-in for
        # `git` itself rather than for one exact argv: `git_log` passes
        # `-c core.quotePath=false` before the subcommand.
        'while [ "$1" = "-c" ]; do shift 2; done\n'
        'case "$1" in\n'
        "  rev-parse) echo 1111111111111111111111111111111111111111 ;;\n"
        "  log) printf '__commit__%s|%s\\n%s\\n' "
        "2222222222222222222222222222222222222222 1700000000 sentinel-from-fake-git.py ;;\n"
        "  *) exit 1 ;;\n"
        "esac\n"
    )
    script.chmod(0o755)
    return bindir


def _hardened_env_with(bindir: Path) -> dict[str, str]:
    from upgradepilot.services.repo import workspace as workspace_module

    patched = dict(workspace_module.HARDENED_GIT_ENV)
    patched["PATH"] = str(bindir)
    return patched


def test_probe_head_sha_runs_under_the_shared_hardened_environment(
    git_repo: Path, fake_git_on_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`probe_head_sha` inherited the ambient environment: the hardening
    dict lived in `clone.py` and only `clone.py` passed it, so two of the
    three git invocations in this package ran under whatever environment
    the server process happened to have -- including a developer's global
    git config, which reaches the two call sites that *parse* git output.

    Proven by substitution rather than by reading the source: with the
    shared environment's PATH pointed at a stand-in git, the value returned
    must be the stand-in's. Remove `env=HARDENED_GIT_ENV` from the call and
    the real git runs instead, returning the repository's real sha.
    """
    from upgradepilot.services.repo import workspace as workspace_module

    monkeypatch.setattr(workspace_module, "HARDENED_GIT_ENV", _hardened_env_with(fake_git_on_path))

    assert probe_head_sha(git_repo, timeout=5) == "1" * 40


def test_git_log_runs_under_the_shared_hardened_environment(
    git_repo: Path, fake_git_on_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other previously-unhardened call site, and the one where global
    config can corrupt a *parse* rather than merely a lookup."""
    from upgradepilot.services.repo import workspace as workspace_module

    monkeypatch.setattr(workspace_module, "HARDENED_GIT_ENV", _hardened_env_with(fake_git_on_path))

    records = Workspace(git_repo).git_log(limit=5)

    assert [record.sha for record in records] == ["2" * 40]
    assert records[0].files == ("sentinel-from-fake-git.py",)


def test_the_hardened_environment_is_one_object_shared_by_clone_and_workspace() -> None:
    """`clone.py` must not keep a copy. A second dict is how the two
    drifted apart in the first place -- the security-critical
    `GIT_CONFIG_GLOBAL=/dev/null` entry existed in one of them only -- and
    a copy would also silently defeat the monkeypatching the two tests
    above rely on."""
    from upgradepilot.services.repo import clone as clone_module
    from upgradepilot.services.repo import workspace as workspace_module

    # Unlike test_clone.py's read of the same name, this assertion's whole
    # point is the identity check through BOTH modules' bindings -- there is
    # no way to state "these are the same object" without naming the one
    # `clone.py` merely imports, so the `--no-implicit-reexport` complaint
    # is narrowly ignored here rather than routed around.
    assert clone_module.HARDENED_GIT_ENV is workspace_module.HARDENED_GIT_ENV  # type: ignore[attr-defined]
    assert workspace_module.HARDENED_GIT_ENV["GIT_CONFIG_GLOBAL"] == "/dev/null"


# --- F3: a legal POSIX filename must never crash the run (rule 20) ---------

_UNCITABLE_NAME = "back\\slash.py"
"""A perfectly legal POSIX filename that `RepoRelativePath` refuses, because
a backslash is a path separator on some platforms and an ordinary filename
character on others -- so a citation naming it could not be resolved. The
analyzer's input is an untrusted third-party repository, so this must be
excluded and recorded, never allowed to raise (CLAUDE.md rule 20)."""


def test_iter_files_skips_a_path_that_could_never_be_cited(repo: Path) -> None:
    """`iter_files` is the single boundary the whole analyzer reads the tree
    through, so it is where an unrepresentable path has to stop. Downstream
    there are four separate model constructors that would each raise on it
    (`ModelClass.file`, `Manifest.path`, `SkippedFile.path`,
    `RepoAnalysis.test_paths`)."""
    (repo / _UNCITABLE_NAME).write_text("from pydantic import BaseModel\n")
    workspace = Workspace(root=repo)
    yielded = [p.as_posix() for p in workspace.iter_files(".py")]

    assert _UNCITABLE_NAME not in yielded
    assert "src/app/models.py" in yielded
    assert workspace.uncitable_files(".py") == (_UNCITABLE_NAME,)


def test_uncitable_files_is_empty_for_an_ordinary_tree(repo: Path) -> None:
    """The negative direction. Without it, an implementation that reported
    every path as uncitable would pass the test above."""
    assert Workspace(root=repo).uncitable_files() == ()


def test_git_log_drops_a_path_that_could_never_be_cited(git_repo: Path) -> None:
    """`CommitRecord.files` is `tuple[RepoRelativePath, ...]`, and git reports
    this name as `"back\\\\slash.py"` -- quoted and re-escaped, so it fails
    validation twice over. Before this was filtered, one such file anywhere in
    the history raised an uncaught `ValidationError` out of `git_log`."""
    (git_repo / _UNCITABLE_NAME).write_text("x = 1\n")
    _git(git_repo, "add", "--", _UNCITABLE_NAME)
    _git(git_repo, "commit", "-q", "-m", "add an uncitable path")

    commits = Workspace(root=git_repo).git_log(limit=10)
    assert commits[0].files == ()
    assert commits[1].files == ("src/app/models.py",)


def test_git_log_reports_a_non_ascii_path_as_itself(git_repo: Path) -> None:
    """The far more common trigger, and the one that must be FIXED rather
    than filtered: git's `core.quotePath` defaults to true, which renders
    `café.py` as the literal seven-character-escape string
    `"caf\\\\303\\\\251.py"`. That contains backslashes, so `RepoRelativePath`
    rejected it and the whole analysis crashed on any repository with a
    non-ASCII filename in its history. `-c core.quotePath=false` emits the
    real path, which is also the path `iter_files` yields -- so churn for
    these files starts working rather than merely stopping the crash.
    """
    name = "café.py"
    (git_repo / name).write_text("x = 1\n")
    _git(git_repo, "add", "--", name)
    _git(git_repo, "commit", "-q", "-m", "add a non-ascii path")

    workspace = Workspace(root=git_repo)
    commits = workspace.git_log(limit=10)
    assert commits[0].files == (name,)
    assert name in [p.as_posix() for p in workspace.iter_files(".py")]
