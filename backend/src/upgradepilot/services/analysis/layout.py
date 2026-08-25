"""Test-path recognition and repository language layout.

Both functions here are pure, path-string or file-listing logic -- no
parsing, no git. `is_test_path` and `corresponding_test_paths` feed Phase 6's
`test_coverage_of_affected` risk factor, which is why `is_test_path` matches
on whole path segments and whole filenames rather than a substring: see its
docstring for the false positive a substring match would cause.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from pydantic import ValidationError

from upgradepilot.models.errors import UpgradePilotError
from upgradepilot.models.repo import LanguageShare

if TYPE_CHECKING:
    from upgradepilot.services.repo.workspace import Workspace

_TEST_DIRECTORIES = frozenset({"tests", "test", "testing"})

_LANGUAGE_BY_SUFFIX: dict[str, str] = {
    ".py": "Python",
    ".pyi": "Python",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".md": "Markdown",
    ".toml": "Config",
    ".yaml": "Config",
    ".yml": "Config",
    ".json": "Config",
    ".sql": "SQL",
    ".sh": "Shell",
    ".html": "HTML",
    ".css": "CSS",
}
"""Deliberately small and honest: only extensions this table can name with
confidence. Anything else is unrecognised and excluded from
`language_shares`'s denominator -- see that function's docstring for why
that is what keeps `RepoAnalysis`'s shares-total-1.0 validator satisfiable."""


def is_test_path(path: str) -> bool:
    """Spec 7.1's path convention, matched on whole SEGMENTS and whole
    filenames -- never on a substring.

    `latest.py` and `src/contest.py` both contain "test". Grading them as
    tests inflates `test_coverage_of_affected`, and that factor lowers risk:
    a false positive here makes a dangerous upgrade read as well covered.
    """
    segments = path.split("/")
    if any(segment in _TEST_DIRECTORIES for segment in segments[:-1]):
        return True
    name = segments[-1]
    return name.startswith("test_") or name.endswith(("_test.py", "_test.pyi"))


def corresponding_test_paths(source: str, test_paths: Sequence[str]) -> tuple[str, ...]:
    """Every path in `test_paths` whose filename conventionally tests
    `source`: `test_<stem>.py` or `<stem>_test.py`, where `<stem>` is
    `source`'s filename without its suffix (`models` for
    `src/app/models.py`).

    Returns `()` when none match -- the honest answer. Phase 6 reads an
    empty tuple as "no locatable test", not as an error.
    """
    stem = PurePosixPath(source).stem
    wanted = {f"test_{stem}.py", f"{stem}_test.py"}
    matches = [path for path in test_paths if PurePosixPath(path).name in wanted]
    return tuple(sorted(matches))


def language_shares(workspace: Workspace) -> tuple[LanguageShare, ...]:
    """One `LanguageShare` per language recognised in `_LANGUAGE_BY_SUFFIX`,
    walking every file `workspace.iter_files("")` yields.

    The denominator is the count of RECOGNISED files, not every file in the
    repository: an unrecognised extension (a binary asset, a lockfile with
    no suffix, ...) is excluded from both the numerator and the denominator
    rather than bucketed into an "other" language. That is what makes the
    shares partition to exactly 1.0, which `RepoAnalysis`'s validator
    requires whenever the tuple is non-empty -- bucketing unrecognised files
    under a fabricated label would be citing a language nothing in the
    repository actually is (CLAUDE.md rule 1).

    Shares are the raw `count / total` quotient, never rounded: `LanguageShare
    .share` is `Field(gt=0.0, le=1.0)` and `RepoAnalysis` checks
    `math.fsum(shares)` against 1.0 at `abs_tol=1e-6`. Rounding a share down
    to 0.0 (a lone file in a repository of thousands) would violate the
    `gt=0.0` constraint on a purely cosmetic step; the unrounded quotient
    never does. Round only at the UI boundary, never here.

    Sorted by descending share, then by language name, so a tie between two
    languages with equal share is resolved the same way every run.
    Returns `()`, not a tuple of zero-shares, when no file has a recognised
    extension -- `LanguageShare.share` cannot be zero, and an empty tuple is
    the only honest answer when nothing was recognised.
    """
    counts: dict[str, int] = {}
    for path in workspace.iter_files(""):
        language = _LANGUAGE_BY_SUFFIX.get(path.suffix)
        if language is None:
            continue
        counts[language] = counts.get(language, 0) + 1

    total = sum(counts.values())
    if total == 0:
        return ()

    try:
        shares = [
            LanguageShare(language=language, share=count / total, file_count=count)
            for language, count in counts.items()
        ]
    except ValidationError as exc:
        # Reachable only by breaking the raw-quotient rule above -- rounding
        # a lone file's share down to 0.0 violates `gt=0.0`. See
        # `_require_shares_total_one` for why that becomes a recorded error
        # rather than a bare pydantic failure.
        raise UpgradePilotError(
            "This repository's language mix could not be computed reliably.",
            detail=f"LanguageShare rejected a computed share: {exc}",
        ) from exc

    return _require_shares_total_one(
        tuple(sorted(shares, key=lambda share: (-share.share, share.language)))
    )


def _require_shares_total_one(shares: tuple[LanguageShare, ...]) -> tuple[LanguageShare, ...]:
    """`shares` unchanged, or a recorded error if they do not partition 1.0.

    No repository can reach the error today: the shares are raw `count /
    total` quotients over at most a handful of languages, so `fsum` is 1.0 to
    well inside `abs_tol`. This guards a future EDIT, and what it changes is
    the failure MODE rather than whether there is a failure.

    Rounding the quotients -- which `language_shares`' docstring argues
    against and which nothing used to catch -- makes three equally common
    languages total 0.99. Without this check that surfaces as
    `RepoAnalysis`'s own validator raising "language shares must total 1.0"
    at assembly time, an unhandled `ValueError` from a model constructor
    several steps away from the arithmetic that caused it. `UpgradePilotError`
    is what CLAUDE.md rule 20 means by a recorded outcome instead: a
    comprehensible user-facing `message`, the arithmetic in `detail`, and
    Phase 4's node turning it into an `AppError` rather than a stack trace.

    Deliberately not a repair: renormalising a rounded set would satisfy
    `RepoAnalysis` while reporting shares no count in the repository
    supports.
    """
    total_share = math.fsum(share.share for share in shares)
    if not math.isclose(total_share, 1.0, abs_tol=1e-6):
        raise UpgradePilotError(
            "This repository's language mix could not be computed reliably.",
            detail=(
                f"language shares must partition 1.0, got {total_share!r} "
                f"over {len(shares)} languages"
            ),
        )
    return shares
