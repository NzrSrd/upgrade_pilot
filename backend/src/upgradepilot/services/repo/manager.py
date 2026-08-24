"""Single entry point for repository access.

Callers pass a RepoRef and receive a Workspace. Nothing outside this module
needs to know whether the code was cloned or read in place -- Phase 2's
analyzer and Phase 9's API both call `WorkspaceManager` instead of importing
`clone.py` or `local.py` directly.
"""

import shutil
import time
from pathlib import Path

from upgradepilot.config import Settings
from upgradepilot.models.inputs import LocalRepoRef, RemoteRepoRef, RepoRef
from upgradepilot.services.repo.clone import clone_repository
from upgradepilot.services.repo.local import open_local_repository
from upgradepilot.services.repo.workspace import Workspace

OWNED_WORKSPACE_PREFIX = "repo-"
"""Must match the literal `clone_repository` (`services/repo/clone.py`) uses
to name a clone's destination directory (`f"repo-{uuid4().hex[:12]}"`).

`clone.py` does not export this as a constant, and this module does not
import from it either, so the two are not mechanically coupled -- see
`test_sweep_stale_prefix_matches_what_clone_repository_actually_produces` in
`test_workspace_manager.py`, which performs a real clone and asserts the
directory it actually produces is matched by this prefix. That test is the
enforcement: if `clone.py`'s naming ever changes, it fails immediately
instead of `sweep_stale` below silently matching nothing and workspaces
quietly accumulating on disk forever.
"""


class WorkspaceManager:
    """The single entry point through which all later code obtains a Workspace."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def open(self, ref: RepoRef) -> Workspace:
        """Resolve a RepoRef to a capped, ready-to-analyze Workspace.

        OWNERSHIP: the caller owns the returned Workspace and must close
        it -- preferably as `with manager.open(ref) as workspace:`, or
        failing that an explicit `workspace.cleanup()` in a `finally`.
        For a RemoteRepoRef that close is what removes the clone
        directory from disk; for a LocalRepoRef it is a documented no-op
        (`open_local_repository` builds the Workspace with
        `cleanup_dir=None`), so the `with` form is always the right
        default and never destroys a user's own checkout. A caller that
        forgets leaks a clone directory: nothing in this call path
        reclaims it later.

        `sweep_stale` below is the backstop for the one case documented
        ownership cannot cover -- a process that crashed or was killed
        before it could close a Workspace it had open. That is why
        `sweep_stale` exists at all; the two halves of this module are
        meant to be read together and neither is sufficient alone.

        There is deliberately NO `__del__` finalizer. Finalizer-driven
        `rmtree` would run at unpredictable times, possibly during
        interpreter shutdown, and would make a destructive operation
        happen implicitly. Documented ownership plus a startup sweep is
        the design.
        """
        if isinstance(ref, LocalRepoRef):
            workspace = open_local_repository(
                ref.path, allowed_roots=list(self._settings.allowed_local_roots)
            )
        elif isinstance(ref, RemoteRepoRef):
            workspace = clone_repository(
                ref.url,
                self._settings.workspace_dir,
                depth=self._settings.clone_depth,
                allowed_schemes=self._settings.allowed_url_schemes,
                # A `file://` URL is the same arbitrary-read surface a
                # LocalRepoRef is, so it is held to the same allowlist. Both
                # branches of this dispatch now pass the same roots.
                allowed_local_roots=list(self._settings.allowed_local_roots),
            )
        else:  # pragma: no cover - the union is closed
            raise TypeError(f"unsupported repository reference: {type(ref).__name__}")

        try:
            workspace.enforce_caps(
                max_files=self._settings.max_repo_files,
                max_bytes=self._settings.max_repo_bytes,
            )
        except Exception as failure:
            # Deliberately broad: enforce_caps can fail with either the
            # expected RepoTooLargeError or an OSError from stat() on a file
            # that vanished mid-walk, and the cleanup obligation below is
            # owed on *any* failure that reaches here, not just the
            # expected one. Nothing is swallowed by this -- rule 20 is
            # satisfied because the exception always propagates to the
            # caller unchanged; only cleanup runs first.
            #
            # workspace.cleanup() is a no-op for a LocalRepoRef's Workspace
            # (open_local_repository constructs it with cleanup_dir=None)
            # and removes the clone directory for a RemoteRepoRef's
            # Workspace. That asymmetry is load-bearing and lives in a
            # different module than this one: see
            # test_open_cap_rejection_never_deletes_a_local_checkout in
            # test_workspace_manager.py, which fails if a future change to
            # local.py ever makes local workspaces self-cleaning.
            try:
                workspace.cleanup()
            except Exception as cleanup_failure:
                # The original exception is the diagnostic one -- it says
                # *why* the workspace was rejected (typically
                # RepoTooLargeError) -- so it must reach the caller
                # unchanged rather than being displaced by a secondary
                # failure in the cleanup that followed it. The cleanup
                # failure is not swallowed either (rule 20): it is
                # attached to the original as a note, so it travels with
                # the traceback that gets logged. Only the exception's
                # type and message are recorded, and both come from this
                # process's own filesystem operations -- no untrusted or
                # unbounded external output reaches this string.
                failure.add_note(
                    "workspace cleanup after this failure also failed: "
                    f"{type(cleanup_failure).__name__}: {cleanup_failure}"
                )
            raise
        return workspace

    def sweep_stale(self, max_age_seconds: int) -> list[Path]:
        """Remove workspaces this service created and then abandoned.

        This is the backstop half of `open`'s ownership contract above:
        it reclaims workspaces a process crashed before closing. Every
        other leak is the caller's `with` block to prevent, not this
        method's to find.

        `max_age_seconds` must not be negative. A negative value puts the
        cutoff in the *future*, which makes every owned workspace count
        as stale -- including one created a second ago -- so the sweep
        would delete all of them. That is a programming error in the
        caller rather than user input, so it raises `ValueError`: an
        `AppError` would misrepresent a bug as an operating condition,
        and silently clamping to 0 would hide the caller's bug behind a
        destructive default.

        `0` is deliberately legal and means "remove every abandoned
        workspace regardless of age" -- a legitimate startup request
        after a crash. It is a real boundary, not an accident of the
        comparison below: with the cutoff at the current time, every
        existing owned directory has an earlier mtime and is removed.

        STARTUP-ONLY CONTRACT: call this once, at process startup, before
        any run has a workspace open -- never from a timer, a background
        scheduler, or a request handler. The cutoff is directory mtime,
        which tracks writes to a directory's own contents, not readers: a
        clone that finished being written twenty minutes ago and is still
        being read by an in-progress analysis has exactly the same mtime as
        one nobody is using any more. Wiring this to run mid-process would
        let it `rmtree` a workspace a running analyzer still has open, with
        no signal that anything went wrong until the analyzer's next read
        fails. The only mitigation is discipline about *when* this runs,
        which is why it is spelled out here rather than left to the "Run at
        startup" comment alone.

        Only directories matching `OWNED_WORKSPACE_PREFIX` are ever even
        considered for removal. This is a best-effort sweep --
        `ignore_errors=True`, because one unremovable directory must not
        abort the sweep of the rest -- but the returned list is a claim of
        what is genuinely gone: a path is appended only after confirming
        `rmtree` actually removed it, never merely because removal was
        attempted. A caller logging this return value, or an operator
        reading that log to explain reclaimed disk, must never be told
        about a removal that did not happen.
        """
        if max_age_seconds < 0:
            # Checked before the early return below so the rejection does
            # not depend on whether the workspace directory happens to
            # exist yet -- a caller passing a negative age has the same
            # bug either way.
            raise ValueError(
                "sweep_stale max_age_seconds must not be negative -- a negative "
                "age puts the cutoff in the future and would delete every owned "
                f"workspace regardless of age; got {max_age_seconds}"
            )

        root = self._settings.workspace_dir
        if not root.exists():
            return []

        cutoff = time.time() - max_age_seconds
        removed: list[Path] = []
        for entry in sorted(root.iterdir()):
            if not entry.is_dir() or not entry.name.startswith(OWNED_WORKSPACE_PREFIX):
                continue
            if entry.stat().st_mtime < cutoff:
                # A `repo-`-prefixed *symlink* pointing at a directory
                # reaches this line -- `Path.is_dir()` follows symlinks,
                # so the two conditions above do not exclude it -- and
                # survives untouched, target included. That safety comes
                # from `shutil.rmtree` itself, which refuses to operate
                # on a top-level symlink (it raises, and
                # `ignore_errors=True` absorbs the raise, after which the
                # `exists()` check below correctly declines to report it
                # as removed). Nothing this module wrote provides it.
                # Recorded here because a borrowed guarantee is exactly
                # what a future "optimisation" that swaps `rmtree` for
                # something else would silently drop.
                shutil.rmtree(entry, ignore_errors=True)
                if not entry.exists():
                    removed.append(entry)
        return removed
