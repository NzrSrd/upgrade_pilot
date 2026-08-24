# UpgradePilot Phase 2 — Repository Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Given a `Workspace` and a `DependencySpec`, produce a `RepoAnalysis` in which every finding carries a real file, a real line, and an honest confidence label.

**Architecture:** A new `services/analysis/` package, pure over a `Workspace` — no LLM, no network, no graph, no HTTP. Nine focused modules, each independently testable, assembled by one orchestrator (`analyzer.py`). The analyzer is **two-pass**: pass 1 finds the dependency's model classes across the repository, pass 2 grades usage against that index. Pass 1 exists because a single pass cannot tell `invoice.dict()` (a real model call) from `bag.dict()` (a coincidence of naming), and that distinction is the whole difference between the medium and low confidence tiers.

**Tech Stack:** Python 3.14.5 standard library only for this phase — `ast`, `tomllib`, `json`, `re`, `subprocess` (via the existing `Workspace`). Pydantic v2 for the typed records.

**Spec:** `docs/superpowers/specs/2026-08-24-upgradepilot-agent-core-design.md` (§7.1 is this phase; §8.1 names the factors this phase feeds)

**Phase order:** `PLANNING.md` → Sub-project 1 → Phase 2. Phase 1 is complete and merged at `8d972d3`.

---

## Global Constraints

Every task's requirements implicitly include this section.

**Project rules** — `CLAUDE.md` binds every task. The four that bite hardest here:

- **Rule 1.** Every claim traces to a real line of code. A `UsageSite` whose `file`/`line` does not point at the construct it names is the exact defect this product exists to prevent.
- **Rule 12.** No new dependency. `packaging` **is** importable in the venv (26.3, pulled in transitively) — do **not** use it. It is not a declared dependency, using it would require pinning it in `pyproject.toml` and recording it in ADR-001, and this phase never needs to *compare* two versions: it classifies a specifier as exact-or-range and reports the raw text. Stdlib `re` is sufficient. Any task that imports `packaging` is rejected.
- **Rule 16.** Layer direction. `services/analysis/` may import `models/` and `services/repo/`. It must not import LangGraph, FastAPI, or anything under `graph/` or `api/`.
- **Rule 19.** The LLM never produces a file path, a line number, or a risk level. This whole phase runs with no LLM at all; there is nothing to prompt.

**Toolchain (verified on this machine, 2026-08-24)**
- Python **3.14.5**, venv at `backend/.venv`. Run everything as `cd backend && .venv/bin/python -m pytest ...`.
- git **2.50.1** at `/usr/bin/git`.
- `pytest` has `addopts = "-q"` already. A second `-q` gives `-qq` and **hides the summary line**. To read pass/fail counts use `-o addopts="" -q`.
- `norecursedirs` already excludes `tests/fixtures/sample_repo`; ruff has `force-exclude = true` for it. Do not touch either — the fixture tree is deliberately Pydantic-v1 and deliberately unparseable, and lint or format touching it destroys what it exists to prove.

**Gates that must be green before any task is reported DONE**

```bash
cd backend
.venv/bin/python -m pytest -o addopts="" -q
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
.venv/bin/python -m mypy
```

Phase 1's baseline: **481 passed, 5 skipped**. A task that reduces the passing count without deleting a test has broken something.

**Naming of the phase's canonical keys** — these strings are consumed by Phase 3's corpus and must not drift:

- A dependency's **canonical name** is PEP 503: `re.sub(r"[-_.]+", "-", name).lower()`. `pydantic` → `pydantic`.
- A dependency's **import root** is the canonical name with `-` replaced by `_`. `pydantic` → `pydantic`.
- The **corpus symbol** for an implicitly-optional field is the literal string `Optional`, whatever the annotation's spelling (`Optional[T]`, `Union[T, None]`, `T | None`). Phase 3's corpus documents key on `Optional`.

---

## Verified before writing this plan

Every fact below was established by running code against this repository on 2026-08-24, not recalled. They are the load-bearing assumptions of the tasks that follow.

| Claim | How it was verified | Result |
|---|---|---|
| `nickname: Optional[str]` (no default) is distinguishable from `note: Optional[str] = None` | `ast.parse` on the fixture, printing `node.value is None` explicitly rather than `ast.unparse(node.value)` | Absent default → `AnnAssign.value is None`; explicit `= None` → `Constant('None')`. **A probe that unparses the value cannot tell them apart — both render as `None`.** |
| A decorator's `col_offset` does not point at the `@` | `line[d.col_offset]` on `@validator("email")` | `col_offset == 5`, the character there is `'v'`; the `@` is at column 4 |
| `ast.walk` cannot tell you a `class Config:` is nested inside a model | walked, then re-walked with a `NodeVisitor` tracking a class stack | `ast.walk` yields `Config` with no parent information. Nesting requires a visitor with an explicit stack. |
| `Attribute.col_offset` is the start of the *receiver*, not the method name | `invoice.dict()` inside `json.dumps(...)` on line 9 of `service.py` | `col_offset == 22`, which is the `i` of `invoice` |
| `service.py`'s receivers resolve without guessing | probed imports, class definitions and annotated parameters | `Customer.parse_obj` / `Invoice.schema` → imported names bound to model classes; `invoice.copy()` / `invoice.dict()` → parameter annotated `Invoice`; `json.dumps` → module, not a model |
| `util.py`'s `bag.dict()` does **not** resolve to a model | same probe | `Bag` is defined locally with no bases. Correctly ungraded. |
| `poetry.lock` and `uv.lock` share a parse shape | `tomllib.load` on a real-shaped file of each | both expose `package` as a list of tables with a bare `version` string |
| `Pipfile.lock` versions carry the operator | `json.load` on a real-shaped file | `{"pydantic": {"version": "==1.10.13"}}` — the `==` **is part of the string** and must be stripped |
| `packaging` is importable but undeclared | `import packaging` in the venv | 26.3, transitive. Forbidden by rule 12. |

---

## Two deviations from the spec, and why

Both were found by running the spec's own rules against the fixture the spec mandates. Both are recorded here rather than resolved silently, per `CLAUDE.md` rule 8.

### Deviation 1 — the medium tier needs a model index, not a module-level heuristic

**Spec §7.1 says:** `.dict()`, `.json()`, `.parse_obj()`, `.copy()`, `.schema()` are medium confidence "in a module that imports the dependency and defines models", and low confidence "elsewhere".

**Measured against the fixture:** `src/app/service.py` imports `app.models`, not `pydantic`, and defines no classes. Under the spec's literal rule its four calls are **low** confidence. But the fixture commits `EXPECTED_MEDIUM_CONFIDENCE_SYMBOLS = ("copy", "dict", "parse_obj", "schema")` and `tests/unit/test_fixture_repo.py` binds that tuple to `service.py`, using `_MODEL_IMPORT_PREFIXES = ("pydantic", "app.models")` — a hardcoded, fixture-specific module name that no analyzer could know.

Collapsing `service.py` to low would erase the distinction between the medium tier and `util.py`'s deliberate low trap, which is the one thing this fixture exists to prove.

**Ruling:** grade a tracked method call by **what its receiver resolves to**, not by what its module imports. Build a repository-wide index of classes deriving from the dependency (pass 1); a call is medium when its receiver is a name bound to an indexed class, or a parameter/variable annotated with one; low otherwise. This reproduces the fixture's documented tiers exactly, derives what `_MODEL_IMPORT_PREFIXES` hardcodes, and is strictly more accurate than the spec's heuristic — it grades `bag.dict()` low because `Bag` has no model base, rather than because of which file it happens to sit in.

**Cost if wrong:** receiver resolution is more code than a module-level flag, and over-clever resolution would over-grade. Mitigated by keeping resolution deliberately narrow — imported names bound to indexed classes, and annotations naming them. No type inference, no dataflow. Anything else is low.

**Spec §7.1 must be amended** to describe the index. That edit is Task 12.

### Deviation 2 — the byte prefilter needs a second pass, and today it passes by accident

**Spec §7.1 says:** candidate selection is "a cheap byte-substring scan for the dependency name over `.py` files, then `ast.parse` only on hits."

**Measured:** `grep -i pydantic` over the fixture hits `models.py` and `service.py`, misses `util.py` and `tests/test_models.py`. `service.py` hits — but **only because its module docstring contains the word "pydantic"**:

```python
"""Method calls on models: medium confidence (generic names, pydantic in scope)."""
```

Rewrite that docstring without the word and `service.py` leaves the candidate set, four medium findings vanish, and every existing test stays green — the fixture tests parse files directly and never go through the prefilter.

**Ruling:** candidate selection is two-phase. Phase A is the spec's byte scan for the import root. Phase B re-scans the non-hits for the byte forms of the **model class names discovered in phase A** — a small, specific, bounded set (`Customer`, `Invoice`), not a generic word. A module that consumes a model must name it. Task 6 pins this with a test that deletes the docstring's "pydantic" and asserts the medium sites are still found: a test that fails if phase B is removed.

**Cost if wrong:** one extra byte pass over the non-hit files — bounded by repository size, no parsing. If phase B over-selects, the cost is parsing files that yield no sites; correctness is unaffected because grading is independent of selection.

**Documented limitation, carried into the report:** the byte scan uses the distribution name's import root. That is correct for `pydantic` and wrong for distributions whose import name differs (`python-dateutil` → `dateutil`, `PyYAML` → `yaml`). Resolving it properly needs installed package metadata, which a static analysis of a cloned repository does not have. Task 9 records this as an explicit confidence reducer whenever no candidate is found, rather than reporting a clean "no usage".

---

## File Structure

**New package — `backend/src/upgradepilot/services/analysis/`**

| File | Responsibility |
|---|---|
| `__init__.py` | empty, matching the other packages |
| `manifests.py` | find the five manifest kinds; parse each into `Declaration` records |
| `versions.py` | precedence, `VersionConfidence`, `DependencyRole`, `DependencyNotFoundError` |
| `imports.py` | `AliasMap` — local name → dotted origin, per module |
| `models_index.py` | pass 1: classes deriving from the dependency, repository-wide |
| `usage.py` | pass 2: `UsageSite` detection and confidence grading |
| `candidates.py` | two-phase candidate selection, decode, parse, `SkippedFile` |
| `churn.py` | `CommitRecord` list → per-path commit count and last-modified |
| `layout.py` | test-path convention, source↔test correspondence, language mix |
| `analyzer.py` | `analyze_repository` — the only entry point Phase 4 will call |

**Modified**

| File | Change |
|---|---|
| `models/enums.py` | three `RiskCategory` renames (spec §8.1) |
| `models/evidence.py` | new `RepoRelativePath` type; apply to `RepoEvidence.file` |
| `models/repo.py` | repo-relative paths, aware datetimes, `commit_count: int \| None`, `languages` → tuple, `confidence_reducers` |
| `models/inputs.py` | `DependencySpec.canonical_name` / `.import_root` computed fields |
| `tests/unit/test_model_invariants.py` | conformance walk widened to `upgradepilot.services` |
| `tests/fixtures/repo_builder.py` | corrected expectation tuples; new low-tier fixture file; lock-file fixtures |
| `pyproject.toml` | mypy `files` gains `tests`; `exclude` gains the fixture tree |

**New test files** — one per module, plus one end-to-end.

`tests/unit/test_analysis_manifests.py`, `test_analysis_versions.py`, `test_analysis_imports.py`, `test_analysis_models_index.py`, `test_analysis_usage.py`, `test_analysis_candidates.py`, `test_analysis_churn.py`, `test_analysis_layout.py`, `tests/analysis/test_analyzer_end_to_end.py`.

**New fixture files** — `tests/fixtures/manifests/` holds one text file per manifest kind for the parser unit tests (parsers are pure functions over text; they do not need a built git repository).

---

## Interfaces at a glance

Every task's `Interfaces` block repeats what it needs. This table is the single place the whole set is visible, so a reader can check consistency without reading eleven tasks.

```python
# manifests.py
def scan_manifests(workspace: Workspace, canonical_name: str) -> ManifestScan
class ManifestScan:  manifests: tuple[Manifest, ...];  declarations: tuple[Declaration, ...]
class Declaration:   manifest: Manifest;  raw_name: str;  version: str | None
                     specifier: str | None;  confidence: VersionConfidence;  is_lockfile: bool

# versions.py
def resolve_version(declarations: tuple[Declaration, ...], *, canonical_name: str) -> DetectedVersion

# imports.py
class AliasMap:      entries: tuple[AliasEntry, ...]
    @classmethod def from_module(cls, tree: ast.Module) -> Self
    def origin_of(self, local_name: str) -> str | None
    def root_of(self, local_name: str) -> str | None
class AliasEntry:    local: str;  origin: str;  line: int;  column: int;  is_module: bool

# models_index.py
def build_model_index(modules: tuple[ParsedModule, ...], *, import_root: str) -> ModelIndex
class ModelIndex:    classes: tuple[ModelClass, ...]
    def names(self) -> frozenset[str]
    def is_model_class(self, dotted: str) -> bool
class ModelClass:    file: str;  dotted_module: str;  name: str;  line: int;  base_symbol: str

# usage.py
TRACKED_METHODS: frozenset[str]
def detect_usage(module: ParsedModule, *, import_root: str, index: ModelIndex) -> tuple[UsageSite, ...]

# candidates.py
@dataclass(frozen=True, slots=True)
class ParsedModule: file: str;  dotted_module: str;  source: str;  tree: ast.Module
@dataclass(frozen=True, slots=True)
class CandidateScan: modules: tuple[ParsedModule, ...];  skipped: tuple[SkippedFile, ...]
                     total_python_files: int
def select_candidates(workspace: Workspace, *, import_root: str) -> CandidateScan

# churn.py
class ChurnIndex:    entries: tuple[ChurnEntry, ...];  available: bool
    @classmethod def from_records(cls, records: tuple[CommitRecord, ...]) -> Self
    def for_path(self, path: str) -> ChurnEntry | None
class ChurnEntry:    path: str;  commit_count: int;  last_modified: AwareDatetime

# layout.py
def is_test_path(path: str) -> bool
def corresponding_test_paths(source: str, test_paths: tuple[str, ...]) -> tuple[str, ...]
def language_shares(workspace: Workspace) -> tuple[LanguageShare, ...]
# LanguageShare is defined in models/repo.py, NOT here: RepoAnalysis.languages is
# typed as tuple[LanguageShare, ...], and models/ must never import services/.

# analyzer.py
def analyze_repository(workspace: Workspace, dependency: DependencySpec,
                       *, history_limit: int = 100) -> RepoAnalysis
```

---

### Task 1: Model refinements the analyzer needs

`PLANNING.md` parks six model findings under "Carried in from Phase 1" with the reason *"the right constraint only becomes visible once the analyzer exists"*. The analyzer is now specified, so the constraints are visible — and every later task in this plan builds on these shapes. Doing this first means nothing gets built twice.

**Files:**
- Modify: `backend/src/upgradepilot/models/enums.py`
- Modify: `backend/src/upgradepilot/models/evidence.py`
- Modify: `backend/src/upgradepilot/models/repo.py`
- Modify: `backend/src/upgradepilot/models/inputs.py`
- Modify: `backend/tests/unit/test_model_invariants.py`
- Test: `backend/tests/unit/test_repo_models.py`, `backend/tests/unit/test_evidence_models.py` (extend both)

**Interfaces:**
- Consumes: nothing new.
- Produces: `RepoRelativePath` (from `models.evidence`), `LanguageShare` (from `models.repo`), `RepoAnalysis.confidence_reducers`, `DependencySpec.canonical_name` / `.import_root`, and the renamed `RiskCategory` members. Every later task uses at least two of these.

- [ ] **Step 1: Write the failing tests for the repo-relative path type**

Add to `backend/tests/unit/test_evidence_models.py`:

```python
import pytest
from pydantic import ValidationError

from upgradepilot.models.evidence import RepoEvidence

_REJECTED = (
    "/etc/passwd",              # absolute
    "../outside/secrets.py",    # parent escape
    "src/../../outside.py",     # parent escape, interior
    "./src/app.py",             # curdir prefix
    ".",                        # curdir itself
    "src\\app\\models.py",      # windows separator
    "   ",                      # blank (already covered by NonBlankStr, kept as a guard)
)
_ACCEPTED = (
    "src/app/models.py",
    "models.py",
    "a/b/c/d/e.py",
    "src/app/.hidden.py",       # a leading dot on a *segment* is a real filename
)


@pytest.mark.parametrize("path", _REJECTED)
def test_repo_evidence_rejects_non_repo_relative_paths(path: str) -> None:
    """Every citation this product prints resolves against a repository root.

    An absolute path in a citation points at the analysis machine's disk, not
    at the user's repository, and a `..` segment points outside the tree that
    was analyzed at all. Either one is a citation the reader cannot check --
    CLAUDE.md rule 1's exact failure.
    """
    with pytest.raises(ValidationError):
        RepoEvidence(file=path, line=1)


@pytest.mark.parametrize("path", _ACCEPTED)
def test_repo_evidence_accepts_ordinary_repo_relative_paths(path: str) -> None:
    """The negative test above is worthless unless the positive direction is
    shown to still pass: a validator that rejected everything would satisfy it."""
    assert RepoEvidence(file=path, line=1).file == path
```

- [ ] **Step 2: Run them and confirm they fail**

```bash
cd backend && .venv/bin/python -m pytest tests/unit/test_evidence_models.py -o addopts="" -q -k repo_relative
```

Expected: the `_REJECTED` cases fail (they are currently accepted) except `"   "`, which `NonBlankStr` already rejects. **Read the failure list.** If every case already passes, the validator you are about to write is unnecessary — stop and report that instead of writing it.

- [ ] **Step 3: Add `RepoRelativePath` to `models/evidence.py`**

Place it immediately after `NonBlankStr`, which it builds on.

```python
import posixpath
from pydantic import AfterValidator


def _require_repo_relative(value: str) -> str:
    """Reject any path that does not name a file inside the analyzed tree.

    Checked against the *text*, never the filesystem: this validator runs on
    citations that may be constructed long after the workspace is deleted, and
    a filesystem probe here would both fail spuriously and turn a model
    constructor into an existence oracle.

    `posixpath` explicitly, not `os.path`: repository paths are POSIX by
    construction (`git log --name-only` emits POSIX, and `Path.as_posix()` is
    what the analyzer calls), and `os.path` would quietly accept `a\\b` as a
    single filename on this platform while treating it as a separator on
    another.
    """
    if value.startswith("/"):
        raise ValueError(f"path must be repository-relative, not absolute: {value!r}")
    if "\\" in value:
        raise ValueError(f"path must use '/' separators: {value!r}")
    segments = value.split("/")
    if any(segment in {"", ".", ".."} for segment in segments):
        raise ValueError(
            f"path must not contain empty, '.' or '..' segments: {value!r}"
        )
    if posixpath.normpath(value) != value:
        raise ValueError(f"path must already be normalised: {value!r}")
    return value


RepoRelativePath = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
    AfterValidator(_require_repo_relative),
]
"""A path naming a file inside the analyzed repository, relative to its root.

Every file-and-line citation the report prints is resolved against a
repository root the reader supplies. An absolute path resolves against the
analysis machine instead, and a `..` segment resolves outside the tree that
was analyzed -- both produce a citation that looks precise and cannot be
checked.

`NonBlankStr` alone was not enough: it accepted `/etc/passwd` and
`../outside.py` without complaint, which `PLANNING.md` recorded as a Phase 2
carry-in. The analyzer is the only producer of these values from Task 9
onward, and it emits `Path.relative_to(root).as_posix()`, which satisfies
this by construction.
"""
```

- [ ] **Step 4: Apply the type where paths are stored**

Replace `NonBlankStr` with `RepoRelativePath` on:

- `models/evidence.py`: `RepoEvidence.file`
- `models/repo.py`: `Manifest.path`, `UsageSite.file`, `SkippedFile.path`, `AffectedFile.path`, `SymbolStat.files` element type
- `models/repo.py`: `CommitRecord.files` and `RepoAnalysis.test_paths` element types (currently bare `str`)

Do **not** apply it to `SkippedFile.reason`, `AppError.detail`, or any `url_or_reference` — those are not paths.

- [ ] **Step 5: Run the whole suite and fix the fallout**

```bash
cd backend && .venv/bin/python -m pytest -o addopts="" -q
```

Existing tests construct these models with paths that were legal before. Fix each by making the test's path repository-relative — never by loosening the validator. Report the count of tests you had to touch in your report file.

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "feat(models): require repository-relative paths on every citation"
```

- [ ] **Step 7: Rename the three overstated `RiskCategory` members**

`PLANNING.md`: *"`RiskCategory` member names have drifted from spec §8.1, and three of them overstate their scope."* Spec §8.1's factor table is the authority:

| Current member | Current value | Becomes | New value |
|---|---|---|---|
| `BREAKING_CHANGE` | `breaking_change` | `BREAKING_CHANGE_EXPOSURE` | `breaking_change_exposure` |
| `TEST_COVERAGE` | `test_coverage` | `TEST_COVERAGE_OF_AFFECTED` | `test_coverage_of_affected` |
| `CHURN` | `churn` | `CHURN_ON_AFFECTED` | `churn_on_affected` |

The other four already match §8.1 and must not be touched. Each rename narrows a claim: `TEST_COVERAGE` reads as the repository's test coverage, which this product never measures — it measures whether *affected* files have a locatable corresponding test. `CHURN` reads as repository churn; the factor is churn on affected paths only.

Add this test to `tests/unit/test_evidence_models.py`:

```python
from upgradepilot.models.enums import RiskCategory

# Copied verbatim from spec 8.1's factor table. If the spec changes, this
# tuple changes with it in the same commit -- it is a transcription of the
# authority, not an independent opinion.
_SPEC_8_1_FACTORS = (
    "breaking_change_exposure",
    "blast_radius",
    "test_coverage_of_affected",
    "churn_on_affected",
    "analysis_coverage",
    "evidence_coverage",
    "constraint_pressure",
)


def test_risk_categories_match_the_spec_factor_table_exactly() -> None:
    """Both directions, deliberately.

    Phase 6 builds one RiskFactor per member of this enum and the report
    prints the value as the factor's name. A member the spec does not define
    is a factor with no documented threshold table; a spec factor with no
    member is a factor that silently never gets computed. `==` on sorted
    tuples catches both; `all(x in y)` catches only one.
    """
    assert tuple(sorted(c.value for c in RiskCategory)) == tuple(sorted(_SPEC_8_1_FACTORS))
```

- [ ] **Step 8: Run it, see it fail, apply the renames, see it pass**

```bash
cd backend && .venv/bin/python -m pytest tests/unit/test_evidence_models.py -o addopts="" -q -k spec_factor_table
grep -rn "RiskCategory\." src tests   # every call site, before you rename
```

- [ ] **Step 9: Aware datetimes, and `commit_count` that can say "unknown"**

In `models/repo.py`:

```python
from pydantic import AwareDatetime
```

- `CommitRecord.timestamp: AwareDatetime` (was `datetime`). `Workspace.git_log` already builds these with `tz=UTC`, so the producer already conforms; this makes it unforgeable.
- `AffectedFile.last_modified: AwareDatetime | None = None` (was `datetime | None`).
- `AffectedFile.commit_count: int | None = Field(default=None, ge=0)` (was `int = Field(default=0, ge=0)`).

The `commit_count` change is the carry-in *"`commit_count=0` currently conflating 'unknown' with 'no churn'"*. Document the two readings on the field:

```python
    commit_count: int | None = Field(default=None, ge=0)
    """Commits touching this file within the history window, or None.

    The three states are genuinely different and Phase 6's `churn_on_affected`
    factor reads all three:

      None  git history was not available -- the workspace has no `.git`, or
            the repository has no commits yet. Churn is UNKNOWN, and a factor
            computed from it must lower confidence rather than report calm.
      0     history WAS read and this file was not touched in the window.
            A real, low-churn signal.
      n>0   touched n times in the window.

    The default is None, not 0: a caller that omits it has supplied no
    history, and defaulting to 0 would let "we did not look" print as
    "this file is stable".
    """
```

Also update `AffectedFile.from_sites`'s `commit_count` parameter to `int | None = None`.

- [ ] **Step 10: Write the test that pins the three states apart**

```python
def test_commit_count_distinguishes_unknown_from_no_churn() -> None:
    site = UsageSite(file="a.py", line=1, column=0, symbol="X",
                     kind=UsageKind.IMPORT, confidence=Confidence.LOW)
    unknown = AffectedFile(path="a.py", usage_sites=(site,))
    calm = AffectedFile(path="a.py", usage_sites=(site,), commit_count=0)

    assert unknown.commit_count is None
    assert calm.commit_count == 0
    # The point of the change: these must not compare equal, because a factor
    # that treats them alike reports "stable" for a repository it never read.
    assert unknown.commit_count != calm.commit_count
```

- [ ] **Step 11: `languages` becomes a tuple of records**

The carry-in: *"`RepoAnalysis.languages` is bounded but still mutable in place, and is unspecified in the spec."* Every other collection on these models is a tuple for exactly this reason (see `models/evidence.py`'s docstring). Replace the `dict` with:

```python
class LanguageShare(HonestModel):
    """One language's share of the repository's recognised source files.

    Defined here in `models/`, not in `services/analysis/layout.py` where it is
    produced: `RepoAnalysis.languages` is typed as a tuple of these, and
    `models/` must never import `services/` (CLAUDE.md rule 16).
    """

    language: NonBlankStr
    share: float = Field(gt=0.0, le=1.0)
    file_count: int = Field(ge=1)
```

`share` is `gt=0.0`, not `ge=0.0`: a language with a zero share is a language with no files, and listing it claims a presence the count contradicts.

On `RepoAnalysis`:

```python
    languages: tuple[LanguageShare, ...] = ()

    @model_validator(mode="after")
    def _language_shares_are_unique_and_total_one(self) -> Self:
        """Two constraints the `dict` could not express.

        Uniqueness: a duplicate language made the old dict silently drop one
        entry; as a tuple it would instead be double-counted by any consumer
        that sums.

        Sum: the shares are computed over files with a RECOGNISED extension,
        so they partition that set and must total 1.0. The old field
        deliberately declined to require this, because the analyzer that
        populates it did not exist and the denominator was undecided. It
        exists now (`services/analysis/layout.py`) and the denominator is
        recognised files, so the constraint is checkable rather than invented.

        `math.isclose` with an absolute tolerance, not `==`: the shares are
        floats summed over up to a few dozen languages.
        """
        names = [entry.language for entry in self.languages]
        if len(names) != len(set(names)):
            duplicated = sorted({n for n in names if names.count(n) > 1})
            raise ValueError(f"duplicate languages: {duplicated}")
        if self.languages:
            total = math.sum(entry.share for entry in self.languages)
            if not math.isclose(total, 1.0, abs_tol=1e-6):
                raise ValueError(f"language shares must total 1.0, got {total}")
        return self
```

> **Note for the implementer:** `math.sum` in the snippet above is **wrong** — there is no such function. Use `math.fsum`, which is the correct choice here anyway: it sums floats without accumulating rounding error, which is exactly what a total being compared against 1.0 needs. This is deliberately flagged rather than silently corrected so you check the snippet rather than transcribe it.

- [ ] **Step 12: Add `confidence_reducers` to `RepoAnalysis`**

Phase 2 discovers three things that make the analysis less trustworthy but are **not** unparseable Python files: git submodules whose content was never cloned, a dependency whose import root could not be guessed, and a repository where the caps stopped the walk. None can be a `SkippedFile` — `skipped_files` is divided by `total_python_files` to produce `skipped_ratio`, and a non-Python entry there both corrupts that ratio and can trip `_analyzed_and_skipped_fit_within_total`.

```python
    confidence_reducers: tuple[NonBlankStr, ...] = ()
    """Reasons this analysis is less complete than its counts suggest.

    Each entry is one user-facing sentence, consumed by Phase 6's confidence
    ceilings (spec 8.1) and printed in the report.

    Deliberately NOT `skipped_files`: that tuple is divided by
    `total_python_files` to produce `skipped_ratio`, so a `.gitmodules` entry
    there would corrupt the analysis_coverage factor and could trip
    `_analyzed_and_skipped_fit_within_total`. These are a different kind of
    fact -- "something outside the Python files we counted was not analysed" --
    and they need their own channel.
    """
```

- [ ] **Step 13: `DependencySpec` gains its canonical forms**

```python
import re

_PEP503_SEPARATORS = re.compile(r"[-_.]+")


class DependencySpec(HonestModel):
    name: NonBlankStr
    ...

    @computed_field  # type: ignore[prop-decorator]
    @property
    def canonical_name(self) -> str:
        """PEP 503 normalised name. The corpus's exact-match key.

        `PLANNING.md` carried this in with the reason that matters: the
        corpus is filtered with Chroma's `$contains`, which is exact-element,
        so a document ingested under `pydantic` is invisible to a query for
        `Pydantic` or `py_dantic`. Normalising at the boundary means every
        producer and every consumer agrees without either remembering to.

        Derived, never stored (CLAUDE.md rule 21): a stored copy could
        disagree with `name`.
        """
        return _PEP503_SEPARATORS.sub("-", self.name).lower()

    @computed_field  # type: ignore[prop-decorator]
    @property
    def import_root(self) -> str:
        """The top-level module name this distribution is expected to provide.

        A GUESS, and the honest name for it is a guess: the mapping from
        distribution name to import name lives in installed package metadata,
        which a static analysis of a cloned repository does not have.
        `pydantic` -> `pydantic` is right; `python-dateutil` -> `dateutil` and
        `PyYAML` -> `yaml` are the well-known cases where it is wrong.

        `services/analysis/analyzer.py` records an explicit confidence reducer
        whenever this guess yields no candidate files, so a wrong guess reads
        as "we could not find it" rather than as "this dependency is unused".
        """
        return self.canonical_name.replace("-", "_")
```

- [ ] **Step 14: Test the canonical forms, including the case that is a guess**

```python
@pytest.mark.parametrize(
    ("raw", "canonical", "root"),
    [
        ("pydantic", "pydantic", "pydantic"),
        ("Pydantic", "pydantic", "pydantic"),
        ("python-dateutil", "python-dateutil", "python_dateutil"),
        ("zope.interface", "zope-interface", "zope_interface"),
        ("ruamel_yaml", "ruamel-yaml", "ruamel_yaml"),
    ],
)
def test_dependency_spec_canonical_forms(raw: str, canonical: str, root: str) -> None:
    spec = DependencySpec(name=raw, current_version="1", target_version="2")
    assert spec.canonical_name == canonical
    assert spec.import_root == root


def test_import_root_is_documented_as_a_guess_and_this_case_proves_it() -> None:
    """`python-dateutil` imports as `dateutil`, not `python_dateutil`.

    This test asserts the WRONG-looking value on purpose. It is the honest
    record that `import_root` is a heuristic, so that a later reader who
    "fixes" it has to delete a test that explains why it is not a bug -- and
    so that the confidence reducer in analyzer.py cannot be dropped as
    unnecessary.
    """
    spec = DependencySpec(name="python-dateutil", current_version="1", target_version="2")
    assert spec.import_root == "python_dateutil"
    assert spec.import_root != "dateutil"
```

- [ ] **Step 15: Widen the model conformance walk to `services`**

`tests/unit/test_model_invariants.py` walks `upgradepilot.models` asserting every `BaseModel` subclass derives from `HonestModel`. This phase defines typed records inside `services/analysis/` (`Declaration`, `AliasMap`, `ModelIndex`, `ChurnIndex`, …), which that walk does not reach — so they could be plain `BaseModel`s with none of the honesty invariants and nothing would notice.

Extend the walk to cover `upgradepilot.services` as well. Keep the existing non-vacuity guard and add one for the new package:

```python
def test_every_service_model_is_an_honest_model() -> None:
    found = _walk_models("upgradepilot.services")
    assert found, "the walk found no models under services -- it is not walking anything"
    for model in found:
        assert issubclass(model, HonestModel), (
            f"{model.__module__}.{model.__qualname__} is a BaseModel but not a "
            f"HonestModel: it is missing frozen=True and the re-validating model_copy"
        )
```

Until Task 2 lands there are no models under `services`, so the non-vacuity guard **will fail**. That is correct and expected. Mark the test `@pytest.mark.xfail(reason="no service models until Task 2", strict=True)` in this task, and **Task 2's final step removes the marker**. `strict=True` means the marker itself becomes a failure the moment a service model exists, so it cannot be forgotten.

- [ ] **Step 16: Full gates, then commit**

```bash
cd backend
.venv/bin/python -m pytest -o addopts="" -q
.venv/bin/python -m ruff check . && .venv/bin/python -m ruff format --check . && .venv/bin/python -m mypy
git add -A && git commit -m "feat(models): analyzer-driven refinements — spec 8.1 factor names, aware datetimes, unknown churn, language shares, canonical dependency names"
```

**Done when:** every carried-in model finding from `PLANNING.md` is either implemented or has a written reason in the report for why it is not, the suite is green, and the three gates pass.

---

### Task 2: Manifest discovery and parsing

Five manifest kinds, three file formats (TOML, JSON, line-oriented text). Parsers are pure functions over text, so they are tested against committed fixture files with no git repository involved.

**Files:**
- Create: `backend/src/upgradepilot/services/analysis/__init__.py` (empty)
- Create: `backend/src/upgradepilot/services/analysis/manifests.py`
- Create: `backend/tests/fixtures/manifests/pyproject_pep621.toml`, `pyproject_poetry.toml`, `requirements_pinned.txt`, `requirements_ranged.txt`, `poetry.lock`, `uv.lock`, `Pipfile.lock`
- Test: `backend/tests/unit/test_analysis_manifests.py`

**Interfaces:**
- Consumes: `Workspace.iter_files`, `Workspace.read_text` (`services/repo/workspace.py`); `Manifest`, `ManifestKind`, `VersionConfidence` (`models/repo.py`, `models/enums.py`); `RepoRelativePath` (Task 1).
- Produces: `scan_manifests(workspace, canonical_name) -> ManifestScan`, and the `Declaration` / `ManifestScan` records. Task 3 consumes `ManifestScan.declarations`; Task 9 consumes `ManifestScan.manifests` for `RepoAnalysis.manifests`.

- [ ] **Step 1: Create the fixture manifest files**

These are the parser test corpus. Each declares `pydantic` and one unrelated package, so a parser that returns everything is distinguishable from one that filters.

`backend/tests/fixtures/manifests/pyproject_pep621.toml`:
```toml
[project]
name = "sample-app"
version = "0.1.0"
dependencies = [
    "pydantic>=1.10,<2",
    "requests>=2.31",
]

[project.optional-dependencies]
dev = ["pytest>=8"]
```

`backend/tests/fixtures/manifests/pyproject_poetry.toml`:
```toml
[tool.poetry]
name = "sample-app"
version = "0.1.0"

[tool.poetry.dependencies]
python = "^3.11"
Pydantic = "^1.10"
requests = "^2.31"
```

Note the capital `P` in `Pydantic` and the `python` entry: both are real Poetry idioms and both are traps. `python` is not a distribution and must never be reported; `Pydantic` must match a query for `pydantic` because matching is on the canonical name.

`backend/tests/fixtures/manifests/requirements_pinned.txt`:
```
# comment line
pydantic==1.10.13
requests==2.31.0
-r other-requirements.txt
--index-url https://example.invalid/simple
```

`backend/tests/fixtures/manifests/requirements_ranged.txt`:
```
pydantic>=1.10,<2
requests
```

`backend/tests/fixtures/manifests/poetry.lock`:
```toml
[[package]]
name = "pydantic"
version = "1.10.13"
description = "Data validation using Python type hints"
optional = false
python-versions = ">=3.7"

[[package]]
name = "requests"
version = "2.31.0"
description = "HTTP for Humans"
optional = false
python-versions = ">=3.7"

[metadata]
lock-version = "2.0"
python-versions = ">=3.11"
content-hash = "0000000000000000000000000000000000000000000000000000000000000000"
```

`backend/tests/fixtures/manifests/uv.lock`:
```toml
version = 1
requires-python = ">=3.11"

[[package]]
name = "pydantic"
version = "1.10.13"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "requests"
version = "2.31.0"
source = { registry = "https://pypi.org/simple" }
```

`backend/tests/fixtures/manifests/Pipfile.lock`:
```json
{
  "_meta": {"hash": {"sha256": "0"}, "requires": {"python_version": "3.11"}},
  "default": {
    "pydantic": {"hashes": ["sha256:0"], "index": "pypi", "version": "==1.10.13"},
    "requests": {"hashes": ["sha256:0"], "version": "==2.31.0"}
  },
  "develop": {
    "pytest": {"hashes": ["sha256:0"], "version": "==8.0.0"}
  }
}
```

Verified when this plan was written: `tomllib.load` on the two `.lock` TOMLs yields `{'pydantic': '1.10.13', 'requests': '2.31.0'}` from a `package` list of tables; `json.load` on `Pipfile.lock` yields `{'pydantic': '==1.10.13', ...}` — **the `==` is part of the string and must be stripped.**

- [ ] **Step 2: Write the failing parser tests**

`backend/tests/unit/test_analysis_manifests.py`:

```python
"""Manifest parsing. Pure functions over text -- no workspace, no git."""

from pathlib import Path

import pytest

from upgradepilot.models.enums import ManifestKind, VersionConfidence
from upgradepilot.services.analysis.manifests import (
    classify_manifest,
    parse_declaration,
)

MANIFESTS = Path(__file__).parent.parent / "fixtures" / "manifests"


@pytest.mark.parametrize(
    ("fixture", "kind", "version", "specifier", "confidence", "is_lockfile"),
    [
        ("pyproject_pep621.toml", ManifestKind.PYPROJECT,
         None, ">=1.10,<2", VersionConfidence.RANGE, False),
        ("pyproject_poetry.toml", ManifestKind.PYPROJECT,
         None, "^1.10", VersionConfidence.RANGE, False),
        ("requirements_pinned.txt", ManifestKind.REQUIREMENTS,
         "1.10.13", "==1.10.13", VersionConfidence.EXACT, False),
        ("requirements_ranged.txt", ManifestKind.REQUIREMENTS,
         None, ">=1.10,<2", VersionConfidence.RANGE, False),
        ("poetry.lock", ManifestKind.POETRY_LOCK,
         "1.10.13", None, VersionConfidence.EXACT, True),
        ("uv.lock", ManifestKind.UV_LOCK,
         "1.10.13", None, VersionConfidence.EXACT, True),
        ("Pipfile.lock", ManifestKind.PIPFILE_LOCK,
         "1.10.13", "==1.10.13", VersionConfidence.EXACT, True),
    ],
)
def test_each_manifest_kind_yields_the_expected_declaration(
    fixture: str, kind: ManifestKind, version: str | None,
    specifier: str | None, confidence: VersionConfidence, is_lockfile: bool,
) -> None:
    """One row per manifest kind the spec names. All five kinds, both
    pyproject dialects, and both requirements shapes."""
    text = (MANIFESTS / fixture).read_text(encoding="utf-8")
    manifest = Manifest(path=f"sub/{fixture}", kind=kind)

    declaration = parse_declaration(text, manifest=manifest, canonical_name="pydantic")

    assert declaration is not None, f"{fixture} declares pydantic but the parser missed it"
    assert declaration.version == version
    assert declaration.specifier == specifier
    assert declaration.confidence is confidence
    assert declaration.is_lockfile is is_lockfile


@pytest.mark.parametrize("fixture", [
    "pyproject_pep621.toml", "pyproject_poetry.toml", "requirements_pinned.txt",
    "requirements_ranged.txt", "poetry.lock", "uv.lock", "Pipfile.lock",
])
def test_no_manifest_kind_reports_a_dependency_it_does_not_declare(fixture: str) -> None:
    """The negative direction. Without this, a parser that returns a
    declaration for every query -- ignoring `canonical_name` entirely --
    passes every assertion above."""
    text = (MANIFESTS / fixture).read_text(encoding="utf-8")
    manifest = Manifest(path=f"sub/{fixture}", kind=classify_manifest(f"sub/{fixture}"))
    assert parse_declaration(text, manifest=manifest, canonical_name="numpy") is None


def test_poetry_python_entry_is_never_reported_as_a_dependency() -> None:
    """`[tool.poetry.dependencies]` lists `python = "^3.11"`. It is an
    interpreter constraint, not a distribution, and reporting it would make
    an upgrade of "python" analysable as if it were a package."""
    text = (MANIFESTS / "pyproject_poetry.toml").read_text(encoding="utf-8")
    manifest = Manifest(path="pyproject.toml", kind=ManifestKind.PYPROJECT)
    assert parse_declaration(text, manifest=manifest, canonical_name="python") is None


def test_matching_is_on_the_canonical_name_not_the_written_one() -> None:
    """The poetry fixture writes `Pydantic`, capital P. PEP 503 says that is
    the same distribution as `pydantic`, and the corpus is keyed on the
    canonical form, so a parser matching raw text would silently find
    nothing here."""
    text = (MANIFESTS / "pyproject_poetry.toml").read_text(encoding="utf-8")
    manifest = Manifest(path="pyproject.toml", kind=ManifestKind.PYPROJECT)
    declaration = parse_declaration(text, manifest=manifest, canonical_name="pydantic")
    assert declaration is not None
    assert declaration.raw_name == "Pydantic", "the name as written is kept for display"


def test_pipfile_lock_strips_the_equals_operator_from_its_version() -> None:
    """Pipfile.lock stores `"version": "==1.10.13"`. Verified against a real
    lock file shape. A parser that does not strip it produces a
    DetectedVersion of `==1.10.13`, which the report then prints as the
    installed version and which never string-compares equal to the user's
    stated `1.10.13` -- manufacturing a version discrepancy out of a parsing
    bug."""
    text = (MANIFESTS / "Pipfile.lock").read_text(encoding="utf-8")
    manifest = Manifest(path="Pipfile.lock", kind=ManifestKind.PIPFILE_LOCK)
    declaration = parse_declaration(text, manifest=manifest, canonical_name="pydantic")
    assert declaration is not None
    assert declaration.version == "1.10.13"
    assert not declaration.version.startswith("=")


@pytest.mark.parametrize("line", [
    "-r other-requirements.txt",
    "--index-url https://example.invalid/simple",
    "# comment line",
    "",
    "   ",
])
def test_requirements_directives_and_comments_are_not_dependencies(line: str) -> None:
    """A requirements file is not a list of packages; it is a list of pip
    arguments that mostly happen to be packages."""
    manifest = Manifest(path="requirements.txt", kind=ManifestKind.REQUIREMENTS)
    assert parse_declaration(line, manifest=manifest, canonical_name="other-requirements") is None


@pytest.mark.parametrize(("text", "why"), [
    ("[project\nname = 'x'", "unterminated table header"),
    ("{not json", "truncated json"),
])
def test_an_unparseable_manifest_returns_None_rather_than_raising(text: str, why: str) -> None:
    """A corrupt manifest in a user's repository must not abort the whole
    analysis: the other manifests may still answer the question. It is
    recorded by the caller (Task 9) as a confidence reducer.

    This is the one place in this package where a broad `except` is correct,
    and CLAUDE.md rule 20 still applies -- the caller turns the None into a
    recorded reason, so nothing is swallowed silently.
    """
    for kind in (ManifestKind.PYPROJECT, ManifestKind.PIPFILE_LOCK):
        manifest = Manifest(path=f"x-{kind.value}", kind=kind)
        assert parse_declaration(text, manifest=manifest, canonical_name="pydantic") is None
```

- [ ] **Step 3: Run them and confirm they fail with an import error**

```bash
cd backend && .venv/bin/python -m pytest tests/unit/test_analysis_manifests.py -o addopts="" -q
```

Expected: collection error, `ModuleNotFoundError: upgradepilot.services.analysis.manifests`.

- [ ] **Step 4: Implement `manifests.py`**

```python
"""Finding and reading the five dependency manifest kinds.

Pure over text. `scan_manifests` is the only function that touches a
Workspace; every parser below takes a string, which is what makes them
testable against committed fixture files with no git repository.

No `packaging` (CLAUDE.md rule 12): this module classifies a specifier as
exact-or-range and reports its raw text. It never compares two versions, so
a specifier grammar is not needed and a dependency for one is not justified.
"""

from __future__ import annotations

import json
import re
import tomllib
from typing import TYPE_CHECKING

from pydantic import Field

from upgradepilot.models.base import HonestModel
from upgradepilot.models.enums import ManifestKind, VersionConfidence
from upgradepilot.models.evidence import NonBlankStr, RepoRelativePath
from upgradepilot.models.repo import Manifest

if TYPE_CHECKING:
    from upgradepilot.services.repo.workspace import Workspace

_PEP503_SEPARATORS = re.compile(r"[-_.]+")

_REQUIREMENT_LINE = re.compile(
    r"""
    ^\s*
    (?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)   # distribution name
    \s*
    (?P<extras>\[[^\]]*\])?                # optional extras, discarded
    \s*
    (?P<specifier>[^;#]*?)                 # everything up to a marker or comment
    \s*
    (?:;.*)?                               # environment marker, discarded
    (?:\#.*)?                              # trailing comment, discarded
    $
    """,
    re.VERBOSE,
)

_EXACT_PIN = re.compile(r"^==\s*(?P<version>[^\s,=<>!~]+)$")

_FILENAMES: tuple[tuple[str, ManifestKind], ...] = (
    ("pyproject.toml", ManifestKind.PYPROJECT),
    ("poetry.lock", ManifestKind.POETRY_LOCK),
    ("uv.lock", ManifestKind.UV_LOCK),
    ("Pipfile.lock", ManifestKind.PIPFILE_LOCK),
)

_REQUIREMENTS_NAME = re.compile(r"^requirements.*\.txt$")


def canonicalize(name: str) -> str:
    """PEP 503. The same transform `DependencySpec.canonical_name` applies --
    matching here and keying the corpus there must agree exactly, or a
    manifest hit never reaches the documents that describe it."""
    return _PEP503_SEPARATORS.sub("-", name.strip()).lower()


class Declaration(HonestModel):
    """One dependency, as one manifest declares it."""

    manifest: Manifest
    raw_name: NonBlankStr
    """The name exactly as written. Kept for display: `Pydantic` in a
    pyproject is what the user sees in their own file, and echoing back a
    canonicalised `pydantic` makes the report look like it read a different
    file than the one on screen."""
    version: str | None = None
    """A concrete version, present only when the manifest pins one."""
    specifier: str | None = None
    """The raw specifier text, e.g. `>=1.10,<2` or `^1.10`. Never parsed
    into a grammar -- see the module docstring."""
    confidence: VersionConfidence
    is_lockfile: bool
    """Whether this manifest is machine-generated. Task 3 uses it for both
    precedence and `DependencyRole`: a dependency appearing ONLY in lock
    files is one the user does not directly control."""


class ManifestScan(HonestModel):
    manifests: tuple[Manifest, ...] = ()
    """Every manifest found, with `declared_specifier` filled in on those
    that declare the dependency. Becomes `RepoAnalysis.manifests`, which is
    how a reader sees two manifests disagreeing without this module needing
    a field to announce it."""
    declarations: tuple[Declaration, ...] = ()
    unreadable: tuple[RepoRelativePath, ...] = ()
    """Manifests that exist but could not be parsed. Task 9 turns each into
    a confidence reducer."""


def classify_manifest(path: str) -> ManifestKind | None:
    """Map a repo-relative path to its kind, or None if it is not a manifest."""
    name = path.rsplit("/", 1)[-1]
    for filename, kind in _FILENAMES:
        if name == filename:
            return kind
    if _REQUIREMENTS_NAME.match(name):
        return ManifestKind.REQUIREMENTS
    return None
```

Then the five parsers, each returning `Declaration | None`, and a `parse_declaration` dispatcher keyed on `manifest.kind`:

- **`_parse_pyproject`** — `tomllib.loads`. Read `[project].dependencies` (a list of PEP 508 strings, parsed with `_REQUIREMENT_LINE`) **and** `[tool.poetry.dependencies]` (a table of name → specifier). Skip the key `python` in the Poetry table. `[project.optional-dependencies]` is **not** read: an optional extra is not a dependency this repository has. Confidence is always `RANGE` — a pyproject specifier is a constraint, not a pin — **including** when the specifier happens to be `==1.2.3`, because that is still a declared constraint rather than the resolved install. Record `version=None`, `specifier=<raw text>`.
- **`_parse_requirements`** — line-oriented. Skip blanks, `#` comments, and any line starting `-` (that covers `-r`, `-e`, `--index-url`, `--find-links`). Match `_REQUIREMENT_LINE`; canonicalize the name; compare. If `_EXACT_PIN` matches the specifier, `confidence=EXACT` and `version=<the captured version>`; otherwise `confidence=RANGE` and `version=None`. Both carry the raw `specifier`.
- **`_parse_toml_lock`** — shared by `poetry.lock` and `uv.lock` because both expose `package` as a list of tables with a bare `version` (verified). Match on `canonicalize(entry["name"])`. `confidence=EXACT`, `specifier=None`, `is_lockfile=True`.
- **`_parse_pipfile_lock`** — `json.loads`; search `default` then `develop`. **Strip the leading `==`** from `version`. `confidence=EXACT`, `specifier=<raw, with the operator>`, `is_lockfile=True`.

Every parser wraps its decode in:

```python
    try:
        data = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, ValueError):
        return None
```

`ValueError` as well as `TOMLDecodeError` because the former is the latter's base class in some releases and catching only the subclass is the kind of assumption this project has been burned by. `json.JSONDecodeError` is likewise a `ValueError`.

- [ ] **Step 5: Implement `scan_manifests`**

```python
def scan_manifests(workspace: Workspace, canonical_name: str) -> ManifestScan:
    """Find every manifest in the workspace and read this dependency out of it.

    `iter_files("")` rather than a per-suffix walk: the five kinds span three
    extensions, and the Workspace's single walk already applies the vendor
    skip list and the symlink containment check. Filtering the result is
    cheaper and safer than three walks that each need those rules reapplied.
    """
    manifests: list[Manifest] = []
    declarations: list[Declaration] = []
    unreadable: list[str] = []

    for relative in workspace.iter_files(""):
        path = relative.as_posix()
        kind = classify_manifest(path)
        if kind is None:
            continue
        try:
            text = workspace.read_text(relative)
        except (OSError, UnicodeDecodeError):
            unreadable.append(path)
            manifests.append(Manifest(path=path, kind=kind))
            continue

        bare = Manifest(path=path, kind=kind)
        declaration = parse_declaration(text, manifest=bare, canonical_name=canonical_name)
        if declaration is None:
            manifests.append(bare)
            continue
        filled = Manifest(path=path, kind=kind, declared_specifier=declaration.specifier)
        manifests.append(filled)
        declarations.append(declaration.model_copy(update={"manifest": filled}))

    return ManifestScan(
        manifests=tuple(sorted(manifests, key=lambda m: m.path)),
        declarations=tuple(sorted(declarations, key=lambda d: d.manifest.path)),
        unreadable=tuple(sorted(unreadable)),
    )
```

`model_copy(update=...)` is safe here and only here: `HonestModel` overrides it to route the update through `model_validate` (see `models/base.py`), so the replacement `Manifest` is validated rather than smuggled in.

- [ ] **Step 6: Add the workspace-level test**

```python
def test_scan_manifests_finds_both_manifests_in_the_sample_repo(tmp_path: Path) -> None:
    root = build_sample_repo(tmp_path)
    scan = scan_manifests(Workspace(root), canonical_name="pydantic")

    assert tuple(m.path for m in scan.manifests) == ("pyproject.toml", "requirements.txt")
    assert {d.manifest.kind for d in scan.declarations} == {
        ManifestKind.PYPROJECT, ManifestKind.REQUIREMENTS,
    }
    assert scan.unreadable == ()


def test_scan_manifests_ignores_files_that_merely_look_like_manifests(tmp_path: Path) -> None:
    """`my-requirements.txt.bak`, `pyproject.toml.orig` and a `requirements/`
    DIRECTORY are all things real repositories contain. Matching on a
    substring rather than the whole filename picks up all three."""
    root = build_sample_repo(tmp_path)
    (root / "pyproject.toml.orig").write_text("[project]\n", encoding="utf-8")
    (root / "notes-requirements.txt.bak").write_text("pydantic==9\n", encoding="utf-8")

    scan = scan_manifests(Workspace(root), canonical_name="pydantic")
    assert tuple(m.path for m in scan.manifests) == ("pyproject.toml", "requirements.txt")
```

> `notes-requirements.txt.bak` must **not** match. Check your `_REQUIREMENTS_NAME` regex is anchored at both ends — `^requirements.*\.txt$` rejects it because of the `.bak` suffix, but it also rejects `dev-requirements.txt`, which real repositories do use. Decide which you want, write the decision into the regex's docstring, and add the case you chose to the parametrised test either way. Do not leave it undecided.

- [ ] **Step 7: Remove the `xfail` marker Task 1 left on the service conformance walk**

`Declaration` and `ManifestScan` are the first models under `services`, so `test_every_service_model_is_an_honest_model` now has something to walk. Delete the `@pytest.mark.xfail` line. If the test then fails, one of your new models does not derive from `HonestModel` — fix the model, not the test.

- [ ] **Step 8: Gates, then commit**

```bash
cd backend
.venv/bin/python -m pytest -o addopts="" -q
.venv/bin/python -m ruff check . && .venv/bin/python -m ruff format --check . && .venv/bin/python -m mypy
git add -A && git commit -m "feat(analysis): manifest discovery and parsing across all five kinds"
```

**Done when:** all five manifest kinds parse, both pyproject dialects work, the negative direction is tested for every kind, and `scan_manifests` reads the sample repository.

---

### Task 3: Version precedence, confidence and dependency role

**Files:**
- Create: `backend/src/upgradepilot/services/analysis/versions.py`
- Test: `backend/tests/unit/test_analysis_versions.py`

**Interfaces:**
- Consumes: `Declaration` (Task 2); `DetectedVersion`, `DependencyRole`, `VersionConfidence` (`models/`); `DependencyNotFoundError` (`models/errors.py`, already exists, `http_status = 422`).
- Produces: `resolve_version(declarations, *, canonical_name) -> DetectedVersion`. Task 9 calls it once.

- [ ] **Step 1: Write the failing tests**

```python
"""Version precedence. Pure over Declaration records -- no files, no workspace."""

import pytest

from upgradepilot.models.enums import (
    DependencyRole, ManifestKind, VersionConfidence,
)
from upgradepilot.models.errors import DependencyNotFoundError
from upgradepilot.models.repo import Manifest
from upgradepilot.services.analysis.manifests import Declaration
from upgradepilot.services.analysis.versions import resolve_version


def _declaration(kind: ManifestKind, *, version=None, specifier=None,
                 confidence=VersionConfidence.EXACT, is_lockfile=False) -> Declaration:
    return Declaration(
        manifest=Manifest(path=f"{kind.value}-manifest", kind=kind,
                          declared_specifier=specifier),
        raw_name="pydantic", version=version, specifier=specifier,
        confidence=confidence, is_lockfile=is_lockfile,
    )


def test_no_declaration_raises_rather_than_guessing() -> None:
    """Spec 7.1: "absent -> DependencyNotFound error -- never a guess."

    The alternative -- returning None and letting the graph continue -- would
    produce a migration plan for a dependency the repository does not use,
    every claim in it correctly cited and the whole thing about the wrong
    subject.
    """
    with pytest.raises(DependencyNotFoundError) as caught:
        resolve_version((), canonical_name="pydantic")
    assert "pydantic" in caught.value.args[0]


def test_a_lockfile_pin_beats_a_pyproject_range() -> None:
    """The lock file records what is actually installed; the pyproject
    records what is permitted. The report says "you are on 1.10.13", which
    is only true of the former."""
    detected = resolve_version(
        (
            _declaration(ManifestKind.PYPROJECT, specifier=">=1.10,<2",
                         confidence=VersionConfidence.RANGE),
            _declaration(ManifestKind.POETRY_LOCK, version="1.10.13", is_lockfile=True),
        ),
        canonical_name="pydantic",
    )
    assert detected.value == "1.10.13"
    assert detected.confidence is VersionConfidence.EXACT
    assert detected.source_manifest.kind is ManifestKind.POETRY_LOCK


def test_an_exact_requirements_pin_beats_a_pyproject_range() -> None:
    detected = resolve_version(
        (
            _declaration(ManifestKind.PYPROJECT, specifier="^1.10",
                         confidence=VersionConfidence.RANGE),
            _declaration(ManifestKind.REQUIREMENTS, version="1.10.13",
                         specifier="==1.10.13"),
        ),
        canonical_name="pydantic",
    )
    assert detected.value == "1.10.13"
    assert detected.confidence is VersionConfidence.EXACT


def test_a_range_only_declaration_reports_the_specifier_as_the_value() -> None:
    """There is no pin to report, so the specifier IS the most precise true
    statement available. Reporting a resolved-looking version here would be
    the guess spec 7.1 forbids."""
    detected = resolve_version(
        (_declaration(ManifestKind.PYPROJECT, specifier=">=1.10,<2",
                      confidence=VersionConfidence.RANGE),),
        canonical_name="pydantic",
    )
    assert detected.value == ">=1.10,<2"
    assert detected.specifier == ">=1.10,<2"
    assert detected.confidence is VersionConfidence.RANGE


def test_lockfile_only_means_the_user_does_not_control_the_pin() -> None:
    """Spec 7.1's TRANSITIVE_ONLY case, and the reason it exists: pydantic is
    usually pulled in by FastAPI rather than declared. "You do not directly
    control this pin" changes the migration story and caps confidence."""
    detected = resolve_version(
        (_declaration(ManifestKind.POETRY_LOCK, version="1.10.13", is_lockfile=True),),
        canonical_name="pydantic",
    )
    assert detected.role is DependencyRole.TRANSITIVE_ONLY


def test_a_human_authored_manifest_means_direct() -> None:
    for kind in (ManifestKind.PYPROJECT, ManifestKind.REQUIREMENTS):
        detected = resolve_version(
            (
                _declaration(kind, version="1.10.13", specifier="==1.10.13"),
                _declaration(ManifestKind.POETRY_LOCK, version="1.10.13", is_lockfile=True),
            ),
            canonical_name="pydantic",
        )
        assert detected.role is DependencyRole.DIRECT, kind


def test_two_lockfiles_disagreeing_resolve_deterministically() -> None:
    """A repository mid-migration between package managers really does carry
    both. Precedence must not depend on iteration order, because the version
    the report prints would otherwise change between runs on the same input."""
    forward = resolve_version(
        (
            _declaration(ManifestKind.POETRY_LOCK, version="1.10.13", is_lockfile=True),
            _declaration(ManifestKind.UV_LOCK, version="1.9.0", is_lockfile=True),
        ),
        canonical_name="pydantic",
    )
    backward = resolve_version(
        (
            _declaration(ManifestKind.UV_LOCK, version="1.9.0", is_lockfile=True),
            _declaration(ManifestKind.POETRY_LOCK, version="1.10.13", is_lockfile=True),
        ),
        canonical_name="pydantic",
    )
    assert forward.value == backward.value
    assert forward.source_manifest.kind is backward.source_manifest.kind
```

- [ ] **Step 2: Run them, confirm they fail**

```bash
cd backend && .venv/bin/python -m pytest tests/unit/test_analysis_versions.py -o addopts="" -q
```

- [ ] **Step 3: Implement `versions.py`**

```python
"""Choosing one version from several manifests that each claim one.

The precedence order is spec 7.1's table, made total so that a repository
carrying three manifests resolves the same way on every run.
"""

from __future__ import annotations

from upgradepilot.models.enums import DependencyRole, ManifestKind, VersionConfidence
from upgradepilot.models.errors import DependencyNotFoundError
from upgradepilot.models.repo import DetectedVersion
from upgradepilot.services.analysis.manifests import Declaration

_KIND_ORDER: dict[ManifestKind, int] = {
    ManifestKind.POETRY_LOCK: 0,
    ManifestKind.UV_LOCK: 1,
    ManifestKind.PIPFILE_LOCK: 2,
    ManifestKind.REQUIREMENTS: 3,
    ManifestKind.PYPROJECT: 4,
}
"""Total order over manifest kinds, tie-breaking within a confidence tier.

Locks before human-authored files because a lock records what IS installed
while a specifier records what is PERMITTED, and the report's sentence is
"you are currently on X". The three lock kinds are ordered arbitrarily but
FIXED: their relative order carries no meaning, and having one is what makes
a repository holding two of them resolve identically on every run rather
than by dict iteration order.
"""

_HUMAN_AUTHORED = frozenset({ManifestKind.PYPROJECT, ManifestKind.REQUIREMENTS})


def _rank(declaration: Declaration) -> tuple[int, int, str]:
    """Sort key: confidence tier, then kind, then path.

    The path is the final tie-break so that two `requirements*.txt` files
    declaring the same package resolve deterministically -- without it the
    order is the filesystem walk's, which differs across platforms.
    """
    exact_first = 0 if declaration.confidence is VersionConfidence.EXACT else 1
    return (exact_first, _KIND_ORDER[declaration.manifest.kind], declaration.manifest.path)


def resolve_version(
    declarations: tuple[Declaration, ...], *, canonical_name: str
) -> DetectedVersion:
    """Pick the most precise true statement about the installed version.

    Raises DependencyNotFoundError when nothing declares it -- spec 7.1's
    "never a guess". The caller (analyzer.py) does not catch it: a run for a
    dependency the repository does not use has no honest output.
    """
    if not declarations:
        raise DependencyNotFoundError(
            f"{canonical_name!r} is not declared in any dependency manifest in this "
            f"repository, so there is no current version to upgrade from.",
            detail=f"canonical_name={canonical_name!r} declarations=0",
        )

    best = min(declarations, key=_rank)
    role = (
        DependencyRole.DIRECT
        if any(d.manifest.kind in _HUMAN_AUTHORED for d in declarations)
        else DependencyRole.TRANSITIVE_ONLY
    )
    value = best.version if best.version is not None else best.specifier
    if value is None:  # pragma: no cover -- a Declaration always carries one
        raise DependencyNotFoundError(
            f"{canonical_name!r} is declared in {best.manifest.path} with neither a "
            f"version nor a specifier.",
            detail=f"manifest={best.manifest.path} kind={best.manifest.kind.value}",
        )

    return DetectedVersion(
        value=value,
        specifier=best.specifier,
        source_manifest=best.manifest,
        confidence=best.confidence,
        role=role,
    )
```

Note the `role` rule: it is computed over **all** declarations, not the winning one. A repository whose `pyproject.toml` declares the dependency and whose `poetry.lock` pins it is `DIRECT` even though the lock file wins on precedence — the user does control that pin, they just do not write the resolved number themselves.

Document the known over-report on the function:

> A `requirements.txt` produced by `pip-compile` lists transitive pins in the same shape as direct ones, so a dependency that is genuinely transitive reads as `DIRECT` there. Distinguishing them needs the `# via` comments pip-compile writes, which are not part of the requirements format and are absent from a hand-written file. Reported as `DIRECT`, which is the safer error: it understates how constrained the user is rather than overstating it.

- [ ] **Step 4: Gates, then commit**

```bash
cd backend
.venv/bin/python -m pytest -o addopts="" -q
.venv/bin/python -m ruff check . && .venv/bin/python -m ruff format --check . && .venv/bin/python -m mypy
git add -A && git commit -m "feat(analysis): version precedence, confidence and dependency role"
```

**Done when:** every row of spec §7.1's precedence table has a test, the absent case raises, and the two-lockfile case resolves identically in both argument orders.

---

### Task 4: Import alias map

Everything downstream asks the same question — *does this local name refer to the dependency?* — and it has four spellings: `import pydantic`, `import pydantic as pyd`, `from pydantic import BaseModel`, `from pydantic.v1 import BaseModel as BM`. One module answers it for all of them.

**Files:**
- Create: `backend/src/upgradepilot/services/analysis/imports.py`
- Test: `backend/tests/unit/test_analysis_imports.py`

**Interfaces:**
- Consumes: `ast` (stdlib), `HonestModel`, `NonBlankStr`.
- Produces: `AliasMap.from_module(tree)`, `.origin_of(local)`, `.root_of(local)`, `.entries`. Tasks 6 and 7 both depend on it.

- [ ] **Step 1: Write the failing tests**

```python
"""Alias resolution. Pure over an ast.Module -- no files, no workspace."""

import ast

import pytest

from upgradepilot.services.analysis.imports import AliasMap


def _map(source: str) -> AliasMap:
    return AliasMap.from_module(ast.parse(source))


@pytest.mark.parametrize(
    ("source", "local", "origin", "root"),
    [
        ("import pydantic",                    "pydantic",  "pydantic",            "pydantic"),
        ("import pydantic as pyd",             "pyd",       "pydantic",            "pydantic"),
        ("import pydantic.dataclasses",        "pydantic",  "pydantic.dataclasses","pydantic"),
        ("import pydantic.dataclasses as pd",  "pd",        "pydantic.dataclasses","pydantic"),
        ("from pydantic import BaseModel",     "BaseModel", "pydantic.BaseModel",  "pydantic"),
        ("from pydantic import BaseModel as B","B",         "pydantic.BaseModel",  "pydantic"),
        ("from pydantic.v1 import validator",  "validator", "pydantic.v1.validator","pydantic"),
        ("from app.models import Customer",    "Customer",  "app.models.Customer", "app"),
        ("import os.path",                     "os",        "os.path",             "os"),
    ],
)
def test_every_import_spelling_resolves(source, local, origin, root) -> None:
    aliases = _map(source)
    assert aliases.origin_of(local) == origin
    assert aliases.root_of(local) == root


def test_import_dotted_without_as_binds_only_the_top_package() -> None:
    """`import pydantic.dataclasses` binds the name `pydantic`, NOT
    `pydantic.dataclasses`. Getting this backwards means every
    `pydantic.dataclasses.dataclass(...)` reference fails to resolve, because
    the lookup is on the bound name and the bound name is the short one.
    """
    aliases = _map("import pydantic.dataclasses")
    assert aliases.origin_of("pydantic") == "pydantic.dataclasses"
    assert aliases.origin_of("pydantic.dataclasses") is None


def test_a_name_that_was_never_imported_resolves_to_None() -> None:
    aliases = _map("import pydantic")
    assert aliases.origin_of("BaseModel") is None
    assert aliases.root_of("BaseModel") is None


def test_relative_imports_are_recorded_with_their_level_and_never_guessed() -> None:
    """`from . import models` and `from ..pkg import thing` cannot be
    resolved to an absolute dotted path without knowing where the file sits
    in the package tree, and this module deliberately does not take that
    argument.

    They are recorded with `is_relative=True` and a None origin rather than
    being resolved wrongly. `root_of` returns None, so a relative import can
    never be mistaken for an import of the dependency -- which is the safe
    direction: it costs a missed finding, not a fabricated one.
    """
    aliases = _map("from . import models\nfrom ..pkg import thing")
    assert aliases.origin_of("models") is None
    assert aliases.root_of("models") is None
    assert [e.local for e in aliases.entries if e.is_relative] == ["models", "thing"]


def test_star_imports_are_recorded_but_bind_no_name() -> None:
    """`from pydantic import *` binds names this module cannot enumerate
    without importing pydantic, which a static analyzer must not do. It is
    recorded so Task 9 can raise it as a confidence reducer, and binds
    nothing -- again failing toward a missed finding rather than a wrong one.
    """
    aliases = _map("from pydantic import *")
    assert aliases.origin_of("*") is None
    assert aliases.has_star_import_from("pydantic") is True


def test_a_later_import_shadows_an_earlier_one() -> None:
    """Real files rebind names, usually in a `try/except ImportError` block.
    Python's own semantics are last-wins at module level; anything else
    reports a line the reader can check against and disagrees with."""
    aliases = _map("from pydantic import BaseModel\nfrom typing import BaseModel")
    assert aliases.origin_of("BaseModel") == "typing.BaseModel"


def test_entries_carry_the_line_and_column_of_the_import() -> None:
    """These become UsageSite citations in Task 7, so they must point at the
    import statement itself."""
    aliases = _map("import json\nimport pydantic\n")
    entry = next(e for e in aliases.entries if e.local == "pydantic")
    assert (entry.line, entry.column) == (2, 0)
```

- [ ] **Step 2: Run, confirm failure, implement `imports.py`**

```python
"""Local name -> dotted origin, for one module.

Module scope only. A name imported inside a function body is recorded like
any other -- Python's own scoping would shadow it, and modelling that
correctly needs a scope tree this phase does not build. The consequence is
over-binding in a rare case, which can only produce a finding at the import's
own line, and that line is real.
"""

from __future__ import annotations

import ast
from typing import Self

from pydantic import Field

from upgradepilot.models.base import HonestModel
from upgradepilot.models.evidence import NonBlankStr


class AliasEntry(HonestModel):
    local: NonBlankStr
    origin: str | None = None
    """Absolute dotted path the local name refers to, or None when it cannot
    be known statically (a relative import, or a star import's contents)."""
    line: int = Field(ge=1)
    column: int = Field(ge=0)
    is_module: bool
    """True for `import x`, False for `from x import y`. Task 7 needs the
    distinction: `pydantic.BaseModel` is an attribute access on a module,
    while a bare `BaseModel` is a name."""
    is_relative: bool = False
    is_star: bool = False


class AliasMap(HonestModel):
    entries: tuple[AliasEntry, ...] = ()

    @classmethod
    def from_module(cls, tree: ast.Module) -> Self:
        ...

    def origin_of(self, local_name: str) -> str | None:
        """Last binding wins, matching Python's own module-level semantics."""
        ...

    def root_of(self, local_name: str) -> str | None:
        """The top-level package of `origin_of`, or None."""
        ...

    def has_star_import_from(self, root: str) -> bool:
        ...
```

`from_module` walks `tree.body` **and** nested bodies with `ast.walk` (imports inside `try:` blocks are the common real case), building one `AliasEntry` per bound name:

- `ast.Import`: for each alias, `local = alias.asname or alias.name.split(".")[0]`, `origin = alias.name`, `is_module=True`.
- `ast.ImportFrom` with `node.level > 0`: `is_relative=True`, `origin=None`.
- `ast.ImportFrom` with `alias.name == "*"`: `is_star=True`, `local="*"`, `origin=None`, and record `node.module` so `has_star_import_from` can answer.
- `ast.ImportFrom` otherwise: `local = alias.asname or alias.name`, `origin = f"{node.module}.{alias.name}"`, `is_module=False`.

`origin_of` must skip entries whose `origin` is None so a relative import never shadows a real one with a None.

- [ ] **Step 3: Gates, then commit**

```bash
cd backend && .venv/bin/python -m pytest -o addopts="" -q && .venv/bin/python -m ruff check . && .venv/bin/python -m ruff format --check . && .venv/bin/python -m mypy
git add -A && git commit -m "feat(analysis): import alias map across every import spelling"
```

**Done when:** all nine spellings resolve, relative and star imports bind nothing and are recorded, and shadowing follows Python's last-wins rule.

---

### Task 5: Two-phase candidate selection

Spec §7.1's byte prefilter, plus the second phase Deviation 2 requires. This task also owns decoding and parsing, because a file that cannot be decoded and a file that cannot be parsed are both `SkippedFile`s and both belong to selection rather than to grading.

**Files:**
- Create: `backend/src/upgradepilot/services/analysis/candidates.py`
- Modify: `backend/tests/fixtures/sample_repo/src/app/consumer.py` (new fixture file — see Step 1)
- Modify: `backend/tests/fixtures/repo_builder.py`
- Test: `backend/tests/unit/test_analysis_candidates.py`

**Interfaces:**
- Consumes: `Workspace.iter_files`, `Workspace.read_text`; `SkippedFile` (`models/repo.py`).
- Produces: `ParsedModule`, `CandidateScan`, `select_candidates(workspace, *, import_root)`, `expand_candidates(workspace, scan, *, model_names)`. Tasks 6, 7 and 9 all consume `ParsedModule`.

- [ ] **Step 1: Add the fixture file that produces a genuine low-confidence site**

The fixture has no low-confidence site today. `util.py` is a trap for the *substring* filter — it is never selected as a candidate at all, so it can never produce a graded site. Without a low-tier fixture the `Confidence.LOW` branch of Task 7 is untestable against the real tree.

Create `backend/tests/fixtures/sample_repo/src/app/consumer.py`:

```python
"""Selected as a candidate (it names a model class), but its call receiver
cannot be resolved: low confidence."""

from app.models import Customer


def build(rows: list) -> list:
    return [Customer(**row) for row in rows]


def summarise(anything) -> dict:
    return anything.dict()      # receiver is unannotated: LOW, not MEDIUM
```

`anything` has no annotation, so the receiver resolves to nothing. It is selected (phase B finds `Customer`), it is graded (a tracked method name), and it lands in the low tier — which is exactly what "the same calls elsewhere" means once receivers are what decide the tier.

Update `repo_builder.py`:

```python
EXPECTED_PYTHON_FILES = 7  # was 6: consumer.py added for the low-confidence tier
EXPECTED_LOW_CONFIDENCE_SITE = ("src/app/consumer.py", "dict")
```

Task 10 binds `EXPECTED_LOW_CONFIDENCE_SITE` to the analyzer's real output. Do **not** add a constant here without the assertion that binds it — `repo_builder.py`'s own comment says so, and three constants in this file were once unasserted.

- [ ] **Step 2: Write the failing tests**

```python
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
```

- [ ] **Step 3: Implement `candidates.py`**

```python
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
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, replace
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
    model; a wrong guess costs a missed medium grade, never a wrong citation."""
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
```

`_dotted_module(path)` strips a leading `src/` segment, drops the `.py`, replaces `/` with `.`, and drops a trailing `.__init__`. Document that `src/` is the only stripped prefix and that a repository using a different layout gets a dotted name that matches nothing — costing a missed medium grade, never a wrong one.

`_parse(workspace, relative)` returns `ParsedModule | SkippedFile`:

```python
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
```

`ValueError` covers a source containing a NUL byte, which `ast.parse` raises on rather than `SyntaxError`; `RecursionError` covers pathologically nested expressions. Neither may propagate — CLAUDE.md rule 20 requires a recorded outcome, and a `SkippedFile` is that record.

> **Do not put the exception object in `reason` with `{exc!r}`.** `SyntaxError.__repr__` embeds the absolute filename, which would put an absolute path into a `RepoRelativePath`-adjacent user-facing string. `exc.msg` and `exc.lineno` are the known-shape fields. This is the same rule `services/repo/guards.py` follows for `OSError`.

Phase A selection: read bytes via `(workspace.root / relative).read_bytes()` and test `import_root.encode("utf-8") in data`. Bytes, not text, so an undecodable file costs one membership test rather than a decode attempt — and a file that *is* undecodable but contains the marker still becomes a `SkippedFile`, which is the honest record.

Phase B: for each path in `scanned_files` not already a module or a skip, byte-test against any `name.encode("utf-8")` in `model_names`; parse the hits; return a new `CandidateScan` merging both sets, preserving `total_python_files`, and sorting `modules` by `file` so output order is stable.

- [ ] **Step 4: Gates, then commit**

```bash
cd backend && .venv/bin/python -m pytest -o addopts="" -q && .venv/bin/python -m ruff check . && .venv/bin/python -m ruff format --check . && .venv/bin/python -m mypy
git add -A && git commit -m "feat(analysis): two-phase candidate selection with recorded skips"
```

**Done when:** the docstring-removal regression test passes, the unparseable and undecodable files become `SkippedFile`s, and no file is parsed twice.

---

### Task 6: Model index (pass 1)

**Files:**
- Create: `backend/src/upgradepilot/services/analysis/models_index.py`
- Test: `backend/tests/unit/test_analysis_models_index.py`

**Interfaces:**
- Consumes: `ParsedModule` (Task 5), `AliasMap` (Task 4).
- Produces: `ModelClass`, `ModelIndex`, `build_model_index(modules, *, import_root)`. Task 7 grades every method call against it.

- [ ] **Step 1: Write the failing tests**

```python
def _module(path: str, source: str) -> ParsedModule:
    return ParsedModule(file=path, dotted_module=path[:-3].replace("/", "."),
                        source=source, tree=ast.parse(source))


def test_a_class_deriving_from_the_dependency_is_indexed() -> None:
    index = build_model_index(
        (_module("m.py", "from pydantic import BaseModel\nclass C(BaseModel):\n    x: int\n"),),
        import_root="pydantic",
    )
    assert index.names() == frozenset({"C"})
    entry = index.classes[0]
    assert (entry.file, entry.name, entry.line, entry.base_symbol) == ("m.py", "C", 2, "BaseModel")


@pytest.mark.parametrize("source", [
    "import pydantic\nclass C(pydantic.BaseModel):\n    x: int\n",
    "import pydantic as pyd\nclass C(pyd.BaseModel):\n    x: int\n",
    "from pydantic import BaseModel as B\nclass C(B):\n    x: int\n",
    "from pydantic.generics import GenericModel\nclass C(GenericModel):\n    x: int\n",
])
def test_every_way_of_naming_the_base_is_indexed(source: str) -> None:
    index = build_model_index((_module("m.py", source),), import_root="pydantic")
    assert index.names() == frozenset({"C"})


def test_a_class_with_no_dependency_base_is_not_indexed() -> None:
    """`util.py`'s `Bag` in miniature. This is the whole reason the index
    exists: without it, `bag.dict()` and `invoice.dict()` are the same
    expression shape."""
    index = build_model_index((_module("m.py", "class Bag:\n    def dict(self): ...\n"),),
                              import_root="pydantic")
    assert index.names() == frozenset()


def test_a_subclass_of_an_indexed_model_is_itself_indexed() -> None:
    """Real projects define `class Base(BaseModel)` once and subclass it
    everywhere. Indexing only direct subclasses would grade every real model
    in such a project as low confidence."""
    index = build_model_index(
        (
            _module("base.py", "from pydantic import BaseModel\nclass Base(BaseModel):\n    x: int\n"),
            _module("app.py", "from base import Base\nclass Customer(Base):\n    y: int\n"),
        ),
        import_root="pydantic",
    )
    assert index.names() == frozenset({"Base", "Customer"})


def test_transitive_indexing_terminates_on_a_cycle() -> None:
    """`class A(B)` in one file and `class B(A)` in another is not valid
    Python at runtime, but it is perfectly parseable, and a user's repository
    is untrusted input. A fixed-point loop with no visited set hangs the
    analysis here, and a hang is not an error the run can report."""
    index = build_model_index(
        (
            _module("a.py", "from b import B\nclass A(B):\n    pass\n"),
            _module("b.py", "from a import A\nclass B(A):\n    pass\n"),
        ),
        import_root="pydantic",
    )
    assert index.names() == frozenset()


def test_the_sample_repo_indexes_exactly_its_two_models(tmp_path: Path) -> None:
    workspace = Workspace(build_sample_repo(tmp_path))
    scan = select_candidates(workspace, import_root="pydantic")
    index = build_model_index(scan.modules, import_root="pydantic")
    assert index.names() == frozenset({"Customer", "Invoice"})


def test_is_model_class_resolves_a_first_party_import() -> None:
    index = build_model_index(
        (_module("app/models.py", "from pydantic import BaseModel\nclass Customer(BaseModel):\n    x: int\n"),),
        import_root="pydantic",
    )
    assert index.is_model_class("app.models.Customer") is True
    assert index.is_model_class("app.models.Bag") is False
```

- [ ] **Step 2: Implement `models_index.py`**

```python
"""Pass 1: which classes in this repository derive from the dependency.

Task 7 grades a method call by what its receiver resolves to, and this is
what "resolves to a model" means. Built over the whole candidate set at once,
because a model defined in one file is subclassed in another.

Fixed-point, not single-pass: `class Base(BaseModel)` in one module and
`class Customer(Base)` in another is the ordinary shape of a real project,
and a single pass over an arbitrary file order finds one or the other
depending on which came first.
"""
```

`ModelClass` fields: `file: RepoRelativePath`, `dotted_module: NonBlankStr`, `name: NonBlankStr`, `line: int (ge=1)`, `column: int (ge=0)`, `base_symbol: NonBlankStr`.

`ModelIndex` holds `classes: tuple[ModelClass, ...]` plus `names()` and `is_model_class(dotted)`.

Algorithm:

1. Build an `AliasMap` per module once, and a `{dotted_module: [ClassDef]}` collection using a `NodeVisitor` that records only **top-level** classes (a class defined inside a function is not importable by a dotted name, so it cannot be a receiver's resolved type).
2. Seed: a `ClassDef` is a model when any base resolves, via that module's `AliasMap`, to a dotted path whose root equals `import_root`. Two base shapes:
   - `ast.Name` — `aliases.root_of(base.id) == import_root`
   - `ast.Attribute` — flatten to a dotted string (`pydantic.BaseModel`), take its leftmost name, and check `aliases.root_of(leftmost) == import_root`
3. Iterate to a fixed point: a `ClassDef` is a model when any base resolves to a dotted path already in the index. Track a `visited` set of `(dotted_module, class_name)` and stop when a full pass adds nothing. **The cycle test above is what proves the loop terminates** — write the loop as `while True: ... if not added: break`, never as recursion over bases.
4. Sort the result by `(file, line)` so the index is order-stable.

`base_symbol` is the base **as written** (`BaseModel`, `pyd.BaseModel`, `Base`), which Task 7 uses as the `UsageSite.symbol` for a `MODEL_DEFINITION` site. For a transitively-indexed class the base symbol is the first-party base — see Task 7 Step 4 for why that is deliberately *not* what gets reported as the symbol.

- [ ] **Step 3: Gates, then commit**

```bash
cd backend && .venv/bin/python -m pytest -o addopts="" -q && .venv/bin/python -m ruff check . && .venv/bin/python -m ruff format --check . && .venv/bin/python -m mypy
git add -A && git commit -m "feat(analysis): repository-wide model index with transitive bases"
```

**Done when:** all four base spellings index, transitive subclassing indexes, a cycle terminates, and the sample repository yields exactly `Customer` and `Invoice`.

---

### Task 7: Usage detection and confidence grading (pass 2)

The heart of the phase. Spec §7.1's two tables, made executable. Every site produced here becomes a `RepoEvidence` citation in the final report, so a wrong `line` is a wrong claim.

**Files:**
- Create: `backend/src/upgradepilot/services/analysis/usage.py`
- Test: `backend/tests/unit/test_analysis_usage.py`

**Interfaces:**
- Consumes: `ParsedModule` (Task 5), `AliasMap` (Task 4), `ModelIndex` (Task 6); `UsageSite`, `UsageKind`, `Confidence` (`models/`).
- Produces: `TRACKED_METHODS`, `detect_usage(module, *, import_root, index) -> tuple[UsageSite, ...]`. Task 9 calls it once per module.

**The grading table, restated as this task implements it.** Where it departs from spec §7.1, the departure is Deviation 1 and is marked.

| Construct | `UsageKind` | Symbol reported | Confidence |
|---|---|---|---|
| `import pydantic`, `from pydantic import X` | `IMPORT` | the imported name (`pydantic`, `X`) | `LOW` |
| `class C(<dep>.BaseModel)` | `MODEL_DEFINITION` | the dependency symbol (`BaseModel`) | `HIGH` |
| `@<dep>.validator(...)`, `@<dep>.root_validator` | `DECORATOR` | the dependency symbol (`validator`) | `HIGH` |
| `class Config:` directly inside an indexed model | `NESTED_CONFIG` | `Config` | `HIGH` |
| `x: Optional[T]` with **no default**, inside an indexed model | `OPTIONAL_FIELD` | `Optional` | `HIGH` |
| tracked method call, receiver resolves to an indexed model | `METHOD_CALL` | the method name | `MEDIUM` *(Deviation 1)* |
| tracked method call, receiver does not resolve | `METHOD_CALL` | the method name | `LOW` *(Deviation 1)* |

`IMPORT` is graded `LOW` deliberately. An import proves the dependency is reachable, not that any breaking behaviour is exercised, and `Confidence.HIGH` is the gate for both the RAG sufficiency check (§7.3) and the `evidence_coverage` factor (§8.1). Grading imports high would make every dependency's every imported name a high-confidence symbol demanding corpus coverage, and `evidence_coverage` would collapse toward zero on repositories that use the dependency lightly. Nothing is lost: `SymbolInventory.from_sites` already takes a symbol's confidence to be the **best** of its sites, so `validator` is high because of its decorator site regardless of its import site.

- [ ] **Step 1: Write the failing tests for the four high-confidence kinds**

```python
"""Usage detection. Pure over a ParsedModule -- no workspace, no git."""

import ast

import pytest

from upgradepilot.models.enums import Confidence, UsageKind
from upgradepilot.services.analysis.candidates import ParsedModule
from upgradepilot.services.analysis.models_index import build_model_index
from upgradepilot.services.analysis.usage import TRACKED_METHODS, detect_usage

MODELS = """\
from typing import Optional

from pydantic import BaseModel, validator


class Customer(BaseModel):
    id: int
    nickname: Optional[str]
    note: Optional[str] = None

    class Config:
        orm_mode = True

    @validator("id")
    def check(cls, v):
        return v
"""


def _sites(source: str, *, extra: tuple[str, ...] = ()) -> tuple[UsageSite, ...]:
    module = ParsedModule(file="m.py", dotted_module="m", source=source, tree=ast.parse(source))
    others = tuple(
        ParsedModule(file=f"x{i}.py", dotted_module=f"x{i}", source=s, tree=ast.parse(s))
        for i, s in enumerate(extra)
    )
    index = build_model_index((module, *others), import_root="pydantic")
    return detect_usage(module, import_root="pydantic", index=index)


def _one(sites, kind: UsageKind) -> UsageSite:
    matching = [s for s in sites if s.kind is kind]
    assert len(matching) == 1, f"expected exactly one {kind}, got {matching}"
    return matching[0]


def test_a_model_definition_is_high_confidence_at_the_class_line() -> None:
    site = _one(_sites(MODELS), UsageKind.MODEL_DEFINITION)
    assert (site.line, site.symbol, site.confidence) == (6, "BaseModel", Confidence.HIGH)
    assert MODELS.splitlines()[site.line - 1].startswith("class Customer(")


def test_a_nested_config_is_high_confidence_and_reports_the_Config_line() -> None:
    site = _one(_sites(MODELS), UsageKind.NESTED_CONFIG)
    assert (site.line, site.symbol, site.confidence) == (11, "Config", Confidence.HIGH)
    assert MODELS.splitlines()[site.line - 1].strip() == "class Config:"


def test_a_decorator_site_points_at_the_at_sign() -> None:
    """Verified when this plan was written: a decorator expression's
    `col_offset` is the character AFTER the `@` (5 for a decorator at indent
    4). The citation must point at the `@`, because that is where the reader's
    eye and their editor's column ruler both go.
    """
    site = _one(_sites(MODELS), UsageKind.DECORATOR)
    assert (site.line, site.symbol, site.confidence) == (14, "validator", Confidence.HIGH)
    line = MODELS.splitlines()[site.line - 1]
    assert line[site.column] == "@", f"column {site.column} is {line[site.column]!r}, not '@'"


def test_an_implicitly_optional_field_is_flagged_and_an_explicit_default_is_not() -> None:
    """The single most valuable finding this analyzer makes for the demo
    target: in v1 `nickname: Optional[str]` defaults to None, in v2 it is
    REQUIRED. `note: Optional[str] = None` is unaffected.

    Verified when this plan was written that these are distinguishable:
    absent default gives `AnnAssign.value is None`, explicit `= None` gives
    `Constant('None')`. A probe that calls `ast.unparse` on the value CANNOT
    tell them apart -- both render as the text `None`.
    """
    sites = _sites(MODELS)
    optional = [s for s in sites if s.kind is UsageKind.OPTIONAL_FIELD]
    assert [(s.line, s.symbol, s.confidence) for s in optional] == [
        (7, "Optional", Confidence.HIGH)
    ]
    assert 8 not in {s.line for s in optional}, "line 8 has an explicit default"


@pytest.mark.parametrize("annotation", ["Optional[str]", "Union[str, None]", "str | None"])
def test_every_spelling_of_implicitly_optional_reports_the_same_corpus_symbol(
    annotation: str,
) -> None:
    """The corpus is keyed on `Optional` regardless of spelling (see the
    plan's Global Constraints). Three symbols for one concept would need
    three corpus documents saying the same thing, and a `$contains` query for
    one would miss the other two."""
    source = (
        "from typing import Optional, Union\n"
        "from pydantic import BaseModel\n"
        "class C(BaseModel):\n"
        f"    x: {annotation}\n"
    )
    site = _one(_sites(source), UsageKind.OPTIONAL_FIELD)
    assert (site.symbol, site.line) == ("Optional", 4)


def test_an_optional_field_outside_a_model_is_not_flagged() -> None:
    """`Optional[str]` in an ordinary class or a function signature has
    nothing to do with the dependency. Flagging it manufactures findings
    proportional to how much typing the repository uses."""
    source = (
        "from typing import Optional\n"
        "class Plain:\n"
        "    x: Optional[str]\n"
        "def f(y: Optional[int]) -> None: ...\n"
    )
    assert [s for s in _sites(source) if s.kind is UsageKind.OPTIONAL_FIELD] == []


def test_a_class_Config_outside_a_model_is_not_flagged() -> None:
    source = "class Plain:\n    class Config:\n        pass\n"
    assert [s for s in _sites(source) if s.kind is UsageKind.NESTED_CONFIG] == []


def test_only_a_directly_nested_Config_counts() -> None:
    """A `class Config:` nested two levels deep, or inside a method, is not
    the pydantic v1 idiom -- it is an unrelated class that happens to share
    the name."""
    source = (
        "from pydantic import BaseModel\n"
        "class C(BaseModel):\n"
        "    class Inner:\n"
        "        class Config:\n"
        "            pass\n"
    )
    assert [s for s in _sites(source) if s.kind is UsageKind.NESTED_CONFIG] == []
```

- [ ] **Step 2: Write the failing tests for the two method-call tiers**

```python
CONSUMER = """\
from app.models import Customer, Invoice


def serialize(invoice: Invoice) -> str:
    return invoice.dict()


def load(raw: dict) -> Customer:
    return Customer.parse_obj(raw)


def summarise(anything):
    return anything.dict()
"""

MODELS_MODULE = (
    "from pydantic import BaseModel\n"
    "class Customer(BaseModel):\n    x: int\n"
    "class Invoice(BaseModel):\n    y: int\n"
)


def _consumer_sites():
    consumer = ParsedModule(file="c.py", dotted_module="c", source=CONSUMER,
                            tree=ast.parse(CONSUMER))
    models = ParsedModule(file="app/models.py", dotted_module="app.models",
                          source=MODELS_MODULE, tree=ast.parse(MODELS_MODULE))
    index = build_model_index((consumer, models), import_root="pydantic")
    return detect_usage(consumer, import_root="pydantic", index=index)


def test_a_call_on_a_parameter_annotated_with_a_model_is_medium() -> None:
    site = next(s for s in _consumer_sites() if s.line == 5)
    assert (site.kind, site.symbol, site.confidence) == (
        UsageKind.METHOD_CALL, "dict", Confidence.MEDIUM,
    )


def test_a_call_on_an_imported_model_class_is_medium() -> None:
    site = next(s for s in _consumer_sites() if s.line == 9)
    assert (site.symbol, site.confidence) == ("parse_obj", Confidence.MEDIUM)


def test_a_call_on_an_unresolvable_receiver_is_low() -> None:
    """The tier that separates a real finding from a coincidence of naming.
    `anything` has no annotation, so nothing connects `.dict()` here to the
    dependency beyond the method's name -- and the method's name is `dict`."""
    site = next(s for s in _consumer_sites() if s.line == 13)
    assert (site.symbol, site.confidence) == ("dict", Confidence.LOW)


def test_the_two_tiers_are_actually_different_in_this_fixture() -> None:
    """Guards against a grader that returns one constant. Both tiers must be
    present in the same module, or neither assertion above is discriminating."""
    tiers = {s.confidence for s in _consumer_sites() if s.kind is UsageKind.METHOD_CALL}
    assert tiers == {Confidence.MEDIUM, Confidence.LOW}


def test_a_call_on_a_module_is_never_a_method_call_site() -> None:
    """`json.dumps(...)` and, more importantly, a tracked NAME on a module:
    `json.dict()` is not a model call however it is spelled."""
    source = "import json\nfrom app.models import Customer\nx = json.dict()\n"
    models = ParsedModule(file="app/models.py", dotted_module="app.models",
                          source=MODELS_MODULE, tree=ast.parse(MODELS_MODULE))
    module = ParsedModule(file="c.py", dotted_module="c", source=source, tree=ast.parse(source))
    index = build_model_index((module, models), import_root="pydantic")
    sites = detect_usage(module, import_root="pydantic", index=index)
    method_sites = [s for s in sites if s.kind is UsageKind.METHOD_CALL]
    assert [s.confidence for s in method_sites] == [Confidence.LOW]


def test_a_bare_function_call_named_dict_is_not_a_site() -> None:
    """`dict(items)` is the builtin. Only an attribute access can be a method
    call, and `util.py`'s `def dict(self)` is a definition, not a call."""
    source = "from app.models import Customer\nx = dict(a=1)\n"
    module = ParsedModule(file="c.py", dotted_module="c", source=source, tree=ast.parse(source))
    models = ParsedModule(file="app/models.py", dotted_module="app.models",
                          source=MODELS_MODULE, tree=ast.parse(MODELS_MODULE))
    index = build_model_index((module, models), import_root="pydantic")
    assert [s for s in detect_usage(module, import_root="pydantic", index=index)
            if s.kind is UsageKind.METHOD_CALL] == []


def test_tracked_methods_is_exactly_the_spec_list() -> None:
    """Spec 7.1 names five: .dict(), .json(), .parse_obj(), .copy(),
    .schema(). Equality, not containment: an extra name manufactures findings
    with no corpus document behind them, and a missing one silently drops a
    whole class of finding."""
    assert TRACKED_METHODS == frozenset({"dict", "json", "parse_obj", "copy", "schema"})
```

- [ ] **Step 3: Write the snippet and citation tests**

```python
def test_every_site_carries_the_source_line_it_cites() -> None:
    """`UsageSite.snippet` is quoted verbatim into the report. It must be the
    line `line` names, with its own indentation intact -- `snippet` is
    deliberately NOT NonBlankStr for exactly that reason (see models/repo.py).
    """
    source = MODELS
    for site in _sites(source):
        assert site.snippet == source.splitlines()[site.line - 1]


def test_every_site_points_inside_the_file_it_names() -> None:
    """A line number past the end of the file is a citation that cannot be
    resolved -- CLAUDE.md rule 1's failure with a plausible-looking number."""
    lines = MODELS.splitlines()
    for site in _sites(MODELS):
        assert 1 <= site.line <= len(lines)
        assert 0 <= site.column <= len(lines[site.line - 1])
        assert site.file == "m.py"


def test_sites_are_returned_in_source_order() -> None:
    sites = _sites(MODELS)
    assert [s.line for s in sites] == sorted(s.line for s in sites)
```

- [ ] **Step 4: Implement `usage.py`**

Structure it as one `ast.NodeVisitor` subclass with an explicit class stack, **not** `ast.walk`. Verified when this plan was written: `ast.walk` yields a nested `class Config` with no indication of its parent, so `NESTED_CONFIG` and `OPTIONAL_FIELD` — both of which are defined by *being inside a model* — are not expressible over a flat walk.

```python
TRACKED_METHODS = frozenset({"dict", "json", "parse_obj", "copy", "schema"})
"""Spec 7.1's medium/low method list. Generic names, all five of them --
which is the entire reason they are not graded high."""

_OPTIONAL_NAMES = frozenset({"Optional", "Union"})
```

Visitor state: `self._class_stack: list[tuple[str, bool]]` — each entry is the class name and whether that class is in the `ModelIndex`. `visit_ClassDef` pushes, emits a `MODEL_DEFINITION` site when the class is indexed, emits `NESTED_CONFIG` when `node.name == "Config"` and the immediately enclosing entry is a model, recurses, pops.

`visit_AnnAssign` emits `OPTIONAL_FIELD` when **all** of:
- `self._class_stack` is non-empty and its **last** entry is a model,
- `node.value is None` (verified: this is what distinguishes an absent default from `= None`),
- `_is_optional_annotation(node.annotation)`.

`_is_optional_annotation` returns True for `Subscript(value=Name("Optional"))`, `Subscript(value=Name("Union"))` whose slice contains a `Constant(None)`, and `BinOp(op=BitOr)` with a `Constant(None)` operand. Resolve `Optional`/`Union` through the `AliasMap` where possible, but accept the bare names too — `typing` is not the dependency, so there is no alias root to match against, and a repository that shadows `Optional` with something else is not a case worth manufacturing complexity for. Say so in the docstring.

`visit_FunctionDef` / `visit_AsyncFunctionDef` emit `DECORATOR` for each decorator whose head name resolves through the `AliasMap` to a root equal to `import_root`. The site's column is:

```python
        column = max(decorator.col_offset - 1, 0)
```

with the reason inline — the decorator expression's `col_offset` points past the `@`, and the citation should point at the `@`. `max(..., 0)` is defensive only; a decorator always has an `@` before it.

`visit_Call` emits `METHOD_CALL` when `node.func` is an `ast.Attribute` whose `attr` is in `TRACKED_METHODS`. Confidence comes from `_receiver_is_model(node.func.value)`:

```python
    def _receiver_is_model(self, receiver: ast.expr) -> bool:
        """MEDIUM when the receiver is known to be a model, LOW otherwise.

        Deliberately narrow -- exactly two forms resolve, and nothing else:

          1. `Customer.parse_obj(...)` -- a Name bound by an import to a class
             in the ModelIndex.
          2. `invoice.dict()` -- a Name bound to a parameter or an AnnAssign
             whose annotation names a class in the ModelIndex.

        No dataflow, no return-type inference, no attribute chains. Every
        widening of this function trades a LOW that is honest for a MEDIUM
        that might not be, and MEDIUM is what the report presents as a likely
        break. The plan's Deviation 1 records the reasoning.
        """
```

Maintain `self._annotated_names: dict[str, str]` per function scope, populated from `arg.annotation` on entry and cleared on exit, mapping local name → annotation's head name. Then a receiver `Name` is a model when either `index.is_model_class(aliases.origin_of(name))` or `self._annotated_names.get(name)` is in `index.names()`.

`visit_Import` / `visit_ImportFrom` emit one `IMPORT` site per bound name whose root is `import_root`, at `Confidence.LOW`.

Finally, `detect_usage` sorts by `(line, column)` before returning.

- [ ] **Step 5: Run every test in the file, then the whole suite**

```bash
cd backend && .venv/bin/python -m pytest tests/unit/test_analysis_usage.py -o addopts="" -q
cd backend && .venv/bin/python -m pytest -o addopts="" -q
```

- [ ] **Step 6: Gates, then commit**

```bash
.venv/bin/python -m ruff check . && .venv/bin/python -m ruff format --check . && .venv/bin/python -m mypy
git add -A && git commit -m "feat(analysis): usage site detection with receiver-resolved confidence tiers"
```

**Done when:** every row of the grading table has a passing test and a negative counterpart, the medium and low tiers are both present in one fixture, and every emitted site's `snippet` equals the source line its `line` names.

---

### Task 8: Churn attribution and repository layout

Two small, independent modules, batched into one dispatch because each is a pure function over data the `Workspace` already provides and neither is worth its own review surface.

**Files:**
- Create: `backend/src/upgradepilot/services/analysis/churn.py`
- Create: `backend/src/upgradepilot/services/analysis/layout.py`
- Test: `backend/tests/unit/test_analysis_churn.py`, `backend/tests/unit/test_analysis_layout.py`

**Interfaces:**
- Consumes: `CommitRecord` (`models/repo.py`), `Workspace.git_log`, `Workspace.iter_files`; `LanguageShare` (Task 1).
- Produces: `ChurnIndex.from_records(records)`, `.for_path(path)`, `.available`; `is_test_path(path)`, `corresponding_test_paths(source, test_paths)`, `language_shares(workspace)`.

- [ ] **Step 1: Write the churn tests**

```python
def test_churn_counts_commits_per_path_and_keeps_the_newest_timestamp() -> None:
    records = (
        CommitRecord(sha="bbbbbbb", timestamp=datetime(2026, 8, 2, tzinfo=UTC),
                     files=("src/app/models.py",)),
        CommitRecord(sha="aaaaaaa", timestamp=datetime(2026, 8, 1, tzinfo=UTC),
                     files=("src/app/models.py", "README.md")),
    )
    index = ChurnIndex.from_records(records)
    entry = index.for_path("src/app/models.py")
    assert entry is not None
    assert entry.commit_count == 2
    assert entry.last_modified == datetime(2026, 8, 2, tzinfo=UTC)
    assert index.for_path("README.md").commit_count == 1


def test_a_path_in_no_commit_returns_None_while_history_is_still_available() -> None:
    """The distinction Task 1 built `commit_count: int | None` for. History
    WAS read; this file simply was not touched. Task 9 turns this into
    `commit_count=0`, a real low-churn signal -- NOT into None."""
    index = ChurnIndex.from_records((
        CommitRecord(sha="aaaaaaa", timestamp=datetime(2026, 8, 1, tzinfo=UTC),
                     files=("other.py",)),
    ))
    assert index.available is True
    assert index.for_path("src/app/models.py") is None


def test_no_records_means_history_was_not_available() -> None:
    """`Workspace.git_log` returns [] both for "no .git directory" and for "a
    real repository with no commits yet". Neither is churn data, and Task 9
    must report `commit_count=None` -- unknown -- rather than zero.

    `available` is derived from the records rather than from a new Workspace
    API, because both of git_log's empty cases mean the same thing here.
    """
    index = ChurnIndex.from_records(())
    assert index.available is False
    assert index.for_path("anything.py") is None


def test_churn_over_the_sample_repo_sees_the_second_commit(tmp_path: Path) -> None:
    """`build_sample_repo` makes two commits on purpose: the second touches
    only `models.py`. This binds that intent to a real assertion."""
    workspace = Workspace(build_sample_repo(tmp_path))
    index = ChurnIndex.from_records(tuple(workspace.git_log(limit=100)))
    assert index.available is True
    assert index.for_path("src/app/models.py").commit_count == 2
    assert index.for_path("src/app/util.py").commit_count == 1
```

- [ ] **Step 2: Write the layout tests**

```python
@pytest.mark.parametrize("path", [
    "tests/test_models.py", "tests/unit/test_x.py", "src/app/test_thing.py",
    "src/app/thing_test.py", "test/test_a.py",
])
def test_test_paths_are_recognised(path: str) -> None:
    assert is_test_path(path) is True


@pytest.mark.parametrize("path", [
    "src/app/models.py", "src/app/latest.py", "src/contest.py",
    "src/protest_utils.py", "attest/main.py",
])
def test_ordinary_paths_are_not_mistaken_for_tests(path: str) -> None:
    """`latest.py`, `contest.py`, `protest_utils.py` and `attest/` all contain
    "test" as a substring. Matching on substring rather than on the path
    convention marks ordinary source as test coverage, which inflates
    `test_coverage_of_affected` -- a factor that LOWERS risk. A false
    positive here makes a risky upgrade read as safe."""
    assert is_test_path(path) is False


def test_a_source_file_finds_its_conventional_test() -> None:
    tests = ("tests/test_models.py", "tests/test_service.py")
    assert corresponding_test_paths("src/app/models.py", tests) == ("tests/test_models.py",)


def test_a_source_file_with_no_test_finds_nothing() -> None:
    assert corresponding_test_paths("src/app/util.py", ("tests/test_models.py",)) == ()


def test_language_shares_total_one_and_are_sorted_by_descending_share(tmp_path: Path) -> None:
    root = build_sample_repo(tmp_path)
    (root / "static").mkdir()
    (root / "static" / "app.ts").write_text("export const x = 1\n", encoding="utf-8")
    (root / "README.md").write_text("# hi\n", encoding="utf-8")

    shares = language_shares(Workspace(root))
    assert math.isclose(math.fsum(s.share for s in shares), 1.0, abs_tol=1e-6)
    assert [s.share for s in shares] == sorted((s.share for s in shares), reverse=True)
    assert shares[0].language == "Python"


def test_language_shares_are_empty_for_a_repository_with_no_recognised_files(
    tmp_path: Path,
) -> None:
    """RepoAnalysis's validator requires the shares to total 1.0 when the
    tuple is non-empty. An empty tuple is the only honest answer here, and it
    must not be a tuple of zeros -- LanguageShare.share is gt=0.0."""
    root = tmp_path / "opaque"
    (root / "data").mkdir(parents=True)
    (root / "data" / "blob.bin").write_bytes(b"\x00\x01")
    assert language_shares(Workspace(root)) == ()
```

- [ ] **Step 3: Implement both modules**

`churn.py`: `ChurnIndex.from_records` groups by path over `CommitRecord.files`, counting records and taking `max(timestamp)`. `available = bool(records)`, documented with git_log's two empty cases. `ChurnEntry.commit_count` is `Field(ge=1)` — an entry exists only because at least one commit touched the path, and a zero-count entry would be a record with no evidence behind it.

`layout.py`:

```python
_TEST_DIRECTORIES = frozenset({"tests", "test", "testing"})


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
```

`corresponding_test_paths(source, test_paths)` takes the source's stem (`models` from `src/app/models.py`) and returns every test path whose filename is `test_<stem>.py` or `<stem>_test.py`, sorted. Returns `()` when none — the honest answer, and Phase 6 reads an empty tuple as "no locatable test".

`language_shares(workspace)` walks `workspace.iter_files("")`, maps each suffix through a documented table, counts files per language, and divides by the count of **recognised** files. Document the denominator on the function — `RepoAnalysis`'s new validator requires the total to be 1.0, and that is only true because unrecognised files are excluded rather than bucketed. Sort by descending share, then by language name so ties are stable. Keep the table small and honest: `.py`→Python, `.pyi`→Python, `.ts`/`.tsx`→TypeScript, `.js`/`.jsx`→JavaScript, `.md`→Markdown, `.toml`/`.yaml`/`.yml`/`.json`→Config, `.sql`→SQL, `.sh`→Shell, `.html`→HTML, `.css`→CSS. Anything else is unrecognised.

- [ ] **Step 4: Gates, then commit**

```bash
cd backend && .venv/bin/python -m pytest -o addopts="" -q && .venv/bin/python -m ruff check . && .venv/bin/python -m ruff format --check . && .venv/bin/python -m mypy
git add -A && git commit -m "feat(analysis): churn attribution and repository layout detection"
```

**Done when:** the three churn states are distinguishable, no substring path is mistaken for a test, and language shares total 1.0 or are empty.

---

### Task 9: The analyzer

One function, and the only thing Phase 4's graph node will ever call.

**Files:**
- Create: `backend/src/upgradepilot/services/analysis/analyzer.py`
- Test: `backend/tests/unit/test_analyzer_assembly.py`

**Interfaces:**
- Consumes: everything from Tasks 2–8, plus `Workspace` and `DependencySpec`.
- Produces: `analyze_repository(workspace, dependency, *, history_limit=100) -> RepoAnalysis`.

- [ ] **Step 1: Write the assembly tests**

```python
def _analysis(tmp_path: Path, **overrides) -> RepoAnalysis:
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
```

- [ ] **Step 2: Implement `analyzer.py`**

```python
"""Repository analysis: Workspace + DependencySpec -> RepoAnalysis.

The only entry point Phase 4's graph node calls. Everything else in this
package is a pure function this one composes, which is why every module
below has its own tests and this one is tested for ASSEMBLY -- that the
pieces are wired to each other and to the right fields -- rather than for
the behaviour they already prove.

No LLM, no network, no graph. CLAUDE.md rule 19 has nothing to constrain
here because there is no model in this path at all.
"""
```

Sequence, in order:

1. `canonical = dependency.canonical_name`, `import_root = dependency.import_root`.
2. `scan = scan_manifests(workspace, canonical)`.
3. `detected = resolve_version(scan.declarations, canonical_name=canonical)` — **not** caught. A dependency the repository does not declare has no honest analysis.
4. `phase_a = select_candidates(workspace, import_root=import_root)`.
5. `index = build_model_index(phase_a.modules, import_root=import_root)`.
6. `candidates = expand_candidates(workspace, phase_a, model_names=index.names())`.
7. `index = build_model_index(candidates.modules, import_root=import_root)` — rebuilt over the expanded set, because phase B can add a module that itself defines a model (a consumer that subclasses one). Rebuilding is cheap: the modules are already parsed.
8. `sites = [site for module in candidates.modules for site in detect_usage(module, import_root=import_root, index=index)]`.
9. `records = tuple(workspace.git_log(limit=history_limit))`; `churn = ChurnIndex.from_records(records)`.
10. `test_paths = tuple(sorted(p.as_posix() for p in workspace.iter_files(".py") if is_test_path(p.as_posix())))`.
11. Group sites by file into `AffectedFile`s:

```python
        entry = churn.for_path(path)
        commit_count = (
            entry.commit_count if entry is not None
            else (0 if churn.available else None)
        )
```

    This three-way expression is the whole point of Task 1's `int | None`. Write it exactly this way and put the reasoning beside it — `0 if churn.available else None` is the line that keeps "we read the history and this file is quiet" apart from "we never read the history".

12. `inventory = SymbolInventory.from_sites(sites)`.
13. Confidence reducers, appended in a fixed order so output is deterministic:
    - `.gitmodules` exists at the workspace root → `"This repository uses git submodules. Submodule contents are not cloned and were not analysed, so code living in them is invisible to this report."`
    - `not candidates.modules` → `f"No file in this repository names the module {import_root!r}. The import name was inferred from the distribution name {dependency.name!r} and the two differ for some distributions, so this may mean the dependency was not found rather than that it is unused."`
    - `scan.unreadable` → one entry naming the manifests that could not be parsed.
14. Construct and return the `RepoAnalysis`, with `commit_sha=workspace.commit_sha` and `languages=language_shares(workspace)`.

Every tuple built from a set must be `sorted(...)` before it is stored. The determinism test in Step 1 is what catches a missed one.

- [ ] **Step 3: Gates, then commit**

```bash
cd backend && .venv/bin/python -m pytest -o addopts="" -q && .venv/bin/python -m ruff check . && .venv/bin/python -m ruff format --check . && .venv/bin/python -m mypy
git add -A && git commit -m "feat(analysis): assemble RepoAnalysis from manifests, usage, churn and layout"
```

**Done when:** the sample repository produces a `RepoAnalysis` whose every citation resolves, both confidence-reducer cases have a positive and a negative test, and two runs are byte-identical.

---

### Task 10: Bind the fixture's expectations to the analyzer, both directions

`PLANNING.md`'s carry-in: *"The fixture's expectation tuples bind **one way**: every listed symbol must exist, but nothing catches someone shortening a tuple, which would silently narrow the documented claim while the suite stayed green. The analyzer's own test must assert its findings **equal** those tuples exactly, which closes both directions."*

This task is where the analyzer becomes the thing that checks the fixture, rather than the fixture merely describing itself.

**Files:**
- Modify: `backend/tests/fixtures/repo_builder.py`
- Create: `backend/tests/analysis/__init__.py`, `backend/tests/analysis/test_analyzer_end_to_end.py`

**Interfaces:**
- Consumes: `analyze_repository` (Task 9) and every expectation constant in `repo_builder.py`.
- Produces: nothing new. This is the phase's exit criterion made executable.

- [ ] **Step 1: Correct the expectation tuples against the implementation**

Two constants are now wrong, and both must change **with the assertion that binds them**, in this task, never by loosening a test:

- `EXPECTED_HIGH_CONFIDENCE_SYMBOLS` is `("Config", "validator")`. The analyzer also grades `BaseModel` (two `MODEL_DEFINITION` sites) and `Optional` (one `OPTIONAL_FIELD` site) as high. Both are real, both are in the fixture, and both are exactly the idioms it was written to contain. The tuple becomes `("BaseModel", "Config", "Optional", "validator")`.
- `EXPECTED_PYTHON_FILES` becomes `7`, per Task 5's `consumer.py`.

Do **not** change `EXPECTED_MEDIUM_CONFIDENCE_SYMBOLS`. If the analyzer does not produce exactly `("copy", "dict", "parse_obj", "schema")` at medium, the analyzer is wrong — that tuple is the fixture's documented claim and Deviation 1 exists to honour it.

Update the comment block at the top of `repo_builder.py` to say that these tuples are now asserted for **equality** by `tests/analysis/test_analyzer_end_to_end.py`, so a future reader knows shortening one is caught.

- [ ] **Step 2: Write the end-to-end test**

```python
"""The phase's exit criterion, executable.

Spec: "given the fixture repository and `pydantic`, the analyzer returns
structured evidence with real file/line usage sites and honest confidence
labels."

Every assertion here is an EQUALITY against a constant in `repo_builder.py`.
The fixture tests in `tests/unit/test_fixture_repo.py` bind those constants
one way -- everything listed must exist. These bind the other way: nothing
unlisted may appear, and nothing listed may go missing. Together they mean
shortening a tuple turns a test red instead of quietly narrowing what this
project claims to detect.
"""


def _analysis(tmp_path: Path) -> RepoAnalysis:
    workspace = Workspace(build_sample_repo(tmp_path))
    spec = DependencySpec(name="pydantic", current_version="1.10.13", target_version="2.9.0")
    return analyze_repository(workspace, spec)


def test_high_confidence_symbols_equal_the_documented_set(tmp_path: Path) -> None:
    analysis = _analysis(tmp_path)
    assert analysis.symbol_inventory.high_confidence_symbols() == EXPECTED_HIGH_CONFIDENCE_SYMBOLS


def test_medium_confidence_symbols_equal_the_documented_set(tmp_path: Path) -> None:
    """Deviation 1's acceptance test. Under spec 7.1's literal module-level
    rule these four would be LOW, because `service.py` imports `app.models`
    rather than `pydantic` and defines no models. They are MEDIUM because the
    receiver of each call resolves to an indexed model."""
    inventory = _analysis(tmp_path).symbol_inventory
    medium = tuple(sorted(
        s.symbol for s in inventory.entries if s.confidence is Confidence.MEDIUM
    ))
    assert medium == EXPECTED_MEDIUM_CONFIDENCE_SYMBOLS


def test_the_low_confidence_site_is_where_the_fixture_says_it_is(tmp_path: Path) -> None:
    path, symbol = EXPECTED_LOW_CONFIDENCE_SITE
    analysis = _analysis(tmp_path)
    affected = next(a for a in analysis.affected_files if a.path == path)
    low = [s for s in affected.usage_sites if s.confidence is Confidence.LOW
           and s.kind is UsageKind.METHOD_CALL]
    assert [s.symbol for s in low] == [symbol]


def test_util_py_is_never_reported(tmp_path: Path) -> None:
    """The fixture's deliberate false-positive trap. `util.py` defines a
    `dict()` method on a plain class and calls it, with no model library
    anywhere in scope. Reporting it would be a fabricated finding -- the
    thing CLAUDE.md rule 1 exists to prevent -- and it is the one file whose
    ABSENCE from the output is the assertion."""
    analysis = _analysis(tmp_path)
    assert "src/app/util.py" not in {a.path for a in analysis.affected_files}
    assert "src/app/util.py" not in {s.path for s in analysis.skipped_files}


def test_the_detected_version_equals_the_documented_pin(tmp_path: Path) -> None:
    detected = _analysis(tmp_path).detected_version
    assert detected is not None
    assert detected.value == EXPECTED_PINNED_VERSION
    assert detected.confidence is VersionConfidence.EXACT
    assert detected.role is DependencyRole.DIRECT
    assert detected.source_manifest.path == "requirements.txt"


def test_the_declared_specifier_survives_into_the_manifest_record(tmp_path: Path) -> None:
    analysis = _analysis(tmp_path)
    pyproject = next(m for m in analysis.manifests if m.path == "pyproject.toml")
    assert pyproject.declared_specifier == EXPECTED_DECLARED_SPECIFIER


def test_the_affected_file_set_equals_the_documented_set(tmp_path: Path) -> None:
    """Equality on the file set, not containment. A future change that makes
    the analyzer report every Python file would satisfy every other test
    here."""
    analysis = _analysis(tmp_path)
    assert tuple(a.path for a in analysis.affected_files) == (
        "src/app/consumer.py",
        "src/app/models.py",
        "src/app/service.py",
    )


def test_the_test_file_is_marked_as_a_test(tmp_path: Path) -> None:
    analysis = _analysis(tmp_path)
    assert analysis.test_paths == ("tests/test_models.py",)
    assert corresponding_test_paths("src/app/models.py", analysis.test_paths) == (
        "tests/test_models.py",
    )


def test_every_citation_in_the_analysis_resolves(tmp_path: Path) -> None:
    """The product's central promise, asserted over the whole output at once:
    every file exists, every line is in range, and every snippet is the line
    it claims to quote."""
    root = build_sample_repo(tmp_path)
    spec = DependencySpec(name="pydantic", current_version="1.10.13", target_version="2.9.0")
    analysis = analyze_repository(Workspace(root), spec)

    cited = 0
    for affected in analysis.affected_files:
        lines = (root / affected.path).read_text(encoding="utf-8").splitlines()
        for site in affected.usage_sites:
            assert site.file == affected.path
            assert 1 <= site.line <= len(lines)
            assert site.snippet == lines[site.line - 1]
            cited += 1
    assert cited >= 8, "too few citations for this assertion to be discriminating"
```

- [ ] **Step 3: Run, and treat a mismatch as an analyzer bug first**

```bash
cd backend && .venv/bin/python -m pytest tests/analysis -o addopts="" -q
```

If an equality fails, the order of investigation is: (1) is the analyzer wrong? (2) is the fixture wrong? (3) — and only then — is the constant wrong? Changing the constant to match the output is how a test stops being able to fail. If you change one, say so explicitly in your report with the reasoning.

- [ ] **Step 4: Gates, then commit**

```bash
cd backend && .venv/bin/python -m pytest -o addopts="" -q && .venv/bin/python -m ruff check . && .venv/bin/python -m ruff format --check . && .venv/bin/python -m mypy
git add -A && git commit -m "test(analysis): bind the fixture's documented expectations to the analyzer's real output"
```

**Done when:** every expectation constant is asserted for equality, `util.py`'s absence is asserted, and every citation in the analysis resolves against the built tree.

---

### Task 11: Bring `tests` under mypy

The last carry-in. `strict = true` currently covers `src/upgradepilot` only, so every test file — including the ones asserting security guards — is unchecked.

**Measured on this branch, 2026-08-25** (not recalled from spec §11, whose figures predate several Phase 1 commits and now read 130/8/13):

```
mypy src/upgradepilot tests                          -> 135 errors in 13 files
  of which inside tests/fixtures/sample_repo         ->   8 errors in  3 files
mypy src/upgradepilot tests --exclude sample_repo    -> 127 errors in 10 files
```

Per file, after excluding the fixture: `test_settings.py` 39, `test_langgraph_contract.py` 27, `test_chroma_contract.py` 22, `test_evidence_models.py` 15, `test_health.py` 13, `test_clone.py` 5, `test_repo_models.py` 2, `test_model_invariants.py` 2, `test_workspace.py` 1, `test_usage_metadata_live.py` 1.

These counts will have grown by the time this task runs — Tasks 1–10 add ten test modules. Re-measure before starting; do not plan against the numbers above.

**Files:**
- Modify: `backend/pyproject.toml`
- Modify: every test module mypy reports

**Interfaces:** none. This task adds no runtime code.

- [ ] **Step 1: Exclude the fixture tree, and prove the exclusion works**

The fixture tree is deliberately Pydantic-v1 and deliberately unparseable. Its 8 errors must never be "fixed" — fixing them destroys what the fixture exists to prove, and `tests/unit/test_fixture_repo.py` would go red.

```toml
[tool.mypy]
python_version = "3.14"
strict = true
files = ["src/upgradepilot", "tests"]
# The fixture tree is deliberately Pydantic-v1 (`Optional[str]` with no
# default, `class Config:`, `@validator`) and contains a deliberately
# unparseable file. Its 8 strict errors are the POINT of the fixture, and
# "fixing" them turns tests/unit/test_fixture_repo.py red.
exclude = ["^tests/fixtures/sample_repo/"]
```

**Known limitation, verified on 2026-08-25 rather than assumed:** mypy's `exclude` is bypassed when a path is named explicitly on the command line, exactly as ruff's `extend-exclude` was — and mypy has **no** `force-exclude` equivalent to close it.

```
mypy --exclude 'tests/fixtures/sample_repo' tests/fixtures/sample_repo/src/app/util.py
  -> Found 3 errors in 1 file (checked 1 source file)     # excluded, still checked

mypy --exclude 'tests/fixtures/sample_repo' tests/fixtures
  -> Success: no issues found in 2 source files           # crawl respects it
```

So a bare `mypy` (the CI invocation and the gate in this plan) is protected; an editor's check-this-file action or a pre-commit hook that passes changed filenames is not. Record this in a comment beside the `exclude` key so the next person does not discover it by having the fixture "corrected".

- [ ] **Step 2: Re-measure, then fix by category rather than by file**

```bash
cd backend && .venv/bin/python -m mypy 2>&1 | grep ' error: ' | sed 's/.*\[//;s/\]//' | sort | uniq -c | sort -rn
```

Fix the largest category first. The categories seen on this branch and the correct fix for each:

- `no-untyped-def` — add `-> None` to test functions and annotate fixture parameters. Mechanical.
- `no-untyped-call` — a typed test calling an unannotated local helper. Annotate the helper.
- `type-arg` — a bare `dict` or `list` in an annotation. Parameterise it.
- `arg-type` / `call-overload` on LangGraph and Chroma calls — a literal dict passed where the stubs want `RunnableConfig`. Build the typed object, or add a narrowly-scoped `# type: ignore[arg-type]` **with a comment naming the stub** and nothing wider.
- `attr-defined` on `HARDENED_GIT_ENV` — mypy's `--no-implicit-reexport` (part of `strict`) refuses an import of a name a module re-exported implicitly. `HARDENED_GIT_ENV` is defined in `services/repo/workspace.py` and imported by `clone.py`; the test imports it from `clone`. Fix the **test**, by importing from `workspace` where it is defined. Do not add an `__all__` to make the indirection legal — the test was reaching through a module that merely passes it along.

A blanket `# type: ignore` on a whole file, or `disable_error_code`, defeats the task. If a specific module genuinely cannot be typed, add a `[[tool.mypy.overrides]]` block for it **with the reason in a comment** and say so in your report.

- [ ] **Step 3: Verify the suite still passes after every annotation**

```bash
cd backend && .venv/bin/python -m pytest -o addopts="" -q
```

Annotations are not supposed to change behaviour, but replacing a literal dict with a typed object can. Run the suite, not just mypy.

- [ ] **Step 4: Prove the fixture is still what it was**

```bash
cd backend && .venv/bin/python -m pytest tests/unit/test_fixture_repo.py tests/analysis -o addopts="" -q
git diff --stat -- backend/tests/fixtures/sample_repo
```

The diff must be empty. If any file under `tests/fixtures/sample_repo/` changed, revert it: something reached past the exclusion.

- [ ] **Step 5: Gates, then commit**

```bash
cd backend && .venv/bin/python -m pytest -o addopts="" -q && .venv/bin/python -m ruff check . && .venv/bin/python -m ruff format --check . && .venv/bin/python -m mypy
git add -A && git commit -m "chore(types): bring tests under strict mypy, excluding the v1 fixture tree"
```

**Done when:** `mypy` with no arguments reports success, the suite is green, and `git diff -- backend/tests/fixtures/sample_repo` is empty.

---

### Task 12: Record what this phase decided

Documentation, held to the end because it records what was actually built rather than what was planned. `CLAUDE.md` rules 6 and 7.

**Files:**
- Modify: `PLANNING.md`
- Modify: `docs/superpowers/specs/2026-08-24-upgradepilot-agent-core-design.md` (§7.1)
- Modify: `docs/adr/ADR-001-system-architecture.md`

- [ ] **Step 1: Amend spec §7.1 for both deviations**

The spec is the binding authority, so it must say what the code does — not be left describing a rule the code deliberately does not follow.

- Replace the medium/low rows of the confidence table with the receiver-resolution rule, and add a sentence naming the reason: the module-level rule graded `service.py` low, which collapsed the medium tier into the low trap the fixture exists to distinguish from it.
- Extend the "Candidate file selection" paragraph with phase B, and state the docstring accident plainly — that `service.py` passed the one-phase filter only because its docstring contained the word "pydantic", which no reviewer would have noticed removing.
- Add the `import_root` limitation: candidate selection uses an import name inferred from the distribution name, which differs for some distributions, and a zero-candidate result is reported as a confidence reducer rather than as "unused".

- [ ] **Step 2: Mark Phase 2 complete in `PLANNING.md`, honestly**

Tick only what a test or a demonstrated run proves (rules 9–11). For each carried-in item, either tick it or move it forward **with a stated reason** — the pattern Phase 1 established. Two are already known to move:

- The `sweep_stale` lifespan wiring and its root-level race stay with Phase 9.
- The `LLMRateLimitedError` / `LLMUnavailableError` taxonomy stays with Phase 4.

Add a "Carried in from Phase 2" block under Phase 3 for anything this phase discovers and defers, with the same one-reason-per-line discipline.

- [ ] **Step 3: Record the two deviations in ADR-001**

They are architectural: the two-pass analyzer and the two-phase candidate filter are both structural choices a later reader will otherwise try to simplify away. Give each a row with what was measured, not just what was decided.

- [ ] **Step 4: Commit**

```bash
git add -A && git commit -m "docs: record Phase 2's analyzer deviations and complete its planning entry"
```

**Done when:** the spec describes the analyzer that exists, `PLANNING.md`'s Phase 2 boxes are ticked only where a test proves them, and both deviations are in ADR-001.

---

## Self-review

Run against this plan after writing it, per the writing-plans skill.

**1. Spec coverage.** Every bullet under `PLANNING.md`'s Phase 2 maps to a task:

| `PLANNING.md` Phase 2 item | Task |
|---|---|
| Manifest detection across all five kinds | 2 |
| Version detection with precedence and confidence; `DependencyNotFound` | 3 |
| `DependencyRole` direct vs transitive-only | 3 |
| Stated-versus-detected discrepancy detection | 9 (model helper existed; Task 9 feeds it) |
| Byte-substring candidate prefilter, then `ast.parse` | 5 |
| Alias map from `Import` / `ImportFrom` | 4 |
| Usage detection with confidence tiers per spec §7.1 | 7 |
| `SkippedFile` records for unparseable files | 5 |
| Churn from a single `git log --name-only` call | 8 |
| Test location detection | 8 |
| Language mix for `RepoAnalysis.languages` | 8 (+ shape change in 1) |
| `SymbolInventory` and `AffectedFile` assembly | 9 |
| Tests: every usage kind, an unparseable file, every manifest type | 2, 5, 7, 10 |
| Carry-in: PEP 503 normalisation | 1 |
| Carry-in: `commit_count=0` conflation | 1 (shape), 8 (source), 9 (assembly) |
| Carry-in: `RiskCategory` names vs spec §8.1 | 1 |
| Carry-in: citation path forms | 1 |
| Carry-in: `languages` in-place mutability | 1 |
| Carry-in: naive datetimes | 1 |
| Carry-in: fixture tuples bind one way | 10 |
| Carry-in: `.gitmodules` as a confidence reducer | 9 |
| Carry-in: bring `tests` under mypy | 11 |

Deferred to their owning phase, unchanged from `PLANNING.md`: `sweep_stale` wiring and its root race (9), the LLM error taxonomy (4), `create_app()` at import time (9), and the hardcoded `PATH`/`GIT_ASKPASS` (a deployment concern, not an analyzer one).

**2. Placeholder scan.** One deliberate error is planted — `math.sum` in Task 1 Step 11 — and is flagged in the note immediately below it, with the reason. Nothing else is a placeholder. Where a task describes an implementation rather than giving a full function body (Tasks 4, 6, 7, 8, 9), the tests above it are complete and executable and the description names every branch; that is the specification the implementer works to.

**3. Type consistency.** Checked across tasks: `ParsedModule` is `(file, dotted_module, source, tree)` in Tasks 5, 6, 7 and 9 alike; `Declaration` is `(manifest, raw_name, version, specifier, confidence, is_lockfile)` in Tasks 2 and 3; `ChurnIndex.for_path` returns `ChurnEntry | None` in Tasks 8 and 9; `LanguageShare` is defined in `models/repo.py` (Task 1) and produced by `services/analysis/layout.py` (Task 8), never the reverse, because `models/` may not import `services/`.

**One inconsistency found and fixed while reviewing:** `ModelIndex.names()` returns a `frozenset[str]` of bare class names while `is_model_class()` takes a dotted path. Both are needed — Task 5's `expand_candidates` byte-searches for bare names, Task 7 resolves an import to a dotted one — so the two-method shape stands, and Task 6's interface block states which is which.

**4. Task independence.** Tasks 2, 3 and 4 depend on nothing but Task 1 and can run in any order. Tasks 5→6→7→9→10 form a chain. Task 1 must be first (every later task uses its types); Tasks 11 and 12 must be last (they measure and describe the finished state).

**Known risk, stated rather than mitigated away:** Task 7 is the largest task and the only one whose implementation is described rather than written out in full. Its twenty specified tests are the compensating control — an implementer who satisfies all twenty, including every negative case, has built the right thing whatever route they took.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-24-phase-2-repository-analysis.md`. Two execution options:

**1. Subagent-Driven (recommended)** — a fresh subagent per task, a spec-and-quality review after each, fast iteration. This is how Phase 0–1 was executed.

**2. Inline Execution** — tasks executed in this session with checkpoints for review.

Which approach?
