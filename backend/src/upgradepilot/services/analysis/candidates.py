"""Choosing which files to parse, and parsing them.

Two phases, because one is not enough -- see the plan's Deviation 2.

  Phase A  byte-scan every .py file for the dependency's import root.
  Phase B  byte-scan the REMAINING files for the model class names phase A
           found, catching consumers that import a model from a first-party
           module and never name the dependency at all.

`ParsedModule` is a frozen dataclass rather than a HonestModel because it
carries an `ast.Module`, which Pydantic cannot validate and which would need
`arbitrary_types_allowed` -- a setting that would then apply to every field.
Frozen and slotted gives the immutability that matters here without that.

Which of `_parse`'s except branches are proven by a test, and which are not
(same ledger `services/repo/workspace.py` keeps for `HARDENED_GIT_ENV`):

- `UnicodeDecodeError`: proven --
  `test_a_file_that_is_not_utf8_is_skipped_with_a_decode_reason`.
- `SyntaxError`: proven --
  `test_the_unparseable_file_becomes_a_skipped_record_not_an_exception`,
  against the fixture's `broken.py.txt`.
- `OSError`: not proven. Reaching it hermetically needs a file that exists
  during `iter_files`'s walk but cannot be read moments later -- a
  permission bit flipped mid-run, or a TOCTOU deletion -- which is not
  something a fixture can force without weakening the sandbox or racing the
  filesystem. Kept anyway: a read genuinely can fail this way in
  production, and CLAUDE.md rule 20 requires the outcome be recorded rather
  than the exception left to propagate, regardless of whether a test can
  force the path.
- `ValueError`: not proven, and no reachable example is known on this
  project's pinned interpreter (3.14.5). The textbook trigger -- a NUL
  byte in the source -- does NOT reach this branch here: verified,
  `ast.parse("x = 1\\0")` raises `SyntaxError: source code string cannot
  contain null bytes` on 3.14, caught by the clause above instead. That
  NUL-bytes-raise-`ValueError` behaviour is stale knowledge from an older
  CPython; do not re-add it as the justification for this branch.
  `UnicodeEncodeError` is a real `ValueError` subclass `ast.parse` does
  raise (e.g. for a lone surrogate in the source), but it cannot arrive
  here either: `Workspace.read_text` decodes UTF-8 strictly, so a lone
  surrogate never survives decoding to reach `ast.parse` at all. The
  branch is kept for CLAUDE.md rule 20's defensive breadth -- `ast.parse`
  is not contractually limited to raising only `SyntaxError`, and this
  clause exists so that whatever else it might someday raise still
  becomes a recorded `SkippedFile` rather than an uncaught crash -- not
  because a specific input is known to trigger it today.
- `RecursionError`: not proven by a test, but genuinely reachable --
  verified, `ast.parse("x" + "+x" * 100000)` raises `RecursionError:
  Stack overflow (used 16352 kB) during compilation` on the pinned
  3.14.5 interpreter. (Deeply nested parentheses or `if` blocks do NOT
  reach this branch: verified, those raise `SyntaxError` and
  `IndentationError` respectively instead.) Left untested anyway: the
  trigger is a C stack-limit overflow, and the repeat count needed to
  hit it varies with platform, interpreter build and thread stack size,
  so a test pinned to a literal count would either pass here without
  truly exercising the branch, or hard-crash the interpreter on a
  machine with a smaller stack. A flaky or platform-dependent test is
  worse than a documented, unproven branch.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from upgradepilot.models.repo import SkippedFile

if TYPE_CHECKING:
    from upgradepilot.services.repo.workspace import Workspace


@dataclass(frozen=True, slots=True)
class ParsedModule:
    file: str
    """Repo-relative POSIX path. Becomes `UsageSite.file` verbatim."""
    dotted_module: str
    """Best-effort dotted name, e.g. `app.models` for `src/app/models.py`.
    Used only to match a first-party import against the module that defines a
    model.

    NOT injective, and nothing may key on it as though it were: the leading
    `src/` strip means `src/app/models.py` and `app/models.py` both land on
    `app.models`. Two places used to key a lookup dict directly on it
    (`(dotted_module, name)`), and both produced a wrong claim -- see
    `models_index.build_model_index`'s `found`/`visited` mapping and
    `usage._UsageVisitor.__init__`, which now select on `file` instead.

    Two lookups still key on the dotted name, deliberately, because an
    import names a module, never a file -- and they do NOT cost the same
    thing:

    - `ModelIndex.is_model_class` -- a call's receiver resolves to a dotted
      path, and `is_model_class` asks whether that path names an indexed
      class. A wrong or ambiguous match here only mis-grades that call's
      CONFIDENCE (medium vs low, via `_receiver_is_model`); it never
      affects the file, line, column or symbol that gets cited.
    - `models_index.build_model_index`'s `dotted_targets` set -- a
      transitive base resolving to a colliding dotted path DECIDES whether
      a class is indexed at all, which mints a new HIGH-confidence
      `MODEL_DEFINITION` citation (and any MEDIUM `METHOD_CALL` sites
      downstream of it). That citation's file, line and snippet are still
      correct; the claim that the class derives from the dependency can be
      wrong when the base actually names the OTHER file sharing this
      dotted module. `analyze_repository` reports the collision as a
      confidence reducer rather than resolving it -- see that function and
      `build_model_index`'s own comment on `dotted_targets`."""
    source: str
    tree: ast.Module


@dataclass(frozen=True, slots=True)
class CandidateScan:
    modules: tuple[ParsedModule, ...] = ()
    skipped: tuple[SkippedFile, ...] = ()
    total_python_files: int = 0
    scanned_files: tuple[str, ...] = ()
    """Every .py path phase A looked at, so phase B knows which are left
    without walking the workspace again."""


def _dotted_module(path: str) -> str:
    """Best-effort dotted name for a repo-relative POSIX path.

    Strips a single leading `src/` segment (and only that segment -- a
    repository laid out under `lib/` or with no src layout at all gets a
    dotted name that matches nothing real), drops the `.py` suffix, replaces
    `/` with `.`, and drops a trailing `.__init__` so a package's own
    `__init__.py` resolves to the package name rather than `pkg.__init__`.

    A wrong guess here only costs a missed medium-confidence grade in Task 7
    (a first-party import that could have been matched to its defining
    module is not), never a wrong citation: `ParsedModule.file` is what ends
    up in `UsageSite.file`, and this value is never used for that.
    """
    stripped = path.removeprefix("src/")
    without_suffix = stripped.removesuffix(".py")
    dotted = without_suffix.replace("/", ".")
    return dotted.removesuffix(".__init__")


def _parse(workspace: Workspace, relative: Path) -> ParsedModule | SkippedFile:
    path = relative.as_posix()
    try:
        source = workspace.read_text(relative)
    except UnicodeDecodeError as exc:
        return SkippedFile(path=path, reason=f"could not decode as UTF-8: {exc.reason}")
    except OSError as exc:
        return SkippedFile(path=path, reason=f"could not be read: {type(exc).__name__}")
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as exc:
        return SkippedFile(path=path, reason=f"syntax error at line {exc.lineno}: {exc.msg}")
    except (ValueError, RecursionError) as exc:
        return SkippedFile(path=path, reason=f"could not be parsed: {type(exc).__name__}")
    return ParsedModule(
        file=path,
        dotted_module=_dotted_module(path),
        source=source,
        tree=tree,
    )


def select_candidates(workspace: Workspace, *, import_root: str) -> CandidateScan:
    """Phase A: every `.py` file whose bytes contain `import_root`.

    A byte membership test, not a decode-then-search: an undecodable file
    costs one `bytes in bytes` check rather than a decode attempt, and a
    file that IS undecodable but still contains the marker becomes a
    `SkippedFile` (via `_parse`, which is the only place that decodes) --
    the honest record, rather than a silent miss.
    """
    needle = import_root.encode("utf-8")
    modules: list[ParsedModule] = []
    skipped: list[SkippedFile] = []
    scanned: list[str] = []
    total = 0

    for relative in workspace.iter_files(".py"):
        total += 1
        scanned.append(relative.as_posix())
        data = (workspace.root / relative).read_bytes()
        if needle not in data:
            continue
        parsed = _parse(workspace, relative)
        if isinstance(parsed, SkippedFile):
            skipped.append(parsed)
        else:
            modules.append(parsed)

    return CandidateScan(
        modules=tuple(sorted(modules, key=lambda m: m.file)),
        skipped=tuple(skipped),
        total_python_files=total,
        scanned_files=tuple(scanned),
    )


def expand_candidates(
    workspace: Workspace, scan: CandidateScan, *, model_names: frozenset[str]
) -> CandidateScan:
    """Phase B: among files phase A did not already select, byte-scan for any
    of `model_names` and parse the hits.

    Never re-scans a file already in `scan.modules` or `scan.skipped` --
    `scanned_files` records exactly what phase A looked at, so phase B walks
    that list rather than the workspace again, and every file it considers
    is skipped if it is already accounted for. That is what keeps a file
    from being parsed twice.
    """
    if not model_names:
        return scan

    needles = [name.encode("utf-8") for name in model_names]
    already_handled = {m.file for m in scan.modules} | {s.path for s in scan.skipped}

    new_modules: list[ParsedModule] = list(scan.modules)
    new_skipped: list[SkippedFile] = list(scan.skipped)

    for path in scan.scanned_files:
        if path in already_handled:
            continue
        relative = Path(path)
        data = (workspace.root / relative).read_bytes()
        if not any(needle in data for needle in needles):
            continue
        parsed = _parse(workspace, relative)
        if isinstance(parsed, SkippedFile):
            new_skipped.append(parsed)
        else:
            new_modules.append(parsed)

    return CandidateScan(
        modules=tuple(sorted(new_modules, key=lambda m: m.file)),
        skipped=tuple(new_skipped),
        total_python_files=scan.total_python_files,
        scanned_files=scan.scanned_files,
    )
