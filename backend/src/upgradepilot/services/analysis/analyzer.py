"""Repository analysis: Workspace + DependencySpec -> RepoAnalysis.

The only entry point Phase 4's graph node calls. Everything else in this
package is a pure function this one composes, which is why every module
below has its own tests and this one is tested for ASSEMBLY -- that the
pieces are wired to each other and to the right fields -- rather than for
the behaviour they already prove.

No LLM, no network, no graph. CLAUDE.md rule 19 has nothing to constrain
here because there is no model in this path at all.
"""

from __future__ import annotations

from upgradepilot.models.errors import RepoUnavailableError
from upgradepilot.models.inputs import DependencySpec
from upgradepilot.models.repo import AffectedFile, RepoAnalysis, SymbolInventory, UsageSite
from upgradepilot.services.analysis.candidates import expand_candidates, select_candidates
from upgradepilot.services.analysis.churn import ChurnIndex
from upgradepilot.services.analysis.imports import AliasMap
from upgradepilot.services.analysis.layout import is_test_path, language_shares
from upgradepilot.services.analysis.manifests import scan_manifests
from upgradepilot.services.analysis.models_index import build_model_index
from upgradepilot.services.analysis.usage import detect_usage
from upgradepilot.services.analysis.versions import resolve_version
from upgradepilot.services.repo.workspace import Workspace

MAX_EXPANSION_PASSES = 10
"""How many times candidate expansion and index rebuilding may alternate.

Each pass is one LINK of a first-party inheritance chain, not one module: a
single pass admits every remaining file naming any known model, so the count
bounds the chain's DEPTH. A depth above ten across separate modules is not a
shape real projects have, while the cost of a pass is a byte scan of the
unhandled files plus a full index rebuild -- and the analyzer's input is an
untrusted repository that must not be able to make it quadratic.

Convergence does not depend on this number: `expand_candidates` only ever
considers files in `scanned_files` that are not already handled, a finite
set that strictly shrinks whenever a pass adds anything, so the loop
terminates on its own. The cap bounds COST, and hitting it is reported as a
confidence reducer -- untrusted input must not be able to make the analyzer
either hang or claim a completeness it does not have.
"""


def analyze_repository(
    workspace: Workspace, dependency: DependencySpec, *, history_limit: int = 100
) -> RepoAnalysis:
    """Analyse `workspace` for its use of `dependency`.

    Raises `DependencyNotFoundError` (from `resolve_version`, uncaught) when
    no manifest in the repository declares `dependency` at all -- a run for a
    dependency the repository does not use has no honest output.
    """
    canonical = dependency.canonical_name
    import_root = dependency.import_root

    # -- Steps 2-3: which version is currently in use, if any. ---------------
    scan = scan_manifests(workspace, canonical)
    # Not caught: see the docstring above and versions.py's own docstring --
    # a dependency this repository does not declare anywhere has no current
    # version to report, and DependencyNotFoundError is the honest answer.
    detected = resolve_version(scan.declarations, canonical_name=canonical)

    # -- Steps 4-8: which files use it, and how. ------------------------------
    #
    # Expand-then-reindex to a FIXED POINT, not once. Rebuilding the index
    # over the expanded set is what discovers a model defined in a module
    # phase B admitted (a consumer that subclasses one) -- and those newly
    # discovered names are exactly what the NEXT expansion has to search
    # for. Expanding once truncated every chain longer than one link:
    # `Link0(BaseModel)` in one module, `Link1(Link0)` in a second and a
    # third module using `Link1` meant the third module was never searched
    # for, landing in neither `analyzed_files` nor `skipped_files` and
    # drawing no reducer, so the report claimed a completeness it did not
    # have. (`RepoAnalysis`'s `analyzed + skipped <= total` validator is
    # `<=`, so it does not catch that either.)
    #
    # Termination is the same argument `build_model_index`'s own fixed point
    # makes: `expand_candidates` only ever considers files in
    # `scanned_files` that are not already handled, a finite set that
    # strictly shrinks whenever a pass adds anything. `MAX_EXPANSION_PASSES`
    # therefore bounds COST rather than guaranteeing termination, and
    # hitting it is reported below rather than silently truncating.
    candidates = select_candidates(workspace, import_root=import_root)
    index = build_model_index(candidates.modules, import_root=import_root)
    expansion_converged = False
    for _ in range(MAX_EXPANSION_PASSES):
        expanded = expand_candidates(workspace, candidates, model_names=index.names())
        if len(expanded.modules) == len(candidates.modules) and len(expanded.skipped) == len(
            candidates.skipped
        ):
            expansion_converged = True
            break
        candidates = expanded
        index = build_model_index(candidates.modules, import_root=import_root)

    sites = [
        site
        for module in candidates.modules
        for site in detect_usage(module, import_root=import_root, index=index)
    ]

    # -- Step 9: commit history, degraded rather than aborted on corruption. -
    # RULING 31: `Workspace.git_log` has three outcomes, not two. No `.git`
    # or a real-but-commitless repository both return `[]` -- a legitimate
    # empty result `ChurnIndex.from_records` reads as `available=False`. A
    # *corrupted* repository (a `.git` that exists but cannot actually be
    # read) instead RAISES `RepoUnavailableError`. By this point steps 4-8
    # have already read and parsed the entire file tree successfully, so the
    # workspace is demonstrably usable -- this failure is broken git
    # METADATA, not an unavailable repository. Aborting the whole analysis
    # here would discard a complete, correct analysis of the code and hand
    # the user nothing; degrading to "no churn data" and recording why keeps
    # everything steps 4-8 already earned.
    #
    # This is a degrade, not an `AppError`: a service has no graph state to
    # put one in (CLAUDE.md rule 16 -- `services/` must not import
    # LangGraph). The confidence reducer appended below IS the in-model
    # record of what happened; Phase 4's graph node is what wires the actual
    # `AppError` and trace event around this call.
    history_unreadable = False
    try:
        records = tuple(workspace.git_log(limit=history_limit))
    except RepoUnavailableError:
        records = ()
        history_unreadable = True
    churn = ChurnIndex.from_records(records)

    # -- Step 10: test paths. --------------------------------------------------
    test_paths = tuple(
        sorted(p.as_posix() for p in workspace.iter_files(".py") if is_test_path(p.as_posix()))
    )

    # -- Step 11: group sites into AffectedFiles. -----------------------------
    grouped: dict[str, list[UsageSite]] = {}
    for site in sites:
        grouped.setdefault(site.file, []).append(site)

    affected_files: list[AffectedFile] = []
    for path in sorted(grouped):
        file_sites = grouped[path]
        entry = churn.for_path(path)
        # This three-way expression is the whole point of Task 1's
        # `int | None`: `0 if churn.available else None` is what keeps "we
        # read the history and this file is quiet" apart from "we never
        # read the history".
        commit_count = entry.commit_count if entry is not None else (0 if churn.available else None)
        affected_files.append(
            AffectedFile.from_sites(
                path,
                file_sites,
                is_test=is_test_path(path),
                commit_count=commit_count,
                last_modified=entry.last_modified if entry is not None else None,
            )
        )

    # -- Step 12: symbol inventory. -------------------------------------------
    inventory = SymbolInventory.from_sites(sites)

    # -- Step 13: confidence reducers, in a fixed order for determinism. -----
    confidence_reducers: list[str] = []

    if (workspace.root / ".gitmodules").exists():
        confidence_reducers.append(
            "This repository uses git submodules. Submodule contents are not "
            "cloned and were not analysed, so code living in them is invisible "
            "to this report."
        )

    # F3: a path the citation type cannot represent. `back\slash.py` is a
    # legal POSIX filename and `RepoRelativePath` refuses it deliberately (a
    # backslash separates paths on some platforms and names files on others,
    # so a reference to it cannot be resolved). `Workspace.iter_files`
    # excludes such a file so it never reaches a model constructor, and this
    # is where that exclusion is recorded -- CLAUDE.md rule 20: the outcome
    # is in the model, not an exception out of a run that had already
    # analysed the rest of the tree.
    #
    # Not a `SkippedFile`: that model's own `path` field is
    # `RepoRelativePath` too, so the record could not name the file either.
    # `!r` rather than the raw path, because the raw bytes are untrusted
    # third-party input going into a user-facing sentence -- `repr` renders
    # a newline or a control character visibly instead of letting it break
    # the line.
    #
    # Placed second, immediately after submodules: both are repository-wide
    # "code exists that this report did not read", which is the broadest
    # scope in the fixed order below.
    uncitable = workspace.uncitable_files()
    if uncitable:
        confidence_reducers.append(
            f"{len(uncitable)} file(s) in this repository were not analysed "
            f"because their names cannot be cited: a citation must be a "
            f"repository-relative path, and these are not representable as "
            f"one (a name containing a backslash, for example, is a path "
            f"separator on some platforms and an ordinary character on "
            f"others). Their contents are invisible to this report: "
            + ", ".join(repr(path) for path in uncitable)
            + "."
        )

    if not candidates.modules:
        confidence_reducers.append(
            f"No file in this repository names the module {import_root!r}. The "
            f"import name was inferred from the distribution name "
            f"{dependency.name!r} and the two differ for some distributions, "
            f"so this may mean the dependency was not found rather than that "
            f"it is unused."
        )

    # F5: candidate expansion did not reach a fixed point within
    # `MAX_EXPANSION_PASSES`. Hitting the cap means convergence was not
    # PROVEN, not that anything was actually truncated: a pass that adds
    # nothing is what sets `expansion_converged = True`, so exhausting the
    # loop always means the LAST pass was still productive, and whether the
    # next one would have been too is genuinely unknown. At a chain depth of
    # exactly `MAX_EXPANSION_PASSES` every module is in fact examined and the
    # report is complete -- asserting a gap that does not exist there is the
    # same class of rule-1 defect as a finding that does not exist (final
    # fix round 2, finding 1). Hedged accordingly: "may not have been
    # examined", never "were not examined". Reported rather than silently
    # truncated either way: untrusted input must not be able to make the
    # analyzer claim a completeness it does not have, in either direction.
    if not expansion_converged:
        confidence_reducers.append(
            f"This repository's first-party model inheritance chain is deeper "
            f"than the {MAX_EXPANSION_PASSES} discovery passes this analysis "
            f"budgets for, so candidate selection did not converge within "
            f"that budget: completeness beyond this depth is unproven, not "
            f"disproven. Modules that use only the deepest links of that "
            f"chain may not have been examined, and usage in them may be "
            f"missing from this report."
        )

    # RULING 17: `resolve_version` returns None -- not raises -- when the
    # dependency IS declared but the declaration pins neither a version nor a
    # specifier (e.g. `dependencies = ["pydantic"]`). Silently assigning None
    # to `detected_version` would report "no version detected" with no
    # traceable reason. Named here as its own reducer: which manifest, among
    # those that declare this dependency, pins nothing -- the alphabetically
    # first by path when more than one qualifies, which is deterministic and
    # matches how `scan.declarations` is already sorted.
    if detected is None:
        unconstrained = [d for d in scan.declarations if d.version is None and d.specifier is None]
        if unconstrained:
            manifest_path = unconstrained[0].manifest.path
            confidence_reducers.append(
                f"{dependency.name!r} is declared in {manifest_path} without a "
                f"version or version constraint, so the current version could "
                f"not be determined from this repository. The upgrade "
                f"analysis proceeds from the target version alone."
            )

    # RULING 31: history could not be read at all (corrupted `.git`), as
    # opposed to a real repository with no commits yet (which reads as
    # `churn.available is False` with no reducer -- an empty history is not
    # a degraded analysis).
    if history_unreadable:
        confidence_reducers.append(
            "This repository's git history could not be read, so commit "
            "frequency and last-modified data are unavailable for every file "
            "in this report."
        )

    if scan.unreadable:
        confidence_reducers.append(
            "The following manifests exist but could not be parsed, so any "
            "version or dependency information they might contain is missing "
            "from this report: " + ", ".join(scan.unreadable) + "."
        )

    # RULING 64: `from <import_root> import *` binds names this module
    # cannot enumerate without importing the dependency (a static analyzer
    # must not do that), so real usage in that module can be silently
    # missed -- the same "we could not find it" failure the no-candidates
    # reducer above guards against, one file at a time. Placed LAST: the
    # five reducers above run repository-wide -> analysis-coverage ->
    # manifest-level in scope, and this one is the narrowest, a per-module
    # completeness caveat.
    #
    # `detect_usage` already builds an `AliasMap` per module internally,
    # but `_UsageVisitor` does not expose it, so reaching into it would mean
    # changing usage.py's public surface for this one flag. Building one
    # more `AliasMap.from_module(module.tree)` here is cheap instead: the
    # modules are already parsed, so this costs one extra `ast.walk` per
    # module, not a second parse of the file.
    star_import_files = sorted(
        module.file
        for module in candidates.modules
        if AliasMap.from_module(module.tree).has_star_import_from(import_root)
    )
    if star_import_files:
        confidence_reducers.append(
            f"The following modules use `from {import_root} import *`, whose "
            f"bound names cannot be enumerated without importing the "
            f"dependency: {', '.join(star_import_files)}. Usage in these "
            f"files may be under-reported."
        )

    # -- Step 14: assemble. ----------------------------------------------------
    return RepoAnalysis(
        commit_sha=workspace.commit_sha,
        languages=language_shares(workspace),
        manifests=scan.manifests,
        detected_version=detected,
        total_python_files=candidates.total_python_files,
        analyzed_files=len(candidates.modules),
        skipped_files=candidates.skipped,
        affected_files=tuple(affected_files),
        symbol_inventory=inventory,
        commit_records=records,
        test_paths=test_paths,
        confidence_reducers=tuple(confidence_reducers),
    )
