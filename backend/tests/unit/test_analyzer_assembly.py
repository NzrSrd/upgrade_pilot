"""Assembly tests for `analyze_repository`.

Every function this composes has its own tests already, so these are tested
for ASSEMBLY -- that the pieces are wired to each other and to the right
fields on `RepoAnalysis` -- not for the behaviour those functions already
prove themselves.
"""

import shutil
from pathlib import Path

import pytest

from tests.fixtures.repo_builder import (
    EXPECTED_PINNED_VERSION,
    EXPECTED_PYTHON_FILES,
    EXPECTED_UNPARSEABLE,
    build_sample_repo,
)
from upgradepilot.models.enums import Confidence, UsageKind
from upgradepilot.models.errors import DependencyNotFoundError
from upgradepilot.models.inputs import DependencySpec
from upgradepilot.models.repo import RepoAnalysis
from upgradepilot.services.analysis.analyzer import MAX_EXPANSION_PASSES, analyze_repository
from upgradepilot.services.repo.workspace import Workspace


def _analysis(tmp_path: Path, **overrides: int) -> RepoAnalysis:
    workspace = Workspace(build_sample_repo(tmp_path))
    spec = DependencySpec(name="pydantic", current_version="1.10.13", target_version="2.9.0")
    return analyze_repository(workspace, spec, **overrides)


def test_counts_are_internally_consistent(tmp_path: Path) -> None:
    """RepoAnalysis's own validator enforces `analyzed + skipped <= total`,
    so constructing it at all proves that much. This asserts the stronger
    property that makes the counts meaningful: the analyzer looked at fewer
    files than exist, and at more than none."""
    analysis = _analysis(tmp_path)
    assert 0 < analysis.analyzed_files < analysis.total_python_files
    assert analysis.total_python_files == EXPECTED_PYTHON_FILES


def test_the_unparseable_file_is_reported_not_swallowed(tmp_path: Path) -> None:
    analysis = _analysis(tmp_path)
    assert EXPECTED_UNPARSEABLE in {s.path for s in analysis.skipped_files}
    assert analysis.skipped_ratio > 0.0


def test_every_affected_file_path_appears_in_the_repository(tmp_path: Path) -> None:
    """CLAUDE.md rule 1, asserted rather than asserted-about: every path this
    analysis cites must resolve to a file that exists in the tree analyzed."""
    root = build_sample_repo(tmp_path)
    workspace = Workspace(root)
    spec = DependencySpec(name="pydantic", current_version="1.10.13", target_version="2.9.0")
    analysis = analyze_repository(workspace, spec)
    for affected in analysis.affected_files:
        assert (root / affected.path).is_file(), affected.path
        for site in affected.usage_sites:
            lines = (root / site.file).read_text(encoding="utf-8").splitlines()
            assert 1 <= site.line <= len(lines)
            assert lines[site.line - 1] == site.snippet


def test_churn_reaches_the_affected_files(tmp_path: Path) -> None:
    analysis = _analysis(tmp_path)
    models = next(a for a in analysis.affected_files if a.path == "src/app/models.py")
    assert models.commit_count == 2
    assert models.last_modified is not None
    assert models.last_modified.tzinfo is not None


def test_commit_count_is_None_when_the_repository_has_no_history(tmp_path: Path) -> None:
    """Task 1's three-state `commit_count`, end to end. A directory with no
    `.git` is a legitimate LocalRepoRef -- a user analysing an unpacked
    tarball -- and it must read as "churn unknown", not "churn zero"."""
    root = build_sample_repo(tmp_path)
    shutil.rmtree(root / ".git")
    spec = DependencySpec(name="pydantic", current_version="1.10.13", target_version="2.9.0")
    analysis = analyze_repository(Workspace(root), spec)
    assert analysis.affected_files
    assert all(a.commit_count is None for a in analysis.affected_files)


def test_a_dependency_the_repository_does_not_declare_raises(tmp_path: Path) -> None:
    workspace = Workspace(build_sample_repo(tmp_path))
    spec = DependencySpec(name="numpy", current_version="1.0", target_version="2.0")
    with pytest.raises(DependencyNotFoundError):
        analyze_repository(workspace, spec)


def test_gitmodules_becomes_a_confidence_reducer_not_a_skipped_file(tmp_path: Path) -> None:
    """`git clone` does not fetch submodule content. A repository whose real
    code lives in submodules analyses as nearly empty and would report LOW
    risk having never seen the code -- the carry-in `PLANNING.md` records.

    It must not be a SkippedFile: `skipped_ratio` divides by
    `total_python_files`, and a non-Python entry there corrupts the
    analysis_coverage factor and can trip RepoAnalysis's own validator.
    """
    root = build_sample_repo(tmp_path)
    (root / ".gitmodules").write_text(
        '[submodule "vendor/lib"]\n\tpath = vendor/lib\n\turl = https://example.invalid/lib\n',
        encoding="utf-8",
    )
    spec = DependencySpec(name="pydantic", current_version="1.10.13", target_version="2.9.0")
    analysis = analyze_repository(Workspace(root), spec)

    assert any("submodule" in reducer.lower() for reducer in analysis.confidence_reducers)
    assert ".gitmodules" not in {s.path for s in analysis.skipped_files}


def test_no_gitmodules_means_no_submodule_reducer(tmp_path: Path) -> None:
    """The negative direction. Without it, an implementation that appends the
    reducer unconditionally passes the test above."""
    analysis = _analysis(tmp_path)
    assert not any("submodule" in r.lower() for r in analysis.confidence_reducers)


def test_finding_no_candidate_at_all_is_reported_as_a_reducer(tmp_path: Path) -> None:
    """The `import_root` guess. `DependencySpec.import_root` for
    `python-dateutil` is `python_dateutil`, and the real import name is
    `dateutil`, so a repository that uses it heavily yields zero candidates.

    Zero findings must read as "we could not find it" rather than as "this
    dependency is unused" -- the second is a claim the analysis did not earn.
    """
    root = build_sample_repo(tmp_path)
    (root / "requirements.txt").write_text("python-dateutil==2.9.0\n", encoding="utf-8")
    spec = DependencySpec(name="python-dateutil", current_version="2.8.0", target_version="2.9.0")
    analysis = analyze_repository(Workspace(root), spec)

    assert analysis.affected_files == ()
    assert any("import" in r.lower() for r in analysis.confidence_reducers)


def test_the_analysis_is_deterministic_over_the_same_input(tmp_path: Path) -> None:
    """Two runs against the same tree must be byte-identical. Anything
    order-dependent -- a set iterated into a tuple, a dict's insertion order --
    makes the report's contents change between runs on unchanged input, and
    a reader cannot tell that from a real change in the repository."""
    root = build_sample_repo(tmp_path)
    spec = DependencySpec(name="pydantic", current_version="1.10.13", target_version="2.9.0")
    first = analyze_repository(Workspace(root), spec)
    second = analyze_repository(Workspace(root), spec)
    assert first.model_dump_json() == second.model_dump_json()


def test_version_discrepancy_surfaces_rather_than_being_overridden(tmp_path: Path) -> None:
    """Spec 7.1: never silently overridden in either direction. The model's
    `version_discrepancy` helper already exists; this asserts the analyzer
    feeds it a detected version it can actually compare."""
    workspace = Workspace(build_sample_repo(tmp_path))
    spec = DependencySpec(name="pydantic", current_version="1.9.0", target_version="2.9.0")
    analysis = analyze_repository(workspace, spec)
    assert analysis.version_discrepancy("1.9.0") == ("1.9.0", EXPECTED_PINNED_VERSION)
    assert analysis.version_discrepancy(EXPECTED_PINNED_VERSION) is None


# -- RULING 17: `resolve_version` returning None (declared, unconstrained) ---


def test_a_dependency_declared_without_a_version_becomes_a_confidence_reducer(
    tmp_path: Path,
) -> None:
    """`dependencies = ["pydantic"]` with no version or specifier at all is
    DECLARED but not pinned -- `resolve_version` returns None, it does not
    raise. `detected_version=None` on its own reads as "no version detected"
    with no reason given; this must become a reducer naming both the
    dependency and the manifest that declares it unconstrained, so the claim
    still traces to a real file (CLAUDE.md rule 1).

    Only manifest in play is `pyproject.toml`'s bare entry:
    `requirements.txt` is overwritten to not mention pydantic at all, so
    nothing else could produce a detected version or this reducer by
    accident -- an implementation that forgot the `detected is None` branch
    fails this test, not merely reports it differently.
    """
    root = build_sample_repo(tmp_path)
    (root / "pyproject.toml").write_text(
        '[project]\nname = "sample-app"\nversion = "0.1.0"\n'
        'requires-python = ">=3.11"\ndependencies = ["pydantic"]\n',
        encoding="utf-8",
    )
    (root / "requirements.txt").write_text("requests==2.31.0\n", encoding="utf-8")
    spec = DependencySpec(name="pydantic", current_version="1.10.13", target_version="2.9.0")
    analysis = analyze_repository(Workspace(root), spec)

    assert analysis.detected_version is None
    reducer = next(
        (r for r in analysis.confidence_reducers if "pydantic" in r and "version" in r.lower()),
        None,
    )
    assert reducer is not None, analysis.confidence_reducers
    assert "pyproject.toml" in reducer
    assert "submodule" not in reducer.lower()
    assert "import" not in reducer.lower()


# -- RULING 31: a corrupted `.git` degrades rather than aborting -------------


def test_a_corrupted_git_history_becomes_a_confidence_reducer_not_an_abort(
    tmp_path: Path,
) -> None:
    """Verified by direct execution: `Workspace.git_log` against a `.git`
    with its `objects` directory deleted raises `RepoUnavailableError`
    (`This repository's git history could not be read.`), not an empty
    result. By this point in the analysis, steps 4-8 have already read and
    parsed the whole file tree successfully, so the repository is
    demonstrably usable -- this is broken git METADATA, not an unavailable
    repository, and the analyzer must degrade (empty churn, one named
    reducer) rather than let the exception abort a complete, correct
    analysis of the code.

    Distinguishes this from `test_commit_count_is_None_when_the_repository_has_no_history`
    above (no `.git` at all: no reducer, because there is nothing broken,
    only absent) by checking for the reducer's presence here and its
    absence there.
    """
    root = build_sample_repo(tmp_path)
    shutil.rmtree(root / ".git" / "objects")
    spec = DependencySpec(name="pydantic", current_version="1.10.13", target_version="2.9.0")
    analysis = analyze_repository(Workspace(root), spec)

    assert analysis.affected_files  # the code itself was still fully analysed
    assert all(a.commit_count is None for a in analysis.affected_files)
    assert analysis.commit_records == ()
    assert any("history" in r.lower() for r in analysis.confidence_reducers)


# -- RULING 45: `commit_records` populated from what step 9 already fetched -


def test_commit_records_are_populated_from_the_same_history_git_log_read(
    tmp_path: Path,
) -> None:
    """`commit_records` is `RepoAnalysis`'s raw evidence; the per-file churn
    figures (`AffectedFile.commit_count`) are derived FROM it. Shipping the
    derivation with the source empty would put `commit_count=2` in the
    report with no provenance anywhere in the model (CLAUDE.md rule 1)."""
    analysis = _analysis(tmp_path)
    assert len(analysis.commit_records) == 2
    shas = {record.sha for record in analysis.commit_records}
    assert len(shas) == 2
    for record in analysis.commit_records:
        assert record.timestamp.tzinfo is not None


# -- RULING 61: the three-way `commit_count` expression's untested branch ---


def test_commit_count_is_zero_for_an_untracked_file_while_history_is_available(
    tmp_path: Path,
) -> None:
    """The branch nothing exercised: `entry is None` while `churn.available`
    is True. Every file in the sample repo's git history is committed in
    commit 1, so `test_churn_reaches_the_affected_files` only ever proves
    `entry is not None`, and `test_commit_count_is_None_when_the_repository_has_no_history`
    only ever proves `churn.available is False`. Neither can tell
    `(0 if churn.available else None)` from a bare `None`.

    A new file, written to disk AFTER `build_sample_repo` returns and never
    committed, is picked up by `Workspace.iter_files` (a filesystem walk,
    not a git listing) so it becomes a real affected file with real usage
    sites, while `git log` -- which only ever saw the two earlier commits --
    has no `CommitRecord` naming it. History reads successfully
    (`churn.available is True`) and this path simply never appears in it:
    exactly "we looked and this file is quiet", which must read as `0`, not
    `None`.
    """
    root = build_sample_repo(tmp_path)
    new_file = root / "src" / "app" / "untracked_model.py"
    new_file.write_text(
        "from pydantic import BaseModel\n\n\nclass Widget(BaseModel):\n    name: str\n",
        encoding="utf-8",
    )
    spec = DependencySpec(name="pydantic", current_version="1.10.13", target_version="2.9.0")
    analysis = analyze_repository(Workspace(root), spec)

    widget = next(a for a in analysis.affected_files if a.path == "src/app/untracked_model.py")
    assert widget.commit_count == 0
    assert widget.last_modified is None


# -- RULING 62: the `scan.unreadable` reducer has zero coverage -------------


def test_an_unparseable_manifest_becomes_a_confidence_reducer_naming_it(
    tmp_path: Path,
) -> None:
    """Step 13's third reducer, gutted to `if False:`, still left the whole
    suite green -- nothing had ever put an unparseable-but-present manifest
    in front of the analyzer. A malformed `poetry.lock` (invalid TOML) is
    read, fails to decode, and lands in `ManifestScan.unreadable` (Task 2's
    own contract). `pyproject.toml`/`requirements.txt` still resolve the
    version normally, which is what isolates this to the reducer under
    test: the assertion on `detected_version` rules out this being a
    Ruling-17 "no version" reducer in disguise.
    """
    root = build_sample_repo(tmp_path)
    (root / "poetry.lock").write_text("not [ valid = toml", encoding="utf-8")
    spec = DependencySpec(name="pydantic", current_version="1.10.13", target_version="2.9.0")
    analysis = analyze_repository(Workspace(root), spec)

    assert analysis.detected_version is not None
    reducer = next((r for r in analysis.confidence_reducers if "poetry.lock" in r), None)
    assert reducer is not None, analysis.confidence_reducers


# -- RULING 63: the no-candidates reducer needs its negative direction ------


def test_when_candidates_are_found_there_is_no_import_root_reducer(tmp_path: Path) -> None:
    """The negative direction for the no-candidates reducer, in the same
    shape as `test_no_gitmodules_means_no_submodule_reducer` above. Without
    it, an implementation that appends the reducer unconditionally
    (`if True:`) passes `test_finding_no_candidate_at_all_is_reported_as_a_reducer`
    just as easily as a correct one."""
    analysis = _analysis(tmp_path)
    assert not any(
        "no file in this repository names the module" in r.lower()
        for r in analysis.confidence_reducers
    )


# -- RULING 64: `from <import_root> import *` becomes a confidence reducer --


def test_a_star_import_from_the_dependency_becomes_a_confidence_reducer(
    tmp_path: Path,
) -> None:
    """`AliasMap.has_star_import_from` (Task 4) was built and tested but
    never consumed. `from pydantic import *` binds names this module cannot
    enumerate without importing pydantic, so real usage in that module can
    be silently missed -- the same "we could not find it" failure the
    no-candidates reducer guards against, one file at a time. This must
    read as a named, traceable gap, not a quietly smaller finding set.
    """
    root = build_sample_repo(tmp_path)
    star_file = root / "src" / "app" / "star_import.py"
    star_file.write_text("from pydantic import *\n", encoding="utf-8")
    spec = DependencySpec(name="pydantic", current_version="1.10.13", target_version="2.9.0")
    analysis = analyze_repository(Workspace(root), spec)

    # "import *" -- not the bare word "import" -- is the discriminator: the
    # no-candidates reducer's text also contains "import" ("The import name
    # was inferred..."), but never "import *", and the two reducers cannot
    # fire in the same run anyway (one requires candidates.modules to be
    # empty, the other iterates it), so there is no fixture where both texts
    # could satisfy the same substring check by accident.
    reducer = next((r for r in analysis.confidence_reducers if "import *" in r), None)
    assert reducer is not None, analysis.confidence_reducers
    assert "src/app/star_import.py" in reducer
    assert "enumerated" in reducer.lower()


def test_no_star_import_means_no_star_import_reducer(tmp_path: Path) -> None:
    """The negative direction, in the same shape as
    `test_no_gitmodules_means_no_submodule_reducer` above. Without it, an
    implementation that appends the reducer unconditionally passes the
    test above just as easily as a correct one."""
    analysis = _analysis(tmp_path)
    assert not any("import *" in r for r in analysis.confidence_reducers)


# -- F2: a `dotted_module` collision must not fabricate a finding -----------


def test_a_file_colliding_on_dotted_module_gets_no_borrowed_model_definition(
    tmp_path: Path,
) -> None:
    """`_dotted_module` strips a leading `src/`, so a root-level
    `app/models.py` maps to the same `app.models` as the fixture's
    `src/app/models.py`. Keyed on that name, the impostor inherited the real
    file's `ModelClass` and the analyzer emitted a HIGH-confidence
    MODEL_DEFINITION naming `BaseModel` against a file that never mentions
    pydantic -- its own snippet contradicting its own symbol. Ruling 46's
    defect resurfacing in the variant that fix missed.

    `class Customer` sits on line 8 in both files on purpose: the collision
    needs the same name AND the same line. The impostor is reached by phase B
    (it contains the byte string `Customer`), not phase A, which is why it
    can be a candidate at all while containing no `pydantic`.
    """
    root = build_sample_repo(tmp_path)
    impostor = root / "app"
    impostor.mkdir()
    impostor_source = '"""Not a model."""\n' + "\n" * 6 + "class Customer(dict):\n    pass\n"
    assert impostor_source.split("\n")[7] == "class Customer(dict):"
    assert "pydantic" not in impostor_source
    (impostor / "models.py").write_text(impostor_source, encoding="utf-8")

    spec = DependencySpec(name="pydantic", current_version="1.10.13", target_version="2.9.0")
    analysis = analyze_repository(Workspace(root), spec)

    assert "app/models.py" not in {a.path for a in analysis.affected_files}
    for affected in analysis.affected_files:
        for site in affected.usage_sites:
            if site.kind is UsageKind.MODEL_DEFINITION:
                assert site.snippet is not None
                assert site.symbol in site.snippet, site


# -- Final fix round 2, item 3: report the dotted_module collision itself --


def test_a_dotted_module_collision_produces_a_confidence_reducer_naming_both_paths(
    tmp_path: Path,
) -> None:
    """`_dotted_module` strips a leading `src/`, so a root-level
    `app/models.py` and the fixture's `src/app/models.py` both resolve to
    `app.models`. `build_model_index`'s `dotted_targets` set cannot say
    which of the two a transitive base or a first-party import actually
    names -- see finding 3 of the second fix round -- so the honest
    response is a confidence reducer naming both paths, not a silent
    attribution in favour of one.
    """
    root = build_sample_repo(tmp_path)
    (root / "app").mkdir()
    (root / "app" / "models.py").write_text(
        "from pydantic import BaseModel\n\n\nclass Ticket(BaseModel):\n    id: int\n",
        encoding="utf-8",
    )
    spec = DependencySpec(name="pydantic", current_version="1.10.13", target_version="2.9.0")
    analysis = analyze_repository(Workspace(root), spec)

    reducer = next((r for r in analysis.confidence_reducers if "app.models" in r), None)
    assert reducer is not None, analysis.confidence_reducers
    assert "src/app/models.py" in reducer, reducer
    assert reducer.count("models.py") == 2, reducer


def test_no_dotted_module_collision_means_no_collision_reducer(tmp_path: Path) -> None:
    """The negative direction, in the same shape as
    `test_no_gitmodules_means_no_submodule_reducer` above."""
    analysis = _analysis(tmp_path)
    assert not any("app.models" in r for r in analysis.confidence_reducers)


# -- F3: an unrepresentable filename degrades, it does not crash (rule 20) --


def test_a_filename_that_cannot_be_cited_becomes_a_reducer_not_a_crash(
    tmp_path: Path,
) -> None:
    """`back\\slash.py` is a legal POSIX filename that `RepoRelativePath`
    refuses, because a backslash is a separator on some platforms and an
    ordinary character on others -- a citation naming it cannot be resolved.
    The analyzer's input is an untrusted third-party repository, so this
    reached `ModelClass(file=...)` and raised an uncaught `ValidationError`,
    killing a run that had already analysed the rest of the tree correctly.

    CLAUDE.md rule 20: the outcome is recorded, never a propagating
    exception. It cannot be a `SkippedFile` -- that model's own `path` field
    is `RepoRelativePath`, so the record could not name the file either --
    which is why this is a reducer, the same in-model channel the corrupted-
    history degrade already uses.
    """
    root = build_sample_repo(tmp_path)
    (root / "back\\slash.py").write_text(
        "from pydantic import BaseModel\n\n\nclass Ghost(BaseModel):\n    x: int\n",
        encoding="utf-8",
    )
    spec = DependencySpec(name="pydantic", current_version="1.10.13", target_version="2.9.0")
    analysis = analyze_repository(Workspace(root), spec)

    # The rest of the analysis is intact.
    assert analysis.detected_version is not None
    assert "src/app/models.py" in {a.path for a in analysis.affected_files}
    # And the excluded file is named as a gap, not silently dropped.
    reducer = next((r for r in analysis.confidence_reducers if "cited" in r), None)
    assert reducer is not None, analysis.confidence_reducers
    assert "back\\\\slash.py" in reducer, reducer
    assert "Ghost" not in {s for a in analysis.affected_files for s in a.symbols}


def test_an_ordinary_tree_gets_no_uncitable_reducer(tmp_path: Path) -> None:
    """The negative direction, in the same shape as
    `test_no_gitmodules_means_no_submodule_reducer` above."""
    analysis = _analysis(tmp_path)
    assert not any("cited" in r for r in analysis.confidence_reducers)


# -- F4: the analysis must not contradict itself about the current version --


def test_a_bare_requirement_does_not_hide_a_specifier_declared_elsewhere(
    tmp_path: Path,
) -> None:
    """The fixture declares `pydantic>=1.10,<2` in `pyproject.toml`. Make
    `requirements.txt` declare it bare, and `_rank` used to prefer the bare
    one on kind order alone -- so `detected_version` was None and a reducer
    said the version "could not be determined", inside the same
    `RepoAnalysis` whose `pyproject.toml` manifest carried
    `declared_specifier='>=1.10,<2'`. One object, two contradictory claims.
    """
    root = build_sample_repo(tmp_path)
    (root / "requirements.txt").write_text("pydantic\n", encoding="utf-8")
    spec = DependencySpec(name="pydantic", current_version="1.10.13", target_version="2.9.0")
    analysis = analyze_repository(Workspace(root), spec)

    pyproject = next(m for m in analysis.manifests if m.path == "pyproject.toml")
    assert pyproject.declared_specifier == ">=1.10,<2"
    assert analysis.detected_version is not None
    assert analysis.detected_version.value == ">=1.10,<2"
    assert analysis.detected_version.source_manifest.path == "pyproject.toml"
    assert not any("could not be determined" in r for r in analysis.confidence_reducers)


# -- F5: transitive discovery must not stop after one hop -------------------


def _write_chain(root: Path, links: int) -> None:
    """`Link00(BaseModel)`, `Link01(Link00)`, ... one class per module, plus a
    consumer calling a tracked method on the last link.

    One class per FILE is the point: `build_model_index`'s own fixed point
    already handles a chain within the candidate set, and phase B is what
    decides whether a file enters that set at all. A file naming only
    `LinkNN` is invisible to phase A (no `pydantic` in it) and to a phase B
    that searched for the phase-A index's names only.

    The index is zero-padded to two digits for a reason found the hard way:
    phase B is a BYTE-SUBSTRING scan, so an unpadded `Link1` matches
    `Link10`, `Link11` and `Link12` as well. That admitted most of a long
    chain in a single pass and made a deliberately-too-deep chain converge in
    four, quietly turning the cap test green against unfixed code.
    """
    package = root / "src" / "chain"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "link00.py").write_text(
        "from pydantic import BaseModel\n\n\nclass Link00(BaseModel):\n    x: int\n",
        encoding="utf-8",
    )
    for index in range(1, links):
        (package / f"link{index:02d}.py").write_text(
            f"from chain.link{index - 1:02d} import Link{index - 1:02d}\n\n\n"
            f"class Link{index:02d}(Link{index - 1:02d}):\n    y: int\n",
            encoding="utf-8",
        )
    (package / "consumer.py").write_text(
        f"from chain.link{links - 1:02d} import Link{links - 1:02d}\n\n\n"
        f"def go(item: Link{links - 1:02d}) -> dict:\n    return item.dict()\n",
        encoding="utf-8",
    )


def test_a_three_link_model_chain_reaches_its_consumer(tmp_path: Path) -> None:
    """The analyzer expanded candidates once, rebuilt the index over the
    expanded set -- and never re-expanded with the names that rebuild
    discovered. A chain longer than one link was silently truncated: the
    consumer's `.dict()` was missed with no reducer and no `skipped_files`
    entry, so the output claimed a completeness it did not have.

    `RepoAnalysis`'s `analyzed + skipped <= total` validator is `<=`, so a
    file falling into neither bucket does not even trip that.
    """
    root = build_sample_repo(tmp_path)
    _write_chain(root, links=3)
    spec = DependencySpec(name="pydantic", current_version="1.10.13", target_version="2.9.0")
    analysis = analyze_repository(Workspace(root), spec)

    consumer = next((a for a in analysis.affected_files if a.path == "src/chain/consumer.py"), None)
    assert consumer is not None, sorted(a.path for a in analysis.affected_files)
    calls = [s for s in consumer.usage_sites if s.kind is UsageKind.METHOD_CALL]
    assert [(s.symbol, s.confidence) for s in calls] == [("dict", Confidence.MEDIUM)]
    assert not any("converge" in r for r in analysis.confidence_reducers)


def test_a_chain_deeper_than_the_expansion_cap_says_so_rather_than_truncating(
    tmp_path: Path,
) -> None:
    """Untrusted input must not be able to make the analyzer either hang or
    lie. The loop is capped, and hitting the cap is reported: silently
    stopping would be the same false claim of completeness the one-hop bug
    made, just further along the chain.
    """
    root = build_sample_repo(tmp_path)
    _write_chain(root, links=MAX_EXPANSION_PASSES + 3)
    spec = DependencySpec(name="pydantic", current_version="1.10.13", target_version="2.9.0")
    analysis = analyze_repository(Workspace(root), spec)

    reducer = next((r for r in analysis.confidence_reducers if "converge" in r), None)
    assert reducer is not None, analysis.confidence_reducers
    assert str(MAX_EXPANSION_PASSES) in reducer


def test_the_cap_reducer_hedges_rather_than_asserting_a_gap_at_the_boundary_depth(
    tmp_path: Path,
) -> None:
    """Final fix round 2, finding 1: hitting the cap means convergence was
    not PROVEN, not that anything was actually truncated. At a chain depth
    of exactly `MAX_EXPANSION_PASSES` every module IS examined -- the
    consumer's `.dict()` is found below -- so a reducer that asserts files
    "were not examined" and usage "is missing" would be telling the user
    about a gap that does not exist, which is the same class of defect a
    fabricated finding is. The reducer must hedge ("may not have been
    examined") rather than assert.
    """
    root = build_sample_repo(tmp_path)
    _write_chain(root, links=MAX_EXPANSION_PASSES)
    spec = DependencySpec(name="pydantic", current_version="1.10.13", target_version="2.9.0")
    analysis = analyze_repository(Workspace(root), spec)

    consumer = next((a for a in analysis.affected_files if a.path == "src/chain/consumer.py"), None)
    assert consumer is not None, sorted(a.path for a in analysis.affected_files)
    calls = [s for s in consumer.usage_sites if s.kind is UsageKind.METHOD_CALL]
    assert [(s.symbol, s.confidence) for s in calls] == [("dict", Confidence.MEDIUM)]

    reducer = next((r for r in analysis.confidence_reducers if "converge" in r), None)
    assert reducer is not None, analysis.confidence_reducers
    assert "were not examined" not in reducer, reducer
    assert "is missing from this report" not in reducer, reducer
    assert "may not have been examined" in reducer, reducer
    assert "may be" in reducer, reducer


# -- A16/M12: which manifest the unconstrained reducer names, and why -------


def test_the_unconstrained_reducer_names_the_first_manifest_by_PATH_not_by_walk_order(
    tmp_path: Path,
) -> None:
    """Two things nothing bound: `unconstrained[0]` (A16 mutated it to
    `[-1]`, 675 green) and `scan.declarations`'s sort by path (M12 deleted
    it, 675 green). Ruling 59 relies on that sort for determinism.

    Binding the sort needs a fixture where PATH-STRING order and
    `iter_files`' walk order genuinely disagree, which is why the two
    directories are named `sub` and `sub-dir`. `iter_files` sorts `Path`
    objects, comparing part by part, so `sub` sorts before `sub-dir`
    ("sub" < "sub-dir") and the walk yields `sub/requirements.txt` first.
    Sorting the POSIX strings compares character by character, where `-`
    (0x2d) precedes `/` (0x2f), so `sub-dir/requirements.txt` comes first.
    Verified against this interpreter, not recalled.

    So: with the sort, the reducer names `sub-dir/...`; without it,
    `sub/...`; and with `[-1]` instead of `[0]`, `sub/...` as well. Neither
    the root `requirements.txt` nor `pyproject.toml` may mention pydantic,
    or a third declaration would sort ahead of both and mask the difference.
    """
    root = build_sample_repo(tmp_path)
    (root / "requirements.txt").write_text("requests==2.31.0\n", encoding="utf-8")
    (root / "pyproject.toml").write_text(
        '[project]\nname = "sample-app"\nversion = "0.1.0"\ndependencies = ["requests"]\n',
        encoding="utf-8",
    )
    for directory in ("sub", "sub-dir"):
        (root / directory).mkdir()
        (root / directory / "requirements.txt").write_text("pydantic\n", encoding="utf-8")

    workspace = Workspace(root)
    walk_order = [
        p.as_posix()
        for p in workspace.iter_files("")
        if p.name == "requirements.txt" and "/" in p.as_posix()
    ]
    assert walk_order == ["sub/requirements.txt", "sub-dir/requirements.txt"], walk_order

    spec = DependencySpec(name="pydantic", current_version="1.10.13", target_version="2.9.0")
    analysis = analyze_repository(workspace, spec)

    assert analysis.detected_version is None
    reducer = next(r for r in analysis.confidence_reducers if "version constraint" in r)
    assert "sub-dir/requirements.txt" in reducer, reducer
    assert "sub/requirements.txt" not in reducer, reducer


# -- A17b: the reducers' FIXED ORDER, which had zero coverage ---------------

_REDUCER_DISCRIMINATORS = (
    "submodule",
    "cannot be cited",
    "did not converge",
    "version constraint",
    "history could not be read",
    "could not be parsed",
    "import *",
)
"""One substring per reducer, in the order `analyze_repository` emits them.

Each matches exactly one reducer (Ruling 58's uniqueness property, extended
to the two added in this round). The no-candidates reducer is deliberately
absent: it requires `candidates.modules` to be EMPTY, and four of the seven
above require it to be non-empty, so no fixture can produce all eight.
"""


def test_the_confidence_reducers_are_emitted_in_their_documented_order(
    tmp_path: Path,
) -> None:
    """Mutation A17b emitted `tuple(reversed(confidence_reducers))` and all
    675 tests stayed green. Each of the six discriminators was individually
    tested; nothing bound the SEQUENCE -- and the sequence is what the user
    reads first, ordered deliberately from repository-wide down to a
    per-module caveat (Ruling 57).

    This fixture triggers seven reducers at once, which is the maximum that
    can co-occur, and asserts the exact order. Every trigger is independent
    of the others:

      submodules            an empty `.gitmodules`
      cannot be cited       `back\\slash.py`, which no citation can name
      did not converge      an inheritance chain deeper than the pass cap
      version constraint    both root manifests declare pydantic bare
      history unreadable    `.git/objects` removed
      could not be parsed   a `poetry.lock` that is not valid TOML
      import *              a module doing `from pydantic import *`
    """
    root = build_sample_repo(tmp_path)
    (root / ".gitmodules").write_text('[submodule "x"]\n', encoding="utf-8")
    (root / "back\\slash.py").write_text("x = 1\n", encoding="utf-8")
    _write_chain(root, links=MAX_EXPANSION_PASSES + 3)
    (root / "requirements.txt").write_text("pydantic\n", encoding="utf-8")
    (root / "pyproject.toml").write_text(
        '[project]\nname = "sample-app"\nversion = "0.1.0"\ndependencies = ["pydantic"]\n',
        encoding="utf-8",
    )
    (root / "poetry.lock").write_text("not [ valid = toml", encoding="utf-8")
    (root / "src" / "app" / "star.py").write_text("from pydantic import *\n", encoding="utf-8")
    shutil.rmtree(root / ".git" / "objects")

    spec = DependencySpec(name="pydantic", current_version="1.10.13", target_version="2.9.0")
    reducers = analyze_repository(Workspace(root), spec).confidence_reducers

    matched = [
        next(d for d in _REDUCER_DISCRIMINATORS if d in reducer)
        for reducer in reducers
        if any(d in reducer for d in _REDUCER_DISCRIMINATORS)
    ]
    assert matched == list(_REDUCER_DISCRIMINATORS), reducers
    assert len(reducers) == len(_REDUCER_DISCRIMINATORS), reducers


def test_each_reducer_discriminator_matches_exactly_one_reducer(tmp_path: Path) -> None:
    """Ruling 58's uniqueness property, which the order test above depends on:
    if two reducers shared a discriminator the sequence assertion could pass
    on the wrong pairing. Re-checked here because this round added two
    reducers to the six the ruling covered."""
    root = build_sample_repo(tmp_path)
    (root / ".gitmodules").write_text('[submodule "x"]\n', encoding="utf-8")
    (root / "back\\slash.py").write_text("x = 1\n", encoding="utf-8")
    _write_chain(root, links=MAX_EXPANSION_PASSES + 3)
    (root / "requirements.txt").write_text("pydantic\n", encoding="utf-8")
    (root / "pyproject.toml").write_text(
        '[project]\nname = "sample-app"\nversion = "0.1.0"\ndependencies = ["pydantic"]\n',
        encoding="utf-8",
    )
    (root / "poetry.lock").write_text("not [ valid = toml", encoding="utf-8")
    (root / "src" / "app" / "star.py").write_text("from pydantic import *\n", encoding="utf-8")
    shutil.rmtree(root / ".git" / "objects")

    spec = DependencySpec(name="pydantic", current_version="1.10.13", target_version="2.9.0")
    reducers = analyze_repository(Workspace(root), spec).confidence_reducers

    for discriminator in _REDUCER_DISCRIMINATORS:
        assert sum(discriminator in r for r in reducers) == 1, discriminator
