"""One real https clone, closing the URL half of Phase 1's exit criterion.

That criterion is "a public URL and a local path both produce a `Workspace`".
The local half is covered hermetically. The URL half was not: every clone
test in `tests/unit/test_clone.py` uses a `file://` origin, and `file` is not
in the shipped `allowed_url_schemes` (`https`, `git`). So the whole suite
could pass while cloning a real public repository was broken -- a different
instance of exactly the gap `@pytest.mark.live` exists for, and the same
reason spec §11 layer 6 keeps one real LLM call.

Opt-in, so the default suite stays hermetic and offline: `pytest` skips this,
`pytest --live` runs it. This is the only test in the suite that reaches the
network without needing an API key.
"""

from pathlib import Path

import pytest

from upgradepilot.services.repo.clone import clone_repository
from upgradepilot.services.repo.workspace import Workspace

pytestmark = pytest.mark.live

HTTPS_SCHEME = frozenset({"https"})

PUBLIC_REPO_URL = "https://github.com/octocat/Hello-World.git"
"""GitHub's own long-lived sample repository: a handful of commits, a couple
of small files, no submodules, no LFS. Chosen for size and stability -- this
test is about the transport and the guards, not about repository content, so
the cheapest real remote that exercises them is the right one."""

NO_LOCAL_ROOTS: tuple[Path, ...] = ()
"""`allowed_local_roots` is required but consulted only for a `file://` URL.
Empty is the honest policy for an https clone: this call is permitted to
reach a remote and permitted to read nothing on the local filesystem."""


@pytest.fixture
def workspace(tmp_path: Path) -> object:
    """Clone the public repository, and always clean up after the test."""
    cloned = clone_repository(
        PUBLIC_REPO_URL,
        tmp_path / "workspaces",
        depth=10,
        allowed_schemes=HTTPS_SCHEME,
        allowed_local_roots=NO_LOCAL_ROOTS,
    )
    try:
        yield cloned
    finally:
        cloned.cleanup()


def test_a_public_https_url_produces_a_readable_workspace(workspace: Workspace) -> None:
    """The exit criterion itself: a public URL yields a Workspace to read.

    Asserts the clone is real rather than merely that the call returned:
    a resolved commit SHA, an existing root, and at least one file whose
    bytes can actually be read back through the Workspace API.
    """
    assert workspace.root.is_dir()
    assert workspace.commit_sha, "a real clone must resolve a HEAD commit"

    files = sorted(workspace.iter_files(""))
    assert files, "the clone produced no readable files"
    assert workspace.read_text(files[0]) is not None


def test_the_clone_carries_history_for_the_churn_signal(workspace: Workspace) -> None:
    """Depth is not 1, so `git log` has something to say.

    ADR-001 D5 chose depth 100 over depth 1 specifically because churn
    signals need history, and Phase 2 reads that history through `git_log`.
    A depth-1 clone would satisfy the test above and still destroy this.
    """
    commits = workspace.git_log(limit=10)

    assert commits, "git_log returned nothing from a real clone"
    assert commits[0].sha


def test_the_workspace_is_removed_on_cleanup(tmp_path: Path) -> None:
    """A cloned workspace owns its directory and must delete it.

    Not covered by the fixture above, which cleans up after the assertions
    have already run. Uses the context manager, which is how callers get
    this guarantee without a `finally`.
    """
    with clone_repository(
        PUBLIC_REPO_URL,
        tmp_path / "workspaces",
        depth=1,
        allowed_schemes=HTTPS_SCHEME,
        allowed_local_roots=NO_LOCAL_ROOTS,
    ) as cloned:
        root = cloned.root
        assert root.is_dir()

    assert not root.exists(), "a cloned workspace must delete its own directory"
