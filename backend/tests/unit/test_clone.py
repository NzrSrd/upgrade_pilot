import json
import subprocess
import types
from pathlib import Path
from urllib.parse import quote

import pytest

from upgradepilot.models.errors import (
    InvalidRepoUrlError,
    LocalPathForbiddenError,
    RepoUnavailableError,
    UpgradePilotError,
)
from upgradepilot.services.repo import clone as clone_module
from upgradepilot.services.repo.clone import clone_repository
from upgradepilot.services.repo.local import open_local_repository

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
        allowed_local_roots=[tmp_path],
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
        f"file://{origin}",
        tmp_path / "workspaces",
        depth=10,
        allowed_schemes=FILE_SCHEME,
        allowed_local_roots=[tmp_path],
    )
    try:
        assert len(workspace.git_log(limit=10)) == 3
    finally:
        workspace.cleanup()


def test_clone_respects_the_requested_depth(origin: Path, tmp_path: Path) -> None:
    workspace = clone_repository(
        f"file://{origin}",
        tmp_path / "workspaces",
        depth=1,
        allowed_schemes=FILE_SCHEME,
        allowed_local_roots=[tmp_path],
    )
    try:
        assert len(workspace.git_log(limit=10)) == 1
    finally:
        workspace.cleanup()


def test_cleanup_removes_the_cloned_directory(origin: Path, tmp_path: Path) -> None:
    workspace = clone_repository(
        f"file://{origin}",
        tmp_path / "workspaces",
        depth=5,
        allowed_schemes=FILE_SCHEME,
        allowed_local_roots=[tmp_path],
    )
    root = workspace.root
    assert root.exists()
    workspace.cleanup()
    assert not root.exists()


def test_context_manager_cleans_up(origin: Path, tmp_path: Path) -> None:
    with clone_repository(
        f"file://{origin}",
        tmp_path / "workspaces",
        depth=5,
        allowed_schemes=FILE_SCHEME,
        allowed_local_roots=[tmp_path],
    ) as workspace:
        root = workspace.root
        assert root.exists()
    assert not root.exists()


@pytest.fixture
def not_a_repository(tmp_path: Path) -> Path:
    """An existing, allowlisted directory that is not a git repository.

    This is what "a clone git itself rejects" now has to look like. A
    *missing* path no longer reaches git at all: `clone_repository` routes a
    `file://` target through `resolve_local_path` (the same door a
    LocalRepoRef uses), which rejects a nonexistent path before any
    subprocess is spawned. Pointing these tests at an existing non-repository
    keeps them exercising git's own failure, which is what they were written
    to check; the earlier rejection has its own test in the door-parity
    block below.
    """
    directory = tmp_path / "not-a-repo"
    directory.mkdir()
    return directory


def test_a_clone_git_rejects_raises_repo_unavailable(
    not_a_repository: Path, tmp_path: Path
) -> None:
    with pytest.raises(RepoUnavailableError) as excinfo:
        clone_repository(
            f"file://{not_a_repository}",
            tmp_path / "workspaces",
            depth=5,
            allowed_schemes=FILE_SCHEME,
            allowed_local_roots=[tmp_path],
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
            allowed_local_roots=[tmp_path],
        )
    # Proves ordering, not merely that an error was raised: if a subprocess
    # had been spawned, `dest_parent` (and possibly a partial clone
    # directory under it) would exist on disk.
    assert not workspaces.exists()


def test_failed_clone_leaves_no_partial_directory(not_a_repository: Path, tmp_path: Path) -> None:
    workspaces = tmp_path / "workspaces"
    with pytest.raises(RepoUnavailableError):
        clone_repository(
            f"file://{not_a_repository}",
            workspaces,
            depth=5,
            allowed_schemes=FILE_SCHEME,
            allowed_local_roots=[tmp_path],
        )
    leftovers = list(workspaces.iterdir()) if workspaces.exists() else []
    assert leftovers == []


def test_each_clone_gets_its_own_directory(origin: Path, tmp_path: Path) -> None:
    first = clone_repository(
        f"file://{origin}",
        tmp_path / "workspaces",
        depth=2,
        allowed_schemes=FILE_SCHEME,
        allowed_local_roots=[tmp_path],
    )
    second = clone_repository(
        f"file://{origin}",
        tmp_path / "workspaces",
        depth=2,
        allowed_schemes=FILE_SCHEME,
        allowed_local_roots=[tmp_path],
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
        f"file://{origin}",
        tmp_path / "workspaces",
        depth=depth,
        allowed_schemes=FILE_SCHEME,
        allowed_local_roots=[tmp_path],
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
    not_a_repository: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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
            f"file://{not_a_repository}",
            tmp_path / "workspaces",
            depth=5,
            allowed_schemes=FILE_SCHEME,
            allowed_local_roots=[tmp_path],
        )

    assert len(created) == 1, "the fake git clone must have run exactly once"
    assert not created[0].exists(), "rmtree on the non-zero-exit branch must remove destination"


def test_timed_out_clone_removes_a_partially_created_destination(
    not_a_repository: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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
            f"file://{not_a_repository}",
            tmp_path / "workspaces",
            depth=5,
            allowed_schemes=FILE_SCHEME,
            allowed_local_roots=[tmp_path],
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
            allowed_local_roots=[tmp_path],
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
    patched_env = dict(clone_module.HARDENED_GIT_ENV)
    patched_env["HOME"] = str(fake_home)
    monkeypatch.setattr(clone_module, "HARDENED_GIT_ENV", patched_env)

    workspace = clone_repository(
        f"file://{origin}",
        tmp_path / "workspaces",
        depth=5,
        allowed_schemes=FILE_SCHEME,
        allowed_local_roots=[tmp_path],
    )
    try:
        assert workspace.commit_sha is not None
        files = sorted(str(p) for p in workspace.iter_files(".py"))
        assert files == ["src/mod0.py", "src/mod1.py", "src/mod2.py"]
    finally:
        workspace.cleanup()


# --- The two doors into the local filesystem give the same answer (item 2) --


def _door_one(target: Path, roots: list[Path]) -> str:
    """`LocalRepoRef`'s route: `open_local_repository` -> `resolve_local_path`."""
    try:
        workspace = open_local_repository(str(target), allowed_roots=roots)
    except UpgradePilotError as error:
        return f"denied:{type(error).__name__}:{error.message}"
    workspace.cleanup()
    return "granted"


def _door_two(target: Path, roots: list[Path], dest_parent: Path) -> str:
    """`RemoteRepoRef`'s route for a `file://` URL: `clone_repository`."""
    try:
        workspace = clone_repository(
            f"file://{target}",
            dest_parent,
            depth=1,
            allowed_schemes=FILE_SCHEME,
            allowed_local_roots=roots,
        )
    except UpgradePilotError as error:
        return f"denied:{type(error).__name__}:{error.message}"
    workspace.cleanup()
    return "granted"


@pytest.mark.parametrize(
    "case",
    [
        "no_roots_configured",
        "target_outside_every_root",
        "target_inside_a_root",
        "target_does_not_exist",
        "target_is_a_file_not_a_directory",
        "target_is_a_symlink_escaping_the_root",
        "misconfigured_relative_root",
    ],
)
def test_both_doors_to_the_local_filesystem_give_the_same_answer(
    case: str, origin: Path, tmp_path: Path
) -> None:
    """The invariant, written as the invariant, because the mechanism is
    implementation detail and this is what must never regress.

    There were two doors into the local filesystem with different locks. A
    `LocalRepoRef` went through `resolve_local_path` and obeyed
    `allowed_local_roots`; a `file://` `RemoteRepoRef` went through
    `validate_clone_url`, which knows nothing about roots. Reproduced with
    `allowed_local_roots=()`: door one was denied ("Local repository
    analysis is not enabled on this server.") while door two cloned a
    directory outside every root and read a secret out of it. `file` is not
    in the shipped default scheme allowlist, so an operator had to enable
    it -- but enabling a scheme should not silently confer *unbounded*
    filesystem access from a server whose setting is named
    `allowed_local_roots`.

    Parametrised over every way the answer can differ, not just
    containment: an empty allowlist, a target outside it, a target inside
    it, a missing target, a file where a directory is required, a symlink
    escaping the root, and a misconfigured allowlist entry. Both doors must
    agree on all of them, including agreeing to succeed.
    """
    outside = tmp_path.parent / "wave-b-outside-every-root"
    outside.mkdir(exist_ok=True)
    a_file = tmp_path / "a-file.txt"
    a_file.write_text("not a directory\n")
    escape_target = tmp_path.parent / "wave-b-escape-target"
    escape_target.mkdir(exist_ok=True)
    contained_root = tmp_path / "contained"
    contained_root.mkdir()
    escape_link = contained_root / "escape"
    if not escape_link.exists():
        escape_link.symlink_to(escape_target, target_is_directory=True)

    targets_and_roots: dict[str, tuple[Path, list[Path]]] = {
        "no_roots_configured": (origin, []),
        "target_outside_every_root": (outside, [tmp_path]),
        "target_inside_a_root": (origin, [tmp_path]),
        "target_does_not_exist": (tmp_path / "nowhere", [tmp_path]),
        "target_is_a_file_not_a_directory": (a_file, [tmp_path]),
        "target_is_a_symlink_escaping_the_root": (escape_link, [contained_root]),
        "misconfigured_relative_root": (origin, [Path("relative/root")]),
    }
    target, roots = targets_and_roots[case]

    door_one = _door_one(target, roots)
    door_two = _door_two(target, roots, tmp_path / "workspaces")

    assert door_one == door_two, (
        f"the two doors disagree about {target}: LocalRepoRef said {door_one!r}, "
        f"file:// clone said {door_two!r}"
    )


def test_a_file_url_outside_the_allowed_roots_reads_nothing(origin: Path, tmp_path: Path) -> None:
    """Not merely "the same answer" but the right one, and no side effect.

    The parity test above would still pass if both doors granted access, so
    this pins the direction: with roots that do not contain the target, the
    clone is refused and nothing is written to the workspace directory --
    the rejection happens before any subprocess is spawned, exactly as the
    disallowed-scheme rejection does.
    """
    workspaces = tmp_path / "workspaces"
    with pytest.raises(LocalPathForbiddenError):
        clone_repository(
            f"file://{origin}",
            workspaces,
            depth=1,
            allowed_schemes=FILE_SCHEME,
            allowed_local_roots=[tmp_path / "some-other-place"],
        )
    assert not workspaces.exists()


def test_a_file_url_naming_a_host_is_refused(origin: Path, tmp_path: Path) -> None:
    """git ignores a `file://` URL's host entirely -- verified against git
    2.50.1, `git clone file://otherhost/path` clones the LOCAL `/path` and
    exits 0 without contacting `otherhost`. A URL that reads as remote must
    not quietly read the server's own disk, so the host is refused rather
    than silently dropped."""
    with pytest.raises(InvalidRepoUrlError) as excinfo:
        clone_repository(
            f"file://evil.example.com{origin}",
            tmp_path / "workspaces",
            depth=1,
            allowed_schemes=FILE_SCHEME,
            allowed_local_roots=[tmp_path],
        )
    assert "host" in (excinfo.value.detail or "")


@pytest.mark.parametrize("directory_name", ["with space", "with%20percent", "plain"])
def test_a_percent_escape_is_decoded_for_the_check_and_re_encoded_for_git(
    directory_name: str, tmp_path: Path
) -> None:
    """The parser differential closed while fixing item 2.

    git percent-decodes a `file://` path (verified: cloning
    `file://<dir>/a%20b` reads the directory `a b`). So validating the raw
    URL text and handing that same text to git would check one path and
    read another whenever an escape is present. `_resolve_file_url` decodes
    before the allowlist check and re-encodes the *resolved* path
    afterwards, which makes the string git receives a faithful encoding of
    the exact directory that was allowlisted.

    `with%20percent` is the case that catches a one-way fix: its name
    really contains a `%`, so the re-encoding must produce `%2520` for git
    to arrive back at the right directory.
    """
    source = tmp_path / directory_name
    (source / "src").mkdir(parents=True)
    _git(source, "init", "-q", "-b", "main")
    (source / "src" / "mod.py").write_text("value = 1\n")
    _git(source, "add", ".")
    _git(source, "commit", "-q", "-m", "initial")

    url = f"file://{quote(str(source))}"
    workspace = clone_repository(
        url,
        tmp_path / "workspaces",
        depth=1,
        allowed_schemes=FILE_SCHEME,
        allowed_local_roots=[tmp_path],
    )
    try:
        assert sorted(str(p) for p in workspace.iter_files(".py")) == ["src/mod.py"]
    finally:
        workspace.cleanup()


# --- The stderr truncation has teeth (item 5.3) --


def test_a_hostile_remote_cannot_flood_the_logged_detail(
    not_a_repository: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deleting the `[-_STDERR_DETAIL_BUDGET:]` slice turned zero tests red,
    even though the constant's own docstring says a hostile or merely broken
    remote can emit megabytes into a logged `detail`. No real test origin
    produces more than a line or two of stderr, so the truncation was never
    exercised: the fake below emits 200,000 characters, which is what the
    docstring is actually about.

    The tail is what must survive -- the last lines are the ones most
    likely to name the real failure -- so both halves are asserted: the
    detail is bounded, and the end of the flood is the part that is kept.
    """

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        flood = "X" * 200_000 + "fatal: THE-ACTUAL-REASON"
        return subprocess.CompletedProcess(command, returncode=128, stdout="", stderr=flood)

    monkeypatch.setattr(clone_module, "subprocess", _make_fake_subprocess_module(fake_run))

    with pytest.raises(RepoUnavailableError) as excinfo:
        clone_repository(
            f"file://{not_a_repository}",
            tmp_path / "workspaces",
            depth=1,
            allowed_schemes=FILE_SCHEME,
            allowed_local_roots=[tmp_path],
        )

    detail = excinfo.value.detail or ""
    assert len(detail) < 1000, f"detail is not bounded: {len(detail)} characters"
    assert detail.endswith("fatal: THE-ACTUAL-REASON"), "the tail must be the part kept"


# --- The surrogate class reaches subprocess (wave B residual, item 1) --


def test_a_lone_surrogate_in_a_clone_url_raises_an_app_error_not_a_unicode_crash(
    tmp_path: Path,
) -> None:
    """The blocking crash, end to end, at the door an HTTP request enters.

    `validate_clone_url` accepted this URL -- a lone surrogate is category
    `Cs`, which was not in the disallowed set -- and `subprocess.run` then
    died encoding it for `execve`, raising a bare `UnicodeEncodeError`: a
    non-`UpgradePilotError` escaping the boundary, on input a JSON body
    produces for free.

    The guards test file asserts the rule; this asserts the consequence, at
    the function that actually crashed, because the two are separable and
    only one of them was reachable over HTTP.
    """
    assert json.loads('"\\ud800"') == "\ud800"
    destination = tmp_path / "workspaces"

    with pytest.raises(InvalidRepoUrlError):
        clone_repository(
            "https://github.com/acme/\ud800repo",
            destination,
            depth=1,
            allowed_schemes=frozenset({"https"}),
            allowed_local_roots=[tmp_path],
        )

    assert not destination.exists(), "a refused URL must leave nothing on disk"


def test_a_surrogate_in_a_file_url_is_refused_before_it_reaches_the_local_door(
    tmp_path: Path,
) -> None:
    """The `file://` route to the same crash, held to the same answer.

    This is the door that was fixed one side at a time twice already, so it
    gets its own assertion rather than an argument that the shared guard
    must cover it.
    """
    with pytest.raises(InvalidRepoUrlError):
        clone_repository(
            f"file://{tmp_path}/a\udc80b",
            tmp_path / "workspaces",
            depth=1,
            allowed_schemes=FILE_SCHEME,
            allowed_local_roots=[tmp_path],
        )
