"""Tests for two-phase candidate selection and parsing.

See `candidates.py`'s module docstring for why two phases are needed at
all -- the plan's Deviation 2. `test_phase_b_is_what_finds_service_py_not_
its_docstring` below is THE regression test for that deviation.
"""

from pathlib import Path

from tests.fixtures.repo_builder import (
    EXPECTED_PYTHON_FILES,
    EXPECTED_UNPARSEABLE,
    build_sample_repo,
)
from upgradepilot.services.analysis.candidates import expand_candidates, select_candidates
from upgradepilot.services.repo.workspace import Workspace


def test_phase_a_selects_files_naming_the_import_root(tmp_path: Path) -> None:
    scan = select_candidates(Workspace(build_sample_repo(tmp_path)), import_root="pydantic")
    assert "src/app/models.py" in {m.file for m in scan.modules}


def test_phase_a_counts_every_python_file_not_only_the_candidates(tmp_path: Path) -> None:
    """`total_python_files` is the denominator of `skipped_ratio`, which feeds
    the analysis_coverage risk factor. Counting only candidates would make
    coverage look complete on a repository where one file in fifty was even
    looked at."""
    scan = select_candidates(Workspace(build_sample_repo(tmp_path)), import_root="pydantic")
    assert scan.total_python_files == EXPECTED_PYTHON_FILES
    assert len(scan.modules) < scan.total_python_files


def test_the_unparseable_file_becomes_a_skipped_record_not_an_exception(tmp_path: Path) -> None:
    scan = select_candidates(Workspace(build_sample_repo(tmp_path)), import_root="pydantic")
    assert EXPECTED_UNPARSEABLE not in {m.file for m in scan.modules}
    skipped = {s.path: s.reason for s in scan.skipped}
    assert EXPECTED_UNPARSEABLE in skipped
    assert "syntax" in skipped[EXPECTED_UNPARSEABLE].lower()


def test_phase_b_finds_a_consumer_that_never_names_the_dependency(tmp_path: Path) -> None:
    """`src/app/consumer.py` contains no occurrence of "pydantic" anywhere.
    It is reachable only through phase B, which searches for the model class
    names phase A discovered."""
    workspace = Workspace(build_sample_repo(tmp_path))
    phase_a = select_candidates(workspace, import_root="pydantic")
    assert "src/app/consumer.py" not in {m.file for m in phase_a.modules}

    expanded = expand_candidates(workspace, phase_a, model_names=frozenset({"Customer", "Invoice"}))
    assert "src/app/consumer.py" in {m.file for m in expanded.modules}


def test_phase_b_is_what_finds_service_py_not_its_docstring(tmp_path: Path) -> None:
    """THE regression test for Deviation 2.

    `src/app/service.py` is a phase-A hit today only because its module
    docstring contains the word "pydantic". Rewrite the docstring without it
    -- a change no reviewer would question -- and under a one-phase filter
    the file silently leaves the analysis, taking four medium-confidence
    findings with it, with every existing test still green.

    This test performs that rewrite and asserts the file is STILL found. It
    fails if `expand_candidates` is removed, and it fails for the right
    reason.
    """
    root = build_sample_repo(tmp_path)
    service = root / "src" / "app" / "service.py"
    original = service.read_text(encoding="utf-8")
    assert "pydantic" in original, "the accident this test exists to remove is gone"
    service.write_text(original.replace("pydantic in scope", "models in scope"), encoding="utf-8")
    assert "pydantic" not in service.read_text(encoding="utf-8")

    workspace = Workspace(root)
    phase_a = select_candidates(workspace, import_root="pydantic")
    assert "src/app/service.py" not in {m.file for m in phase_a.modules}, (
        "phase A should no longer find it -- if it does, this test is not testing phase B"
    )

    expanded = expand_candidates(workspace, phase_a, model_names=frozenset({"Customer", "Invoice"}))
    assert "src/app/service.py" in {m.file for m in expanded.modules}


def test_expand_candidates_with_no_model_names_adds_nothing(tmp_path: Path) -> None:
    """A repository where the dependency defines no models the user
    subclasses. Phase B must be a no-op, not a full-repository parse."""
    workspace = Workspace(build_sample_repo(tmp_path))
    phase_a = select_candidates(workspace, import_root="pydantic")
    expanded = expand_candidates(workspace, phase_a, model_names=frozenset())
    assert {m.file for m in expanded.modules} == {m.file for m in phase_a.modules}


def test_expand_candidates_with_model_names_adds_a_file_phase_a_missed(tmp_path: Path) -> None:
    """Companion to the no-op case above: proves `expand_candidates` cannot
    pass both tests by simply ignoring `model_names` and always returning
    phase A unchanged. Together the pair discriminates "phase B is wired to
    `model_names`" from "phase B is a no-op regardless of its argument"."""
    workspace = Workspace(build_sample_repo(tmp_path))
    phase_a = select_candidates(workspace, import_root="pydantic")
    expanded = expand_candidates(workspace, phase_a, model_names=frozenset({"Customer"}))
    assert {m.file for m in expanded.modules} > {m.file for m in phase_a.modules}


def test_a_file_is_never_parsed_twice(tmp_path: Path) -> None:
    """`expand_candidates` returns phase A's modules plus phase B's. If it
    re-scanned the hits it would duplicate them, and `analyzed_files` -- a
    numerator the report prints -- would exceed `total_python_files` and trip
    RepoAnalysis's own validator."""
    workspace = Workspace(build_sample_repo(tmp_path))
    phase_a = select_candidates(workspace, import_root="pydantic")
    expanded = expand_candidates(workspace, phase_a, model_names=frozenset({"Customer"}))
    files = [m.file for m in expanded.modules]
    assert len(files) == len(set(files))


def test_a_file_that_is_not_utf8_is_skipped_with_a_decode_reason(tmp_path: Path) -> None:
    root = build_sample_repo(tmp_path)
    (root / "src" / "app" / "latin.py").write_bytes(b"# pydantic\nx = '\xff\xfe'\n")
    scan = select_candidates(Workspace(root), import_root="pydantic")
    skipped = {s.path: s.reason for s in scan.skipped}
    assert "src/app/latin.py" in skipped
    assert "decode" in skipped["src/app/latin.py"].lower()
