# PLANNING.md — UpgradePilot

What we are building and in what order. Architecture rationale lives in `docs/adr/ADR-001-system-architecture.md`; the detailed design for the current sub-project is `docs/superpowers/specs/2026-08-24-upgradepilot-agent-core-design.md`.

**Development rule:** complete a phase, demonstrate its exit criteria, update this file, then move on. At each step ask: *what is the smallest implementation that proves this part of the architecture?* Build that, test it, iterate.

---

## Sub-project structure

| | Sub-project | Status |
|---|---|---|
| **1** | **Agent core** — analysis, RAG, risk, HITL, plan, cost, API, UI | **In progress** |
| 2 | Repository sources & GitHub — authenticated clones, private repos, OAuth | Not started. Phase 13 adds Clerk with GitHub sign-in, so the OAuth half largely arrives early and Clerk holds the user's GitHub token (`getUserOauthAccessToken`). What remains here is the *authenticated clone* — which ADR-001 D5 says lands without touching the analyzer. |
| 3 | Accounts & history — PostgreSQL, users, saved analyses, and the Postgres **run registry** | Not started. The Postgres *checkpointer* is proposed to leave early, in Phase 13 — see ADR-002 D2. The registry stays here, and it is the registry that pins the single-worker constraint. |

Sub-project 1 contains every graded capability, which is why it comes first. Sub-projects 2 and 3 get their own specs when 1 is complete.

Deployment is **Phase 13**, written after Sub-project 1's definition of done because it is not one of the graded capabilities and nothing above it depends on one. Its decisions are recorded in `docs/adr/ADR-002-deployment.md`.

**Order note, 2026-09-09.** Phase 13 is now worked **before Phases 11 and 12**, on the project owner's explicit instruction, with Phase 11's CI item as its only prerequisite. Phase numbers were left alone so nothing has to be renumbered — read the order from here and from Phase 13's own header, not from the numbering. The motivation is GitHub sign-in and users pasting repository links, which Phase 13 now carries via Clerk (ADR-002 D4).

Demo target throughout: **Pydantic v1 → v2** against a real public Python repository, pinned to a fixed commit.

---

# Sub-project 1 — Agent core

## Phase 0 — Environment and architecture validation — COMPLETE

Nothing here is assumed. Every item is a probe whose result gets written into ADR-001's verification table.

- [x] Repository structure, `.gitignore`, `.env.example`
- [x] Python backend with `pyproject.toml`; pinned dependency set
- [x] React + Vite + TypeScript frontend; Tailwind; Lucide
- [x] `pydantic-settings` configuration module
- [x] FastAPI app with `GET /api/health`
- [x] Backend starts; frontend starts; health responds
- [x] Probe: provider reachable, one minimal call — `probes/probe_llm.py` run 2026-08-25 against OpenRouter (`openai/gpt-4.1-mini`), returns `ok`
- [x] Probe: which usage-metadata surface is populated on the resolved `langchain-core` — **both**: `usage_metadata` and `response_metadata['token_usage']`. Recorded in ADR-001, scoped to OpenRouter
- [x] Probe: `with_structured_output(Schema, include_raw=True)` preserves usage metadata — it does; `result['raw'].usage_metadata` populated, `parsing_error` None
- [x] Probe: ChromaDB persists and retrieves after the seeding client is closed; scalar filters, and list-valued `affected_symbols` filtered with exact-element `$contains`
- [x] Probe: minimal LangGraph executes
- [x] Probe: `AsyncSqliteSaver` interrupt then resume, state intact
- [x] Probe: two concurrent threads, no state bleed
- [x] ADR-001 verification table filled from actual results

**Exit:** all probes pass or the ADR is amended to record what actually happened. No application code before this. **Met 2026-08-25** — the three probes above were blocked on a live key throughout Phase 1 and were closed by pointing the stack at OpenRouter (`llm_base_url`), which serves the same OpenAI API. `pytest --live` now runs the full suite with **nothing skipped** (494 passed, 0 skipped; without `--live`, 489 passed / 5 skipped).

Two things this exit does **not** claim. The probes were closed against OpenRouter, not OpenAI direct, and ADR-001 says so in those words — a pass against one endpoint is not a claim about the other. And embedding *reachability* is verified while embedding token accounting is not; that stays with Phase 3.

One near-miss worth keeping, because it would have changed Phase 3's plan: OpenRouter's model catalog lists 417 models and **zero** embedding models, which reads as "embeddings need OpenAI direct". The `/api/v1/embeddings` endpoint proxies them anyway — HTTP 200, 1536 dimensions, usage reported. The catalog is not evidence about the endpoint.

## Phase 1 — Domain models and repository access — COMPLETE

- [x] The domain models Phase 1 consumes, with the honesty invariants (`BreakingChange.source` required, `RiskFactor.evidence` min length 1): the repository and analysis models (`RepoRef`, `Manifest`, `DetectedVersion`, `RepoAnalysis`, `UsageSite`, `AffectedFile`, `SkippedFile`, `SymbolInventory`, `CommitRecord`), the evidence models (`SourceRef`, `EvidenceRef`, `BreakingChange`, `RiskFactor`), the input models (`DependencySpec`, `UserConstraints`) and the error taxonomy (`AppError`, `ErrorCode`). Sixteen of the thirty-one models listed in spec §6.3 are deliberately **not** built yet — the RAG models (`RagQuery`, `RagEvaluation`, `RagContext`), the judgment and HITL models (`RiskAnalysis`, `DecisionOption`, `InterruptPayload`, `HumanDecision`, `DecisionApplication`), the plan models (`MigrationStep`, `MigrationPlan`, `ValidationReport`) and the run/usage models (`LLMCall`, `UsageSummary`, `TraceEvent`, `RunSnapshot`, `FinalReport`). Each arrives with the phase that consumes it (Phases 5–9), where its shape can be driven by a real caller instead of guessed at here
- [x] `EvidenceRef` discriminated union
- [x] `RepoRef` → `Workspace` abstraction
- [x] Shallow-clone resolver (depth 100, single branch)
- [x] Local-path resolver
- [x] Path and URL guards: scheme allowlist, `ALLOWED_LOCAL_ROOTS` (confining local-path refs **and** `file://` clone URLs, since git resolves a `file://` URL against local disk and ignores its host), symlink escape, size caps. One asymmetry to know about: a `file://` URL must percent-encode a space as `%20`, while a `LocalRepoRef` path accepts a raw space — so prefer the local-path form for a path like `/Users/me/My Documents/repo`
- [x] Workspace cleanup, and `WorkspaceManager.sweep_stale` implemented and tested (`test_workspace_manager.py`). Its **startup invocation is not wired**: `sweep_stale` currently has no caller and there is no FastAPI lifespan to call it from. Wiring it needs a max-age setting, a lifespan handler and tests of its own, so it lands with the API lifespan in Phase 9 rather than being claimed here
- [x] Hand-authored fixture repository with real git history (`backend/tests/fixtures/sample_repo/`, built by `build_sample_repo()`) — supersedes the "vendored, pinned to a commit" wording above; see spec §12 assumption 5 for the recorded deviation. A real public repository, pinned by commit, is still planned but deferred to Phase 12 for the demo and E2E path — carried there as an explicit item, not merely as a promise made here.
- [x] Tests: each guard, both resolvers, cleanup, and the fixture repository's own shape (`test_fixture_repo.py`)

**Exit:** a public URL and a local path both produce a `Workspace` the analyzer can read, and every guard has a failing-case test. Met — see `backend/tests/unit/{test_repo_guards,test_clone,test_workspace,test_workspace_manager,test_fixture_repo}.py` for the local path and the guards, and `backend/tests/repo/test_clone_live.py` for the public URL.

The URL half needs that second file to be cited honestly. Every test in `test_clone.py` clones over `file://`, which is **not** in the shipped `allowed_url_schemes` (`https`, `git`), so the hermetic suite proved the clone machinery without ever proving the transport a real user gets. `test_clone_live.py` closes that with one real `https` clone of a small public repository, marked `@pytest.mark.live` to match the existing opt-in convention: it skips under plain `pytest` and runs under `pytest --live` (verified passing, 3 passed).

**Note on scope:** five items originally listed under Phase 1's wording elsewhere in this doc's history ("detect dependency manifests", "identify the requested dependency", "read installed version", "identify direct imports", "calculate change indicators") are Phase 2 work in this plan's actual task structure — they consume the `Workspace` and fixture built here but are not domain-model or repository-access work themselves. Phase 1, as implemented, is scoped to domain models and repository access only; Phase 2 covers manifest/version/usage detection.

## Phase 2 — Repository analysis

**Carried in from Phase 1.** These were found during Phase 1's review and
deliberately deferred, because the analyzer that populates these models does not
exist yet and the right constraint only becomes visible once it does. Deferred is
not forgotten — each has a stated reason:

- [x] `SymbolInventory`/`AffectedFile` constraints that need a real consumer:
      PEP 503 normalisation of `DependencySpec.name` (it is an exact-match corpus
      key, so normalisation changes retrieval), and `commit_count=0` currently
      conflating "unknown" with "no churn". Both done: `canonicalize_name`
      (`models/inputs.py`) backs matching, proven by
      `tests/unit/test_analysis_manifests.py::test_matching_is_on_the_canonical_name_not_the_written_one`;
      `commit_count` is now `int | None` with `None` meaning "history
      unavailable" and `0` meaning "available, untouched"
      (`services/analysis/analyzer.py:107`), proven by
      `tests/unit/test_analyzer_assembly.py::test_commit_count_is_None_when_the_repository_has_no_history`
      and `::test_commit_count_is_zero_for_an_untracked_file_while_history_is_available`.
- [x] `RiskCategory` member names have drifted from spec §8.1, and three of them
      overstate their scope. Rename with the analyzer, so the names describe what
      is actually computed. Done: `models/enums.py:34-41` now matches the spec
      §8.1 factor table verbatim, proven by
      `tests/unit/test_evidence_models.py::test_risk_categories_match_the_spec_factor_table_exactly`,
      which asserts equality (both directions) between the enum's values and
      the spec's factor list.
- [x] Citation paths accept absolute and `..` forms; `EvidenceRef` should require
      a repo-relative path once the analyzer is the only producer. Done:
      `RepoRelativePath` (`models/evidence.py`) rejects absolute, `..`, and
      backslash forms via `_require_repo_relative`, proven by
      `tests/unit/test_evidence_models.py::test_repo_evidence_rejects_non_repo_relative_paths`
      (parametrized negative cases) and
      `::test_repo_evidence_accepts_ordinary_repo_relative_paths`.
- [x] `RepoAnalysis.languages` is bounded but still mutable in place, and is
      unspecified in the spec. Fixing the mutability is a shape change. Done:
      `languages: tuple[LanguageShare, ...]` (`models/repo.py:246`) — a tuple,
      not a list, so in-place mutation is structurally blocked. Shape checked
      by `tests/unit/test_repo_models.py`; produced correctly by
      `tests/unit/test_analysis_layout.py::test_language_shares_total_one_and_are_sorted_by_descending_share`.
- [x] Naive vs aware datetimes across the models. Done: `CommitRecord.timestamp`
      and `AffectedFile.last_modified` are `pydantic.AwareDatetime`
      (`models/repo.py:164,222`), which rejects a naive `datetime` at
      construction. Demonstrated this session (`CommitRecord(sha=..., timestamp=datetime(2026,1,1))`
      raises `ValidationError`); no dedicated regression test exists in the
      suite for the rejection itself, only for the aware values the analyzer
      already produces (`tests/unit/test_analyzer_assembly.py`, asserting
      `record.timestamp.tzinfo is not None`).
- [x] The fixture's expectation tuples bind **one way**: every listed symbol must
      exist, but nothing catches someone *shortening* a tuple, which would
      silently narrow the documented claim while the suite stayed green. The
      analyzer's own test must assert its findings **equal** those tuples exactly,
      which closes both directions. Done:
      `tests/analysis/test_analyzer_end_to_end.py` (commit `24a50af`) asserts
      `==` against `EXPECTED_HIGH_CONFIDENCE_SYMBOLS`,
      `EXPECTED_MEDIUM_CONFIDENCE_SYMBOLS`, and the affected-file set, not
      containment.
- [x] The analyzer must detect `.gitmodules` and surface "submodule content not
      analysed" as an explicit confidence reducer. `git clone` does not fetch
      submodules, so a repository whose real code lives in them would otherwise
      analyse as nearly empty and report low risk having never seen the code.
      Done: `services/analysis/analyzer.py:124` checks for `.gitmodules` and
      appends a confidence reducer without adding a `SkippedFile`, proven by
      `tests/unit/test_analyzer_assembly.py::test_gitmodules_becomes_a_confidence_reducer_not_a_skipped_file`
      and `::test_no_gitmodules_means_no_submodule_reducer`.
- [x] Bring `tests` under mypy. Done: `files` in `backend/pyproject.toml` is now
      `["src/upgradepilot", "tests"]`, with `exclude =
      ["^tests/fixtures/sample_repo/"]` carving out the fixture tree (13 strict
      errors there are the point of the fixture, not a defect). Measured cost
      for the rest of `tests`: 158 errors across the branch at the start,
      `plugins = ["pydantic.mypy"]` alone resolving 46 of them. Bare `mypy`
      (no path argument) now reports `Success: no issues found in 66 source
      files`. See spec §11. Proven by commit `809ee7c` (`chore(types): bring
      tests under strict mypy, excluding the v1 fixture tree`) and a run of
      `.venv/bin/python -m mypy` this session.

**Deferred to the phase that owns the surface:**

- [ ] Wire `sweep_stale` into a FastAPI lifespan (Phase 9). It is implemented and
      tested but has no caller; the startup-only contract is in its docstring.
- [ ] `sweep_stale` does not guard the workspace root itself vanishing between
      `exists()` and `iterdir()`. Individual entries are guarded.
- [x] An `LLMRateLimitedError` / `LLMUnavailableError` taxonomy (Phase 4). Done — `models/errors.py`. Kept separate because the remedy differs: waiting fixes a rate limit and does not fix a misconfigured endpoint, so a run reporting both as one condition cannot tell an operator which they have.
- [ ] `api/app.py` calls `create_app()` at import time.
- [ ] `HARDENED_GIT_ENV` (`backend/src/upgradepilot/services/repo/workspace.py:40`,
      imported by `clone.py:28`) hardcodes `PATH` to `/usr/bin:/bin:/usr/local/bin`
      and `GIT_ASKPASS` to `/usr/bin/true`. https transport works under it here,
      but git lives at `/opt/homebrew/bin` on Apple Silicon, so the product's
      primary input path would fail on such a host. Resolve these rather than
      assume them, or fail with a clear diagnostic.

**Open decision for the maintainer:** `git://` is in the default URL scheme
allowlist, per spec line 180, and the implementation follows the spec faithfully.
The git protocol has no encryption and no server authentication, so a network
attacker can substitute repository content and the clone succeeds. That matters
more here than in a generic tool: the analysis would be perfectly faithful to
code that is not the user's, and the evidence chain would stay internally
consistent while being globally false. Recommendation is to drop `git` from the
default and keep it reachable via `UP_ALLOWED_URL_SCHEMES`. Not changed
unilaterally, because the code matches the spec and this is a spec decision.


- [x] Manifest detection across `pyproject.toml`, `requirements*.txt`, `poetry.lock`, `uv.lock`, `Pipfile.lock` — proven by `tests/unit/test_analysis_manifests.py::test_each_manifest_kind_yields_the_expected_declaration` (parametrized over one fixture per kind/dialect under `tests/fixtures/manifests/`) and `::test_scan_manifests_finds_both_manifests_in_the_sample_repo`
- [x] Version detection with precedence and confidence label; `DependencyNotFound` when absent — proven by `tests/unit/test_analysis_versions.py::test_no_declaration_raises_rather_than_guessing`, `::test_a_lockfile_pin_beats_a_pyproject_range`, `::test_an_exact_requirements_pin_beats_a_pyproject_range`, `::test_a_range_only_declaration_reports_the_specifier_as_the_value`
- [x] `DependencyRole` direct vs transitive-only — proven by `tests/unit/test_analysis_versions.py::test_lockfile_only_means_the_user_does_not_control_the_pin` and `::test_a_human_authored_manifest_means_direct`
- [x] Stated-versus-detected discrepancy detection — proven by `RepoAnalysis.version_discrepancy` (`models/repo.py:326`) and `tests/unit/test_analyzer_assembly.py::test_version_discrepancy_surfaces_rather_than_being_overridden`
- [x] Byte-substring candidate prefilter, then `ast.parse` — proven by `tests/unit/test_analysis_candidates.py::test_phase_a_selects_files_naming_the_import_root` and `::test_the_unparseable_file_becomes_a_skipped_record_not_an_exception`. This item covers only phase A of what the code actually does; phase B (see the carry-in below) was not part of this line's original scope and is recorded separately
- [x] Alias map from `Import` / `ImportFrom` — proven by `tests/unit/test_analysis_imports.py::test_every_import_spelling_resolves`, parametrized across every import spelling (plain, aliased, dotted, relative, star)
- [x] Usage detection with confidence tiers per spec §7.1 — proven by `tests/unit/test_analysis_usage.py` (27 test functions covering every `UsageKind`) and, for the tier boundary specifically, `::test_a_call_on_a_parameter_annotated_with_a_model_is_medium`, `::test_a_call_on_an_unresolvable_receiver_is_low`, `::test_the_two_tiers_are_actually_different_in_this_fixture`
- [x] `SkippedFile` records for unparseable files — proven by `tests/unit/test_analysis_candidates.py::test_the_unparseable_file_becomes_a_skipped_record_not_an_exception` and `::test_a_file_that_is_not_utf8_is_skipped_with_a_decode_reason`, plus assembly-level `tests/unit/test_analyzer_assembly.py::test_the_unparseable_file_is_reported_not_swallowed`
- [x] Churn from a single `git log --name-only` call — proven by `Workspace.git_log` (`services/repo/workspace.py:265-310`, one subprocess call) and `tests/unit/test_analyzer_assembly.py::test_commit_records_are_populated_from_the_same_history_git_log_read`
- [x] Test location detection — proven by `tests/unit/test_analysis_layout.py::test_test_paths_are_recognised`, `::test_a_source_file_finds_its_conventional_test`, `::test_a_near_miss_filename_is_not_mistaken_for_the_conventional_test`, and end-to-end by `tests/analysis/test_analyzer_end_to_end.py::test_the_test_file_is_marked_as_a_test`
- [x] Language mix for `RepoAnalysis.languages` by counting file extensions over the workspace — the field exists and defaults to `{}`, so without this it stays empty and any UI reading it shows nothing. A sixth item in Phase 1's scope note ("detect repository languages") deferred here without a corresponding line; this is that line. Proven by `tests/unit/test_analysis_layout.py::test_language_shares_total_one_and_are_sorted_by_descending_share` and `::test_language_shares_are_empty_for_a_repository_with_no_recognised_files`
- [x] `SymbolInventory` and `AffectedFile` assembly — proven by `tests/unit/test_analyzer_assembly.py::test_counts_are_internally_consistent` and `::test_every_affected_file_path_appears_in_the_repository`, plus the equality tests in `tests/analysis/test_analyzer_end_to_end.py`
- [x] Tests: fixture files for every usage kind, an unparseable file, every manifest type — proven by `tests/unit/test_fixture_repo.py` (10 tests asserting the fixture's own shape) and `tests/fixtures/repo_builder.py`'s documented expectation constants, consumed by `tests/analysis/test_analyzer_end_to_end.py`

**Exit:** given the fixture repository and `pydantic`, the analyzer returns structured evidence with real file/line usage sites and honest confidence labels. **Met** — proven by `tests/analysis/test_analyzer_end_to_end.py` (commit `24a50af`), which runs `analyze_repository` over the fixture and `pydantic`, asserting equality (not containment) against the fixture's documented expectation tuples for high- and medium-confidence symbols, the low-confidence site, the affected-file set, the detected version, and citation resolution (`test_every_citation_in_the_analysis_resolves`). Confirmed passing this session: `.venv/bin/python -m pytest -o addopts="" -q` → 693 passed, 5 skipped.

**Carried in from Phase 2.** Found during this phase's own work and deliberately deferred, same discipline as the Phase 1 carry-ins above. The final whole-branch review (66 mutations, 44 caught) added the items marked *[review]*; its nine blocking findings were fixed on the branch rather than carried, and the suite went from 695 to 738 passing:

- [ ] The analyzer's two-pass design (`ModelIndex` built to a fixed point, then usage graded by receiver resolution) and its two-phase candidate selection (byte-scan for the import root, then byte-scan the remainder for discovered model names) both depart from spec §7.1 and §7.1's "Candidate file selection" paragraph as originally written. The spec has been amended to describe the code (see this phase's Step 1); the departures themselves, with what was measured, are recorded in ADR-001. The candidate/index loop now runs to a fixed point rather than once, and that too is recorded in ADR-001's D12.
- [ ] *[review]* `usage.py`'s dotted-decorator branch (`@dep.validator(...)`, as opposed to `@validator(...)`, at `usage.py:138-144`) has no test that fails when it is deleted (ruling 56). Confirmed CORRECT by execution during the review — `@pydantic.validator("x")` cites the right line and column — so this is a missing test, not a defect. Reason for deferring: a test over working code, where nine findings that produced wrong claims took priority.
- [ ] *[review]* Async-function scope handling has the same shape of gap at BOTH sites (ruling 60): `usage.py:340` (`visit_AsyncFunctionDef` delegating to `_visit_function`) and `models_index.py:103` (`_TopLevelClassVisitor` declining to descend into an `async def`). Both confirmed correct by execution. Same reason for deferring as the line above.
- [ ] *[review]* Unexecuted lines that would only ever cause an UNDER-report if removed, left as-is rather than force-covered: `usage.py:114` (`_annotation_head_name`'s dotted-annotation branch), `usage.py:352-355` (a decorator whose head does not resolve to the dependency), `usage.py:472` (a receiver that is not a bare name), `models_index.py:115,134,203` (base expressions this pass cannot resolve statically).
- [x] *[review]* **EARLY Phase 3 work**, ranked ahead of the line above because both feed an honesty channel rather than a grading one: phase B's `SkippedFile` append (which feeds `skipped_ratio` and therefore the `analysis_coverage` risk factor and its confidence ceiling) and the unreadable-manifest path behind that reducer. A silent failure in either makes the report claim more coverage than it has. Done, first work of Phase 3. Both were covered by *mutation*, not by execution alone: each test was watched failing with the production line removed and passing with it restored, since the code already existed and a test written over it would otherwise prove nothing. Phase B's append (`candidates.py:230`) is bound by
      `tests/unit/test_analysis_candidates.py::test_a_file_reachable_only_through_phase_b_is_skipped_not_raised`
      — over a fixture file naming `Customer` and never naming `pydantic`, so a phase-A hit cannot make it pass vacuously — and its seed-from-phase-A companion by `::test_phase_b_keeps_phase_a_skips_alongside_its_own`. The manifest read-failure branch (`manifests.py:404-407`, reached only when the bytes are not UTF-8 and `_parse` is therefore never called, so the corrupt-TOML test cannot speak for it) is bound by
      `tests/unit/test_analysis_manifests.py::test_scan_manifests_records_a_manifest_that_does_not_decode_as_unreadable`
      and, at the far end of the same channel, `::test_an_unreadable_manifest_becomes_a_confidence_reducer`.
- [x] **The `dotted_module` collision decision, which Phase 2 deferred *to* Phase 3, is now made: index both and keep the reducer.** The open question was whether to index both files, index neither, or cap the confidence of an ambiguous attribution. Phase 3 answers it, because Phase 3 is what consumes the index: retrieval joins on **symbol names**, never on the file a model was defined in (`store.search`'s `$contains` against `affected_symbols`, and `annotate_coverage` reading `RetrievedChunk.affected_symbols`). So a collision cannot corrupt evidence retrieval at all — it can only misattribute *which file defines a model*, which affects a confidence grade and never a cited file or line, since each usage site cites its own location. Indexing neither would delete real findings to avoid a grading imprecision; capping confidence would penalise a repository for a directory-layout coincidence. Naming the ambiguity and keeping the findings is the option that loses nothing and hides nothing.
- [ ] *[review]* Mutation survivors that are genuine gaps but produce no wrong claim today: X5 (a model class nested inside another class), U4 (`Optional` inside a nested `Config`), M2 (`pyproject.toml` case-folding), M13 (`old.pyproject.toml` classifying as a manifest), A12 (`AffectedFile.is_test` entirely unbound).
- [ ] *[review, found while resolving `AliasEntry.is_module`]* `_receiver_is_model`'s "a module receiver is excluded by the shape of its origin" argument has one residue, reproduced by execution: `import a.b` binds `a` with origin `a.b`, so if package `a`'s `__init__.py` defines `class b(BaseModel)` then `a.dict()` grades MEDIUM on a receiver that is a module. Contrived — it needs a class named exactly like a sibling submodule — and the cost is a confidence grade, never a wrong citation. The fix consumes `AliasEntry.is_module` through a new `AliasMap` accessor (`origin_of` returns a string and loses which entry won), and adopting it reopens ruling 1279's deliberate decision not to special-case a module receiver. Not taken unilaterally for that reason.
- [ ] `broken.py`'s fixture comment names the dependency ("`# pydantic: ...`"), which is structurally the same accident phase B exists to close for `service.py` — acceptable here only because `broken.py` fails loudly (a test goes red immediately) if that comment is removed, unlike `service.py`'s docstring, which failed silently. Worth a real first-party consumer fixture that reaches `broken.py` only through phase B, the way `consumer.py` does for `service.py`, if the distinction ever needs to be demonstrated rather than merely argued.
- [ ] *[final fix round 2, finding 3]* `models_index.build_model_index`'s `dotted_targets` set is keyed on `dotted_module`, which is not injective (`src/app/models.py` and `app/models.py` both give `app.models`), so a transitive base or a first-party import naming that dotted path genuinely cannot say which of the colliding files it means. `models_index.colliding_dotted_modules` now detects the collision and `analyze_repository` reports it as a confidence reducer, but the ambiguity itself is deliberately NOT resolved by this fix — only recorded. Choosing between indexing both colliding files, indexing neither, or capping the confidence of an attribution that lands in a collision is a design decision with real trade-offs in both directions (indexing both over-reports; indexing neither under-reports; capping confidence changes the grading scheme), and belongs to Phase 3, with the reducer already in place to make the problem visible in the meantime.
- [ ] *[final fix round 2, finding 4]* `layout.py:122-127` — F9's new `except ValidationError` arm (guarding `LanguageShare` construction inside `language_shares`) is unexecuted on any raw-quotient input: each `count / total` quotient is a correctly-rounded double and `math.fsum`'s accumulated error over any realistic language count is ~1e-16, six orders of magnitude inside `_require_shares_total_one`'s `abs_tol=1e-6`, so `gt=0.0` can never fire on a value that reaches this branch. Kept anyway for CLAUDE.md rule 20's defensive breadth, the same precedent as `candidates._parse`'s `OSError`/`ValueError` arms — if `LanguageShare`'s validation ever tightened, this arm is what turns that into a recorded `UpgradePilotError` rather than an unhandled `ValidationError` several steps from the arithmetic that caused it. Missing from this round's own carry-in block when F9 landed, even though the block was rebuilt from a fresh coverage run at the time; added here because the block is the project's record of what is known-unbound, and an omission makes it stop being that.

## Phase 3 — Knowledge base — COMPLETE

- [x] Document metadata schema and frontmatter format — `models/knowledge.py`
      (`CorpusDocument`, `DocumentChunk`, `RetrievedChunk`) and
      `services/knowledge/corpus.py` (`parse_document`, `load_corpus`), proven by
      `tests/knowledge/test_corpus_documents.py` (17 tests). The schema follows
      spec §7.2's frontmatter verbatim; what the tests actually pin is the set of
      *refusals*, each one a case where a lenient parser yields a document that
      cites itself confidently and wrongly: no frontmatter, an unterminated fence,
      a missing field, an empty body, an unknown key (a typo'd `affected_symbol`
      is otherwise a silent no-op), frontmatter setting a parser-owned field, an
      unquoted `to_version: 2.0` that YAML reads as a float, a `to_version_major`
      that disagrees with `to_version`, a duplicate `source_id` across the corpus,
      and an empty corpus directory. `affected_symbols` is required for the source
      types that document a specific API change and optional for `adr` /
      `upgrade_report`, because a symbol invented for a prose document is a false
      `$contains` join while a migration guide naming none can never be reached by
      that join at all.
- [x] Corpus authored one breaking change per document — real Pydantic v1→v2 primary sources, plus a small number of authored ADRs and upgrade reports representing internal engineering guidance. 19 documents under `backend/corpus/`: 16 primary sources in `pydantic/` (citing the upstream migration guide) and 3 authored internal documents in `internal/`. The two are kept in separate directories and `_README.md` says why: the product prints their citations side by side, so a reader must be able to tell which claims trace to Pydantic's own documentation and which are this project's opinion. The upgrade report states in its own first line that it is a worked example and not a report of a production incident. Proven by `tests/knowledge/test_shipped_corpus.py`, whose load-time call is the whole schema check, and whose `::test_every_symbol_the_fixture_repository_uses_is_documented` binds the corpus to the analyzer's real output — adding a usage kind to the fixture without documenting it now turns a test red instead of quietly degrading the demo.
- [ ] Ingestion: parse frontmatter, chunk, embed, persist — scalar metadata for the coarse fields, `affected_symbols` as a real list. *Chunking done* (`services/knowledge/chunking.py`, `tests/knowledge/test_chunking.py`, 8 tests): boundaries fall between paragraphs and never inside a fenced code block, `max_chars` is a target rather than a guarantee so an over-budget paragraph is emitted whole, and `chunk_id` is `{source_id}#chunk-{ordinal}` so re-ingesting unchanged content re-mints the same citation keys. The no-text-is-lost and fence invariants were each watched failing under mutation; the fence test was **vacuous on first writing** — its fixture had no blank lines inside the block, so it passed with the fence tracking deleted — and the fixture was corrected until the mutation killed it. Embedding and persistence now done too: `services/knowledge/embeddings.py` (`OpenAIEmbedding`, recording an `EmbeddingCall` per request from the provider's own `usage`) and `services/knowledge/ingest.py` (`python -m upgradepilot.services.knowledge.ingest`), proven by `tests/knowledge/test_ingest.py` and, on the real provider, `tests/knowledge/test_embeddings_live.py`.
      **Ingestion is a rebuild, not an update**, for a correctness reason: `upsert` writes by id and never deletes, so a document removed from the corpus keeps every chunk it ever wrote, and — subtler — a document edited from three chunks down to one rewrites `#chunk-0` and abandons `#chunk-1` and `#chunk-2`, which then carry the *old* text under a live `source_id`. A citation to an orphan resolves to a real document and quotes a passage deleted from it, and nothing in the output looks wrong. Measured cost of rebuilding: the whole corpus embeds in one request for 6191 tokens, so the affordability this relies on is a number, not an assumption. The corpus is parsed in full *before* the rebuild drops anything, so a typo in one document cannot wipe a collection that was serving correctly (`::test_a_failed_re_ingest_leaves_the_working_collection_intact`).
- [x] `affected_symbols` stored as a **list-valued** metadata field, filtered in the database with `$contains` (exact-element) and never with `$in` — `services/knowledge/store.py`, proven by `tests/knowledge/test_store.py::test_a_symbol_filter_returns_only_documents_naming_that_symbol`, `::test_several_symbols_are_joined_as_a_union` and both negative directions. One correction to the plan's assumption, found by probe: chromadb 1.5.9 **rejects an empty list** metadata value outright, so a document naming no symbols omits the key rather than storing `[]` — which is also the right semantics, since such a document is not reachable by the symbol join and was never meant to be.
- [x] Retrieval with scalar metadata filters for coarse narrowing, plus the `$contains` symbol join — `KnowledgeStore.search`, proven by the scalar-filter and symbol-join groups of `tests/knowledge/test_store.py`. `_combine` never wraps fewer than two clauses, because Chroma raises on a single-clause `$and`/`$or` (ADR-001) and the commonest query of all — dependency alone — has exactly one; `::test_one_filter_and_several_filters_both_work` pins both arities.
- [x] Symbol coverage annotation over retrieved candidates, and the deterministic sufficiency gate — `services/knowledge/coverage.py`, proven by `tests/knowledge/test_coverage.py` (10 tests). Only high-confidence symbols block sufficiency, per spec §7.3: a medium- or low-confidence symbol is one the analyzer itself is unsure of, so demanding corpus evidence for it would make the loop iterate against its own uncertainty rather than a real gap. Coverage is read from the document's `affected_symbols`, never from `matched_symbols`, so a caller that forgot to pass its symbols cannot flip the verdict on a plumbing mistake — `::test_coverage_is_read_from_what_the_document_covers_not_from_the_query`. Matching is exact in Python as well as in Chroma (`::test_a_prefix_colliding_symbol_does_not_count_as_coverage`). All four mutations (ignore confidence, read `matched_symbols`, prefix-match, always-sufficient) were watched failing.
- [x] Source metadata returned with every result — `RetrievedChunk` carries its document's identifying metadata and `to_source_ref()` builds the citation without a second round-trip, proven by `tests/knowledge/test_store.py::test_every_result_carries_the_metadata_a_citation_needs` and `::test_ingested_metadata_survives_a_reopen`.
- [x] Deterministic fake embedding function for tests — `tests/knowledge/fake_embedding.py`, now a **hashing vectorizer rather than a whole-text hash**. The previous SHA-256-of-the-document form is stable and offline but assigns unrelated vectors to related texts, so the only ranking it can produce is "an exact repeat comes first" — enough to prove Chroma stores what it was given, and not enough for a golden set, which would have been scoring noise while reporting a floor. What the floors then measure is stated in that module: the pipeline (filters, symbol join, dedup, ordering, the distance-to-relevance mapping) under a lexical embedding, **not** `text-embedding-3-small`'s semantic quality.
- [x] Golden evaluation set (~15 cases) with recall@5 and MRR floors asserted in CI — 19 cases, one per corpus document, in `tests/knowledge/golden_set.py`; floors asserted by `tests/knowledge/test_golden_set.py`. **Measured 0.789 recall@5 and 0.695 MRR** against the shipped corpus; floors set at 0.70 and 0.60. `::test_every_corpus_document_has_a_golden_case` makes CLAUDE.md rule 25 mechanical rather than remembered, and `::test_the_metrics_can_actually_fail` guards the guards — a metric returning 1.0 on unexpected input would make both floor tests vacuous.
      Two things a reader must not over-read. The floors score the *pipeline* under a lexical offline embedding, not `text-embedding-3-small`'s semantic quality; every real pipeline break (inverted ranking, a filter matching nothing, a broken dedup) drives them to approximately zero rather than shaving points. And the gap from 0.789 to 1.0 was diagnosed, not accepted: the four misses rank 6th, 6th, 16th and 13th of 30 chunks, losing to documents sharing common vocabulary because a count vector has no notion of a rare term. Binary (0.737/0.559) and sublinear-tf (0.895/0.686) weightings were both measured; the embedder was deliberately **not** switched to the better-scoring one, because selecting an embedder by its score on the evaluation set makes the floor a measure of how hard we tuned.
- [x] Tests: retrieval, scalar filtering, `$contains` symbol filtering including the negative direction (a prefix-colliding symbol must not match), source metadata fidelity, Chroma-unavailable handling — `tests/knowledge/` (`test_store.py`, `test_coverage.py`, `test_ingest.py`, `test_corpus_documents.py`, `test_chunking.py`, `test_shipped_corpus.py`, `test_golden_set.py`, plus the pre-existing `test_chroma_contract.py`). Both `KB_UNAVAILABLE` shapes are covered: a store that cannot be opened, and a collection that vanished after opening.
      One **production defect** was found by running the suite with `--live` and is worth recording, because it looked like a test-isolation problem and was not: chroma **writes into** the `configuration` mapping it is handed, inserting the embedding function under an `embedding_function` key. `CORPUS_CONFIGURATION` was a module-level constant, so the first store to open stamped its embedder into shared state and every store opened afterwards was validated against that stale embedder. Outside the suite this means a process opening a second collection gets the first one's embedder imposed on it, or fails outright. Fixed by handing chroma a `deepcopy` per call (`store._configuration`), and bound by `::test_two_stores_with_different_embedders_do_not_interfere` (the symptom) and `::test_opening_a_store_does_not_mutate_the_shared_configuration` (the cause, which fails immediately rather than only when two different embedders meet).

**Exit:** a migration question returns relevant evidence with source metadata that resolves, and the golden set meets its floors. **Met 2026-08-25** — demonstrated end to end rather than argued: `analyze_repository` over the fixture repository produces the symbol inventory `('BaseModel', 'Config', 'Optional', 'copy', 'dict', 'parse_obj', 'schema', 'validator')`; that inventory drives a filtered, symbol-joined search; all 10 returned chunks build a `SourceRef` whose `chunk_id`, title and URL resolve; and the deterministic gate reports every symbol covered, no uncovered high-confidence symbol, `sufficient=True`. The golden set meets its floors in CI (0.789 against 0.70 recall@5, 0.695 against 0.60 MRR). Suite: **828 passed / 8 skipped** offline, and **836 passed with nothing skipped** under `pytest --live`, which is the run that exercises the real embedding provider.

Three things this exit does **not** claim. The relevance figures in that demonstration (0.14–0.29) are what a lexical offline embedding produces and say nothing about the real one. The live tests ran against OpenRouter, not OpenAI direct, exactly as ADR-001's earlier rows are scoped. And embedding tokens are *captured* per call, not yet aggregated — `UsageSummary` is Phase 4, and building it here would mean building half of Phase 4's cost model against one caller.

## Phase 4 — Graph foundation — COMPLETE

- [x] `MigrationState` with reducers, including `merge_sources_by_id` — `models/state.py`, proven by `tests/unit/test_state_reducers.py` (12 tests). The state is **deliberately smaller than spec §6's listing**: the RAG, judgment and plan channels are absent because the eight models they hold do not exist yet, and each arrives with the phase that consumes it, exactly as the domain models did in Phase 1. `merge_sources_by_id` keeps the highest-relevance copy *whole* (a citation naming one chunk's id beside another chunk's score resolves to text that does not support the number next to it) and preserves first-appearance order, because the trace panel renders the list while it is still growing.
- [x] `TrackedLLM` service: usage extraction with fallback, tiktoken estimation path, `include_raw=True` — `services/llm/tracked.py`, proven by `tests/llm/test_tracked_llm.py` (15 tests). `_extract_tokens` returns `None` rather than a zeroed tuple when no surface is populated, so a real zero-token call stays distinguishable from an absent measurement and cannot silently suppress the estimation path. Only *recognised* provider errors are typed: a `TypeError` from our own prompt construction propagates unchanged rather than being dressed up as a retryable outage. Adds `LLMUnavailableError` / `LLMRateLimitedError`, the taxonomy carried in from Phase 2.
- [x] `MODEL_PRICING` in settings; unknown model yields `cost = None` — `config.DEFAULT_MODEL_PRICING` and `services/llm/pricing.py`, proven by `tests/unit/test_pricing.py`. Implements §9.4's measured refinement: a provider-reported charge is preferred over the table, tested with `is not None` rather than truthiness because a free tier really does bill `0.0`. A vendor prefix is deliberately **not** stripped — `openai/gpt-4.1-mini` is served by a gateway that sets its own price, so stripping it yields a confident number wrong by the gateway's margin; both spellings ship in the table instead.
- [x] `UsageSummary` derivation as a pure function — `models/usage.py`, proven by `tests/unit/test_usage_models.py` (19 tests). Deduplicates on `call_id`, first record wins. Four honesty invariants live in the type: a cost may not exist without a `CostBasis`, a basis that names an origin may not carry no number, an embedding may not claim output tokens, and a wholly unpriced run reports `None` rather than `0.0` — the per-call fabrication §9.4 forbids is easy to reintroduce at the aggregate level by summing an empty set of known costs.
- [x] `TraceEvent` emission helper — `models/trace.py`, proven by `tests/unit/test_trace_events.py`. CLAUDE.md rule 26 is enforced by *shape*: there is no `payload`, no `metadata`, no `dict[str, Any]` — nothing structured enough for a prompt or a chain of reasoning to travel in — and the tests pin the exact field set, so adding a hiding place turns a test red. `tests/graph/test_skeleton_graph.py::test_the_trace_never_carries_a_prompt` checks the same rule at the level where a node could actually violate it.
- [x] `AsyncSqliteSaver` wired — `graph/checkpointer.py`, proven by `tests/graph/test_checkpoint_serde.py`. **This turned up a real correctness bug, not a wiring detail.** LangGraph 1.2.11 warns "Deserializing unregistered type ... will be blocked in a future version"; measured against the pinned version, "blocked" does not mean *raises* — it means the value comes back as a plain `dict`. A resumed run would carry dictionaries everywhere it expects Pydantic models, so `BreakingChange.source` would stop being required, `RiskFactor.evidence`'s `min_length=1` would stop holding and `LLMCall`'s cost/basis agreement would stop being checked, with nothing raised at the point of loss. The allowlist is derived by walking `upgradepilot.models` (models **and** enums — `CostBasis` degrading to a bare string would make `is CostBasis.UNKNOWN` quietly false) so a model added in a later phase is registered by existing.
- [x] Skeleton nodes wired end to end with stub logic — `graph/nodes.py`, `graph/build.py`. The spine only: §8.5's two conditional edges are omitted because each is defined by a predicate belonging to a later phase (§8.2's interrupt predicate is Phase 7, the ten validation checks are Phase 8), and a conditional edge wired now would have to guess at its own condition. `human_review` is **absent rather than stubbed**, because its entire content is an `interrupt()` call and a version that does not interrupt is a node doing nothing where the run is supposed to stop. The `traced` wrapper enforces rule 20 once for every node rather than per body: a domain failure becomes an `AppError` carrying its own code, an unexpected exception becomes `AppError(INTERNAL)` naming the type, and the run continues so the report can say what *was* established alongside what failed.
- [x] Scripted fake chat model for tests — `tests/llm/fake_chat_model.py`. Reproduces the `with_structured_output(..., include_raw=True)` contract **as Phase 0 measured it**, not as would be convenient: a fake that invented a friendlier shape would let every token-tracking test pass while the real extractor read the wrong field. It refuses `include_raw=False` outright rather than approximating it, so the unsafe call cannot look available.
- [x] Tests: reducers, usage aggregation across a simulated resume with no double-count, unknown-model pricing, thread isolation — all present. One correction worth recording, because the first version of the resume test **overclaimed**: measured against the pinned LangGraph, `interrupt_before` pauses *between* nodes and does not re-execute the completed one, so there is no duplication for the deduplication to remove and an assertion that call ids were unique proved nothing. Replaced by two tests that are real — `::test_a_duplicated_call_record_does_not_change_the_totals` applies the duplicate through `aupdate_state` and the actual `operator.add` channel (asserting the channel *did* grow, so it cannot pass by the duplicate never arriving), and `::test_pausing_and_resuming_does_not_change_what_the_run_cost` asserts the model was invoked exactly once across the pause. The genuine re-execution case needs `interrupt()` inside a node body; ADR-001 records it and Phase 7 owns the test.

**Exit:** a graph executes start to finish over stubs with checkpointed state, and usage aggregation is proven idempotent. **Met 2026-08-25** — demonstrated by running the graph: paused at `generate_plan`, resumed to `next=()`, fourteen trace events covering every node's start and finish, one `LLMCall` recorded with `cost_basis='provider_reported'`, `by_node` attributing it to `assess_risk`, and no errors. Idempotence is proven twice: as arithmetic (`::test_usage_aggregation_is_idempotent`) and through the real channel (`::test_a_duplicated_call_record_does_not_change_the_totals`). Suite: **912 passed / 8 skipped**, mypy and ruff clean.

Four tests in this phase were written vacuous and only caught by mutation, which is worth recording as a pattern rather than four accidents: the source-ordering fixture happened to make insertion order equal relevance order; the checkpoint allowlist test passed against a serializer that registered nothing, because the permissive default allows everything; its "discriminating" companion used a model defined *inside* a test function, which is unimportable and so degrades regardless of any allowlist; and the resume test asserted a property the platform never violates. Each was strengthened until the mutation killed it.

## Phase 5 — Agentic RAG subgraph — COMPLETE

- [x] `RAGState` and shared channel mapping — `graph/rag/state.py`. Two kinds of channel, and the split is the design: child-only loop fields (`iteration`, `candidates`, `uncovered_symbols`, `retrieval_necessary`, `kb_unavailable`) that are meaningless outside the loop, and shared channels carrying *identical names and reducers* to the parent's, because the wrapper maps the child's accumulated values straight onto them. **The child's shared channels start empty, always** — seeding them with the parent's trace would return that trace as the wrapper's update and the parent's `operator.add` would append it to itself, duplicating every event before `agentic_rag` with the run still completing normally. `merge_chunks_by_id` and the parent's `merge_sources_by_id` now share one implementation (`models/state.merge_by_relevance`) rather than two that agree today: they are the same list seen from two sides, and a policy that differed would let the trace's source list and the gate's candidate list disagree about which copy of a document is the good one.
- [x] `plan_retrieval` — query generation from the symbol inventory; retrieval-necessary decision. **The skip decision is arithmetic, not a question put to the model.** Spec §7.3 says "zero usage sites means skip"; letting an LLM answer "is retrieval warranted?" hands it a way to skip gathering the evidence its own later answers are graded against. `::test_an_empty_inventory_skips_retrieval_without_calling_the_model` scripts the model with *no* responses, so any call at all raises — that is the assertion, not the trace event beside it.
- [x] `retrieve` — filtered search, symbol annotation, dedup. The `$contains` symbol join happens in the database (spec §7.2), so nothing post-filters in Python; what this node adds is `symbol_annotations`, so a chunk reached semantically still reports which of the repository's symbols it covers. **A symbol the model invented never reaches Chroma** — proposed symbols are intersected with the real inventory first, and the recorded `RagQuery` shows what was actually sent, because that record is what a reader checks the retrieval against (`::test_a_symbol_the_model_invented_never_reaches_the_store`, killed by mutation).
- [x] `evaluate_retrieval` — LLM coverage grading, with the model **not called at all** when nothing was retrieved: there is nothing to grade, the gate already knows the answer, and a prompt listing zero documents can only produce a guess. A grader outage defers to the gate rather than inventing an opinion — asserting `False` would force another round on a provider outage and asserting `True` would let an outage end the loop early — and `RagEvaluation.notes` says so, so the row is not mistaken for a graded round.
- [x] Deterministic sufficiency gate overriding the model — enforced in the *type*, not only in the node. `RagEvaluation.sufficient` is derived (`model_sufficient and gate_sufficient`), and `gate_sufficient` is refused unless it agrees with `uncovered_high_confidence`, so a passing gate cannot be recorded beside the evidence that it failed. Proven end to end by `::test_the_gate_overrides_a_model_that_declared_victory` (the model says sufficient on every round; the loop still runs its full budget) with `::test_a_covered_inventory_lets_the_loop_stop_at_one_round` as the complement that stops it passing vacuously. Both mutations — removing the `and`, and dropping the intersection — were watched failing.
- [x] Conditional edge with `MAX_RAG_ITERATIONS` bound — `graph/rag/build.py`. Three ways to stop, all real: sufficiency, `kb_unavailable` (a knowledge base down for query one is down for query two, and three rounds of it produce three identical errors and no evidence), and the budget. `>=` rather than `==` on the budget, because the value arrives from configuration and a zero must stop the loop rather than run it forever looking for an equality it will never reach (`::test_the_bound_is_a_floor_as_well_as_a_ceiling`).
- [x] `build_context` — `BreakingChange` construction with mandatory sources; uncovered symbols to `unknowns`. **No model is called here at all.** Each change is built from one retrieved chunk: the document's own title, severity and symbol list, the chunk's verbatim text as the description, and a `SourceRef` naming that exact chunk — so following a citation lands on the passage the change was built from (`::test_every_breaking_change_quotes_the_chunk_it_cites`). `old_form` / `new_form` stay `None`: the corpus carries both, but only as prose, and extracting them means either a paraphrase or a parser guessing which fenced block is which — a wrong code sample labelled "the new form" is the most actionable kind of lie this product could tell. A document naming no symbol this repository uses stays in `retrieved_sources` and is **not** asserted as a change affecting this codebase.
- [x] Explicit wrapper node with subgraph failure handling — `graph/nodes/evidence.py::make_agentic_rag`. Two reasons a bare compiled-graph node is refused, and the second is the one that matters: an empty symbol inventory means two entirely different things — "this repository does not use the dependency" and "the analysis failed" — and only the parent can tell them apart, so the subgraph would describe a failed run to the user as a clean repository (`::test_a_failed_analysis_does_not_make_retrieval_claim_a_clean_repository`). Failure conversion is **not** re-implemented here: every child node wears `traced`, and a second try/except in the wrapper would add a path that swallows the child's accumulated trace and usage records, including calls the provider has already billed.
- [x] Tests: refinement path with two query rounds, iteration cutoff, gate overriding a falsely-sufficient model, `KB_UNAVAILABLE` degradation — `tests/graph/test_rag_subgraph.py` (15 tests) and `tests/unit/test_rag_models.py` (22). The refinement test asserts what was actually *asked*: a loop that iterates while issuing the same query twice has learned nothing, so the assertion is that the second planning prompt carries the first round's `missing_topics`.

**Carried in, and done here because Phase 5 could not run without it:** `analyze_repo` and `inspect_dependency` were still stubs — Phase 2 built the analyzer as a service and no phase owned wiring it into the graph, so the RAG loop would have had no symbol inventory to search with. Both are real now (`graph/nodes/evidence.py`). `analyze_repo` opens and closes the workspace inside its own node, which has a consequence worth stating: **no later node can read the repository.** That is what makes the run resumable — a run pauses at `human_review` and may be resumed days later by a different process, a workspace handle cannot survive that, and a remote clone re-opened on resume is a different checkout of a branch that may have moved. Every file, line and version fact the report prints is captured here, into state, where the checkpoint preserves it exactly as it was read. `asyncio.to_thread`, because a synchronous whole-repository parse on the event loop would stall every other run's status poll under Phase 9's concurrency.

**Also carried in:** `graph/nodes.py` became a package (`base` / `evidence`), `traced` became generic over state so subgraph nodes wear it too, node bodies may now return a reserved `summary` key that becomes their `node_completed` text (without it every timeline row reads "assess_risk finished"), and `build_graph` takes a `GraphDeps` object rather than a growing keyword list. The Phase 4 foundation tests were rewritten onto the **real** graph — a real repository parsed by the real analyzer, a real Chroma collection, the real retrieval loop, with only the chat model scripted and the embedding function offline. Nothing about the properties they assert changed; what changed is that a foundation that only worked for nodes returning `{}` no longer passes.

**Exit:** the agent performs multiple retrieval iterations when the first result set is insufficient, and can name which sources informed the outcome. **Met 2026-08-25** — demonstrated by running the graph, not argued: round 1 searched for `validator` alone, was graded insufficient by both the model and the gate with `uncovered_high_confidence=('BaseModel', 'Config', 'Optional')`; round 2 issued a *different* query driven by round 1's `missing_topics`, and both judges then agreed (`stop_reason=sufficient`, `iterations=2`, `unknowns=()`). Four breaking changes were built, each naming the exact chunk its description is quoted from. Suite: **955 passed / 8 skipped**, mypy and ruff clean.

One thing this exit does **not** claim: the relevance figures are what the offline lexical embedding produces and say nothing about `text-embedding-3-small`. The pipeline is what is proven here — filters, the symbol join, dedup, the loop, the gate — exactly as Phase 3's golden-set floors are scoped.

## Phase 6 — Risk assessment — COMPLETE

- [x] Mechanical factor extraction for all seven factors — `services/risk/factors.py`. **A factor with nothing to cite is omitted, never emitted empty.** `RiskFactor.evidence` has `min_length=1`, so the type already refuses an uncited factor; the choice this module makes is what to do about that, and the choice is silence. Reaching for an unrelated repository line so the constructor accepts is a fabricated citation, which is worse than a missing factor by exactly the margin this project is about. Omission is visible: an empty factor set forces a confidence ceiling, and the report prints the factors it has rather than seven rows of which some are furniture. Evidence is capped at six refs per factor and **the cap is reported in the detail**, because a silent cap reads as the total.
- [x] Documented threshold table — `services/risk/thresholds.py`. **Every metric is a gap, phrased so higher is worse, without exception** — so `test_coverage_of_affected` measures the share of affected files *without* a locatable test. With mixed directions one comparison written the wrong way round produces a plausible level that is exactly inverted, and nothing downstream can tell. Boundaries are inclusive at the named value, and the test-coverage and constraint-pressure boundaries are exact thirds rather than 0.34/0.67: the detail line prints the metric as a rounded percentage, so two untested files out of three printed "67%" while grading MEDIUM against a boundary of 0.67 — a reader checking the level against the table by hand would have been right and the code wrong.
- [x] LLM narrative synthesis over a fixed factor set — `graph/nodes/judgment.py`. The whole analysis is built **before** the call, and two things follow. The model receives a finished verdict to describe rather than a question to answer, and `RiskNarrative` carries `summary` and `notes` and nothing else — a field the model cannot fill in is a field it cannot get wrong, which is the only way that guarantee can be structural rather than a prompt asking nicely. And a provider outage costs the narrative, not the verdict: the numbers are already computed, so the summary degrades to a sentence assembled from the factors themselves, an `AppError` is recorded, and the run continues (`::test_an_unreachable_model_costs_the_narrative_and_not_the_verdict`).
- [x] `overall_risk` clamp against confirmed breaking-change severity — enforced in `RiskAnalysis`, not in the builder. `overall_risk` must equal `max(aggregate_risk, clamp_floor)` and **both directions are refused**: below the floor is the failure spec §8.1 names, and *above* the computed maximum is a quieter one — a verdict inflated past what its own inputs support is unfalsifiable in the report and destroys the reader's ability to tell a serious finding from a cautious one. `RISK_ORDER` exists because `RiskLevel` is a `StrEnum`: `max(RiskLevel.HIGH, RiskLevel.LOW)` is `LOW`, so a clamp written with the obvious operator would quietly clamp **down**. Only high-confidence exposures feed the clamp — it is the strongest mechanism in the system, so it is fed only by evidence the analyzer is surest of.
- [x] Confidence ceilings: no evidence, skipped files, transitive-only, uncovered symbols — plus three this phase adds (unreadable history, the analyzer's own confidence reducers, and an empty factor set). `RiskAnalysis` refuses a confidence above the lowest recorded ceiling, so the check needs nothing from outside and can only be evaded by *not recording* a ceiling, which is a visible omission rather than an invisible one. Base confidence is **0.85, never 1.0**: this system reads a repository without executing it, so a symbol reached through `getattr`, a plugin loaded by name or a dependency pinned by an environment the manifest does not describe is invisible to any amount of parsing — a run reporting full confidence would be claiming a completeness the method does not have, on every repository, before any specific gap was found.
- [x] Tests: each factor, each threshold boundary, each clamp and ceiling — `tests/unit/test_risk_factors.py` (43), `tests/unit/test_risk_aggregate.py` (23), `tests/graph/test_assess_risk_node.py` (6). Each guarantee is asserted twice: once through `build_risk_analysis`, which is how the graph reaches it, and once against `RiskAnalysis` directly, which is where it is enforced — a rule that lives in the builder holds until someone writes a second builder.

**Deviation from spec §8.1, recorded rather than slipped in.** The spec has the model propose a verdict which the clamps then override. Here the model is never handed `overall_risk` or `confidence` at all: both are computed from the factors, and the clamp raises the first while the ceilings cap the second. That is a *stronger* property with the same intent — a clamp that never has to fire cannot be got round — and it is what makes `::test_qualitative_notes_carry_no_weight_in_any_level` assertable: the same inputs with mild prose and with "THIS IS CATASTROPHIC AND CERTAIN" produce identical numbers.

**Two corrections to spec §8.1's wording, both measured.** `blast_radius`'s denominator is `total_python_files`, not `analyzed_files`: candidate selection admits files *because* they mention the dependency, so affected ÷ analyzed is close to 1 for every repository ever analysed and measures nothing. And `EvidenceRef` gained a third variant, `ConstraintEvidence` — `constraint_pressure` is one of the seven factors, `RiskFactor.evidence` is `min_length=1`, and a constraint is neither in the repository nor in the corpus; the three options were a fabricated citation, an exemption from the evidence rule that the next factor walks through, or saying what the evidence really is.

**A real defect found by this phase's tests, worth recording as a class rather than an incident.** The RAG loop was non-terminating whenever an *unexpected* exception occurred inside `plan_retrieval`. `traced` discards a failed body's update — correctly, since a half-built update is not trustworthy — so the `iteration` counter the router bounded on stopped advancing, and the graph spun forever: the run never completes, the API never returns, and the only symptom is a checkpoint file growing on disk. Measured, not theorised: a scripted model that ran out of responses stood in for the bug and the test suite hung. Fixed by deriving the bound from the one channel no node body can suppress — `traced` emits `node_started` for every execution, success or failure — so `rounds_started` advances even on a round where every node raised. `::test_the_bound_advances_even_when_every_node_body_failed` and `::test_a_round_whose_planning_died_ends_the_loop_rather_than_re_searching` hold it. The general lesson: **a loop bound written by a node body is a bound that stops advancing the moment that body fails.**

**Exit:** risk output is traceable to repository and corpus evidence, and no clamp or ceiling can be bypassed by the model. **Met 2026-08-25** — demonstrated by running the graph over the fixture repository: six factors, every one citing either a real `file:line` from the analyzer or a real corpus `chunk_id`; `overall_risk=high` with `clamp_floor=high` from four confirmed exposures; confidence 0.50, capped from 0.85 because 14% of the repository's Python files could not be parsed, with the reason printed beside the number. The model's contribution was the two sentences of narrative and one qualitative note, and nothing else. Suite: **1033 passed / 8 skipped**, mypy and ruff clean.

## Phase 7 — Human-in-the-loop — COMPLETE

- [x] Strategy enumeration and scoring — `services/strategy/catalog.py`. Three strategies with **fixed** axis values, and fixed is a real decision: a strategy's risk here is the risk of the *approach* (a single cutover is a riskier way to change code than a staged one, whatever is being changed), not the risk of this particular upgrade, which is what `RiskAnalysis` measures. Mixing them would make the options move under the reader as the repository changed, and a comparison whose axes move is not a comparison. Ranking is **lexicographic ordering, not weighting** — the first version summed weighted penalties and recommended the *highest*-effort strategy to a user who had asked to minimise effort, because the lowest-risk option's risk saving outweighed its effort cost at whatever weights happened to be written down. Every fix was a matter of choosing a bigger number, which is a sign the model was wrong rather than the numbers. A stated preference is not "this axis counts 3×", it is "compare on this first".
- [x] Interrupt predicate: ≥2 viable strategies differing on an axis constraints do not settle — `catalog.needs_human_choice`, tested against constraint combinations alone (`tests/unit/test_strategy_predicate.py`, 23 tests) rather than through the graph, because the interesting cases are combinations of booleans and one graph run per combination would be a minute of git and Chroma per boolean. Viability is decided **only by hard constraints** (a stated zero-downtime requirement rules out a cutover); every other constraint settles an *axis* rather than deleting an option, because deleting on a preference can empty the set — zero-downtime plus minimise-effort would leave nothing — and a run with no viable strategy has no plan to generate. The default `risk_tolerance=MEDIUM` settles nothing: reading a default as a stated preference would settle the risk axis on every run and no strategy question would ever be asked.
- [x] Four typed decision kinds — `services/strategy/questions.py`, each behind a deterministic trigger so a run either has a real question or has none. `RISK_ACCEPTANCE` needs **both** halves (high risk *and* thin confidence): a high-risk verdict at high confidence is a finding, not a question, and asking whether to accept it would be asking someone to overrule evidence the system is sure of. `SCOPE_TRADEOFF` needs a near deadline *and* enough affected files, because below that a full migration is a day's work whatever the deadline. `DISCREPANCY_RESOLUTION` refuses to resolve silently in either direction — preferring the manifest would override a user who knows their deployment, and preferring the stated version would plan an upgrade from a version the repository does not have.
- [x] `InterruptPayload` with reason, evidence, options, tradeoffs, consequences — `models/decision.py`. Constraints exist to stop a question that cannot be answered from being asked: fewer than two options is not a choice, a recommendation naming an option that is not offered renders as no recommendation at all, and an option with no consequences is a button whose effect the person pressing it has to guess. `DecisionOption.supporting_evidence` is `min_length=1` for the same reason `RiskFactor.evidence` is: an option is a recommendation, and a recommendation with nothing behind it is plausible prose with a button next to it.
- [x] `interrupt()` call and `Command(resume=...)` handling — `graph/nodes/judgment.py::make_human_review`, and the conditional edge in `graph/build.py`. **`human_review` answers one question per execution**, with the router sending the run back for the next. Asking all of them in one execution looks tidier and is wrong: a node that interrupts produces no state update until it finishes, so answers to earlier questions sit in LangGraph's resume store and never reach `human_decisions` — measured, with the channel coming back empty from a two-question run that had answered one. Everything downstream derives from that channel, so a partially-answered run kept showing the question it had already answered.
- [x] Resume-payload validation; re-interrupt on unknown option — `_as_decision` returns the complaint as a *string* rather than raising, because a raised error would be caught by `traced`, recorded as a failure, and the run would continue **past** the question with no answer — turning "you sent something unusable" into "nobody was asked". The complaint travels back on `InterruptPayload.validation_error` so the person answering sees it instead of a log line, and the re-ask loop terminates because each pass consumes one more already-supplied resume value.
- [x] Tests: interrupt fires, checkpoint persists, resume continues the same thread, no-interrupt when constraints decide, invalid decision rejected, multiple sequential interrupts — `tests/graph/test_human_in_the_loop.py` (20 tests) plus the predicate suite. `::test_no_model_call_happens_while_the_run_is_paused` holds ADR-001's rule where it can actually be violated.

**Two defects this phase found, both of which produced a plausible wrong answer rather than a crash.**

1. **`traced` swallowed the interrupt.** `interrupt()` raises `GraphInterrupt` to pause the run, and rule 20's catch-all converted it into `AppError(INTERNAL)`: the graph recorded "an internal error occurred while running human_review", continued to the end, and produced a complete report for a question nobody was ever asked. Fixed by re-raising `GraphBubbleUp` — LangGraph's own base for control flow, rather than `GraphInterrupt` specifically, because `ParentCommand` and `GraphDelegate` are control flow too and a handler naming only the one we happened to hit would swallow the next one silently.
2. **`StateSnapshot.next` is `()` for a run that is genuinely paused.** Measured (`probes/probe_interrupt.py`): whenever the pause comes from a *second* `interrupt()` inside one node execution — which is exactly what re-asking after an unusable answer does — `next` reports empty while `tasks[*].interrupts` correctly reports one. Spec §9.2's status ladder puts "checkpoint has interrupts" first, and this is what "has interrupts" has to mean: `graph/inspect.py` reads only `tasks[*].interrupts`, and `::test_a_re_asked_question_still_reads_as_awaiting_a_human` pins the measurement so a LangGraph change that fixes `next` turns a test red rather than leaving a stale workaround. Reading `next` would have made Phase 9 report such a run as COMPLETED: the client stops polling, the question is never answered, and a partial report is presented as final.

**Deviation from spec §6, recorded.** The state channel is `pending_decisions` (a list), not spec §6's singular `pending_decision`. Spec §8.2 requires that "multiple sequential interrupts work naturally", and a single slot cannot hold two unanswered questions. The singular thing the API surfaces — *the* question currently awaiting an answer — is derived by `models/decision.unanswered`, never stored, because a stored pointer drifts the moment a resume lands on `human_decisions` without it.

**Exit:** a genuine tradeoff pauses the graph with enough context to decide; a settled question does not pause it at all. **Met 2026-08-25**, demonstrated in both directions over the fixture repository. With default constraints the run stops at `human_review` with a `strategy-choice` payload naming three options, their risk/effort/downtime positions, a recommendation, four evidence refs (two corpus chunks, two `file:line`s) and what happens if nobody answers; an answer of `does-not-exist` is refused with "Choose one of: compatibility_layer, staged_rollout, direct_migration" and the run stays paused; a real answer is recorded and the run pauses again on the second question before finishing. With `zero_downtime + minimize_effort + risk_tolerance=low` the strategy question is **not asked at all**, and a trace event says "resolved by the stated constraints". Suite: **1080 passed / 8 skipped**, mypy and ruff clean.

## Phase 8 — Plan generation and validation — COMPLETE

- [x] `generate_plan` with per-step evidence requirements — `graph/nodes/planning.py`. **The model names symbols and documented changes; the file paths come from the analyzer.** CLAUDE.md rule 19, enforced by the schema: `PlannedStep` has no `files` field and no `requires_downtime`, so a symbol the model invented resolves to no file and contributes nothing — it cannot conjure a path. A step that resolves to neither a file nor a citation is dropped rather than constructed, which is the difference between a plan with one fewer step and a node that crashes on a vague answer. `MigrationStep` refuses one anyway (spec §8.3), so the two halves agree.
- [x] `human_decisions_applied` with `how_it_changed_the_plan` — produced together with `strategy_id` in `_chosen_strategy`, not written afterwards, because the two must not be able to disagree: a plan whose strategy came from a human and whose applications list is empty is exactly what check 9 refuses, and building both in one place means there is no path that sets one without the other.
- [x] All ten deterministic validation checks — `services/plan/validate.py`. **Every check is always reported**, passing or failing: a report listing only failures is indistinguishable from a report where the checks did not run. Check 1 reaches the live store, because a citation to a chunk a re-ingest has since rewritten away is precisely the quiet failure it exists for — the citation still looks right, and the reader following it finds a real document with no such passage. An unreachable store fails that check rather than skipping it: "we could not check" must not read as "we checked and it was fine".
- [x] Bounded single repair retry — `route_after_validate`, with the brief generated by `repair_brief` from the failing checks themselves rather than hand-written in the node, so a check whose meaning is refined describes itself to the retry.
- [x] `COMPLETED_WITH_WARNINGS` terminal path — derived from `validation.passed`, never stored. A validator that can be retried indefinitely is one the generator learns to satisfy by attrition, and a run that loops is worse for the user than a run that says what is wrong with its own output.
- [x] `finalize` as a pure function — `FinalReport` is assembled from state alone, with `UsageSummary` derived from `llm_calls` rather than read from a stored total. Pure matters concretely: the API may build this from a checkpoint long after the run ended, and a `finalize` that read anything outside state would produce a different report the second time. The report carries `AppError.message`, never `detail` (CLAUDE.md rule 27) — the technical field is for logs correlated by `thread_id`, and putting it in the report would leak provider responses and internal exception text into a document people share.
- [x] Tests: every check with a passing and failing case, the repair retry, and the decision-flip test — `tests/unit/test_plan_validation.py` (31) and `tests/graph/test_plan_generation.py` (11). `::test_a_clean_run_passes_every_check` is what stops the failing cases all passing for the wrong reason: a validator that failed everything would satisfy every one of them.

**Two spec deviations, both recorded rather than slipped in.**

1. **Checks 2 and 3 resolve against the analysis record, not the workspace.** Spec §8.4 phrases them as "the file exists in the workspace"; the workspace is gone by then, because `analyze_repo` opens and closes it inside its own node so that a run can pause at `human_review` and resume days later in a different process. `RepoAnalysis.citable_paths()` / `.citable_lines()` are what they resolve against instead — a *strengthening*, since "exists on disk" would accept any path in the repository including one nothing here ever read, while the analysis record is the set of locations this system is entitled to name.
2. **A sixth confidence input.** Check 6 also bounds `confidence` by `BASE_CONFIDENCE`. `RiskAnalysis`'s own validator bounds it by the recorded ceilings and knows nothing about the base, so a verdict of 1.0 with no ceilings constructs cleanly — and claims a completeness a method that never executes the code cannot have.

**The same defect class, found a second time.** The repair loop was non-terminating whenever an unexpected exception hit `generate_plan`: `traced` discards a failed body's update, so `plan_attempts` stopped advancing and the router never reached its bound. Identical in shape to Phase 6's RAG-loop finding and fixed the same way — `attempts_started` counts `generate_plan`'s `node_started` events, which `traced` emits whatever the body does. Recorded here as a rule rather than a second incident: **a loop bound written by a node body is a bound that stops advancing the moment that body fails.** Both loops in this system are now bounded on the trace.

**Exit:** plans are validated for real, failures are visible, and the human decision demonstrably changes the output. **Met 2026-08-25** — demonstrated by running the graph: a four-step plan whose every file path came from the analyzer (`src/app/models.py`, `src/app/consumer.py`, `src/app/service.py`), both answered decisions recorded with what they changed, ten of ten checks passing with their reasons printed, and a final report stamping the real commit sha and four model calls at $0.00056. Answering the *same* question with `direct_migration` instead of `staged_rollout` yields `strategy_id=direct_migration` **and** a step marked `requires_downtime` — the decision flip, verified rather than claimed. The failure paths are demonstrated too: a draft that produces no usable step fails check 7, is regenerated exactly once with the failed check named in the repair prompt, and a second failure terminates as `COMPLETED_WITH_WARNINGS` rather than looping. Suite: **1128 passed / 8 skipped**, mypy and ruff clean.

## Phase 9 — API layer — COMPLETE

- [x] Pydantic request and response models; `RunSnapshot` as the single response shape — `api/schemas.py`. One shape for every state, so the frontend renders one thing and never branches on which endpoint replied; `::test_the_snapshot_is_one_shape_in_every_state` asserts the key set is identical across a running, a paused and a finished run. **Errors are reshaped on the way out**: `ApiError` is `AppError` minus `detail`, as a separate model rather than an exclusion rule, because an exclusion is what someone forgets when they add a field (CLAUDE.md rule 27).
- [x] `POST /api/agent/start` returning 202 — accepted, not finished: a full run takes minutes and an HTTP client that waits for one has already timed out. A request naming both a URL and a path is **refused** rather than resolved by precedence — quietly preferring one would analyse a repository the caller did not mean to name, with every citation in the report pointing at the wrong tree.
- [x] `GET /api/agent/status/{thread_id}` deriving status from checkpoint plus registry — `api/status.py`. Never stored (spec §6.5): a status field written by a process would say `RUNNING` forever after that process died, which is precisely the case `ORPHANED` exists for.
- [x] `POST /api/agent/resume` with 409 / 404 / 422 behaviour — two legitimate cases and everything else refused: `AWAITING_HUMAN` with a decision, and `ORPHANED` **without** one. An abandoned run is not waiting for an answer, and asking the client to invent one would be asking for a lie. A resume against a completed run is 409 rather than a quiet re-run, which would bill a second time for a report that already exists.
- [x] `RunRegistry` with concurrency semaphore and `QUEUED` state — `api/registry.py`. The handle is registered **before** the task is created, so a status poll landing in that window sees `QUEUED` rather than nothing; without that ordering a just-started run reports as `ORPHANED`, which is rare, entirely real, and the kind of race that only shows up under load. A cancelled task is deliberately not `FAILED`: it is a run whose process is going away, which the checkpoint describes better.
- [x] `ORPHANED` detection and resume-from-checkpoint — `::test_a_checkpoint_that_outlived_its_process_reads_as_orphaned` simulates the restart exactly: the graph is driven partway, then the status is derived with a **fresh** registry, which is what a restarted process has. The resume then continues rather than restarting, asserted by the resumed state still holding the risk analysis the first half produced.
- [x] Centralized error handler over the full taxonomy — `api/errors.py`, a *dispatch* rather than a table: every `UpgradePilotError` carries its own `http_status`, so adding an error type sets its status where the type is defined. A mapping here would be a second list to keep in step, whose failure mode is a new error class quietly answering 500. FastAPI's own body validation is reshaped into the same `ErrorResponse`, because a contract with two different 422 bodies is one whose client error rendering works for one of them.
- [x] CORS from settings — an explicit origin allowlist, never `*`. The browser sends headers to whatever is allowed, and a wildcard in a service that will later hold repository credentials is a decision nobody would make on purpose.
- [x] Tests: every status code, schema assertions, orphan detection — `tests/api/test_agent_api.py` (17) and `tests/api/test_run_status.py` (12). The application under test is the **real** one — real lifespan, routes, error handlers and CORS — over a graph whose chat model is scripted and whose embeddings are offline, reached through a `runtime_factory` seam rather than by monkey-patching, because a patched module-level name is not the code that runs in production.

**Three defects this phase found, all of which produced a confident wrong answer.**

1. **`next == ()` does not mean "finished".** Spec §9.2 words the second rung of the ladder as "checkpoint next == ()". Measured against the pinned LangGraph, that condition is true at *two* moments: at the end of a run, and immediately after the input is written, before the first node's task is scheduled. A client polling a second after `start` was told `completed`, with an empty trace and no report, and stopped polling. Completion is now read from `final_report`, which `finalize` sets and nothing else does — and `traced` guarantees every path reaches `finalize`.
2. **`/api/health` reported on the wrong settings.** It called the cached global `get_settings()` while the application ran on an injected `Settings`, so it reported a model key as present while every run failed for the lack of it. It now reads `request.app.state.settings`.
3. **`sweep_stale` ran on only one construction path.** Phase 2's carry-in is now wired, and it lives in the lifespan rather than inside `open_runtime` so it runs however the application is built — an action that only happens on one of two paths is an action nobody can test on the path they use. Startup only, because the sweep matches on directory name and mtime and a timer calling it could remove a workspace a slow analysis still has open.

**Also closed here:** two Phase 2 carry-ins — `api/app.py` no longer calls `create_app()` at import time (importing the module opened a SQLite connection and read a Chroma directory), and `sweep_stale` is wired. And four derived values the report renders (`ValidationReport.passed`, `FinalReport.completed_with_warnings`, `RagContext.evidence_available`, `RagEvaluation.sufficient`) became `@computed_field`, so they reach the client: a derived value the frontend cannot see is one the frontend re-derives, which is a second implementation of the rule in a language that cannot check it against this one.

**Exit:** all backend functionality is reachable through the documented contract, verified manually before frontend work begins. **Met 2026-08-25** — every endpoint driven over HTTP against the real application, in one session: `GET /api/health` → 200 `degraded` naming the missing key; `GET /status/{unknown}` → **404** `thread_not_found`; `POST /start` with both a URL and a path → **422** `invalid_repo_url`; `POST /start` → **202** with a `poll_url`; polling mid-run → `awaiting_human` with four completed steps, three affected files, four breaking changes, a `high` risk verdict, live usage (`3 calls / 360 tokens / $0.00042`), the full `strategy-choice` payload and `final_report: null`; `POST /resume` with an unknown option → **202** and the question re-asked carrying "Choose one of: compatibility_layer, staged_rollout, direct_migration"; answering properly → `completed`, eight completed steps, a four-step `compatibility_layer` plan, **10 of 10** validation checks passed, both decisions recorded, and a final report; `POST /resume` on the finished run → **409**; on an unknown thread → **404**. Suite: **1157 passed / 8 skipped**, mypy and ruff clean.

**The limitation spec §9.2 names, restated because it is now load-bearing:** the registry is in memory, so **Spec 1 must run single-worker**. Under `uvicorn --workers 2` there are two registries and half the status lookups are blind — a run started by worker A would be reported by worker B as `ORPHANED`, offering to restart work that is currently in progress. Sub-project 3 moves the registry into Postgres and lifts this.

## Phase 10 — Frontend — CODE COMPLETE, EXIT PENDING ONE HUMAN PASS

- [x] `openapi-typescript` type generation from the live schema — `backend/scripts/dump_openapi.py` writes `frontend/src/api/openapi.json`, `npm run gen:api` derives `schema.d.ts`, and `api/types.ts` re-exports the aliases the app uses. The schema is **checked in** rather than fetched at build time, so a typecheck cannot pass against a contract no running backend serves. Verified current at exit: regenerating both files produced an empty `git diff`.
- [x] `useRunPolling` hook with backoff, terminal-stop, unmount abort — `hooks/useRunPolling.ts`. One second between ticks, and the next tick is scheduled only after the previous **settles**, so a slow response cannot stack requests. Failures back off `1→2→4→8→15s`. ADR-001 A3 defers SSE, so this is a poll and the UI says *live · 1s poll* rather than "streaming" — a badge claiming a transport the system does not have is a claim like any other.
- [x] Status-derived view routing — `derive/view.ts`. `viewFor` is an exhaustive switch with **no `default` clause**, deliberately: a status the backend adds becomes a compile error rather than a blank screen. Verified by deletion at exit — removing a case makes `tsc` fail with TS2366.
- [x] Configuration form with inline 422 rendering — `components/ConfigurationForm.tsx`. Six fields and no model, temperature or additional-context input: configuration is environment variables (rule 14) and the API exposes no configuration endpoint, so a control here would be a setting that goes nowhere. `FIELD_FOR_CODE` is a `Partial<Record<>>` on purpose — an unmapped code renders in the banner, because a `kb_unavailable` is about the system and attaching it to an input tells the user to fix the wrong thing.
- [x] Activity timeline — `components/ActivityTimeline.tsx`, over the eight-step `derive/steps.ts`. `human_review` is the only skippable step, and `awaiting` outranks `completed` so a paused run never reads as finished. **The checklist said "with expandable steps" and that wording is struck rather than ticked:** the timeline is a flat list, and nothing in `docs/ui/DESIGN.md` asks otherwise — expandable rows are specified for the *risk factor* table (§286, "its weight, its detail, and — expandable — the `EvidenceRef`s it cites"), which `RiskFactorsTab` implements, and §557-561 explicitly rules validation checks out as progressive-disclosure candidates. The plan's phrasing described a control the design never asked for. Found by the whole-branch review, in this paragraph, after it was written.
- [x] Evidence panel with relevance and source references — `components/EvidencePanel.tsx` and `EvidenceTab.tsx`. Selected sources are distinguished from merely-retrieved ones by `BreakingChange.source.source_id`, **not** by parsing trace prose — the first implementation did parse prose, and the badge it drove could never be true.
- [x] Human Review panel over a still-incomplete timeline; triple duplicate-submit guard — `components/HumanReviewPanel.tsx`. The guard is `selected === null || submitting || settled`, `submitting` is set before the await and never cleared on success, and a 409 sets `settled` permanently. The panel is keyed by `question_id` so a second interrupt mounts fresh.
- [x] Report view: risk, confidence, affected files, breaking changes, evidence, plan, mitigations, decisions — `components/report/`, five tabs and no PR-draft tab (GitHub writes are sub-project 2, so a tab here would be a button with nothing behind it). Real ARIA tablist: `aria-controls`, `role="tabpanel"`, roving `tabIndex`, arrow keys with `Home`/`End`.
- [x] Persistent metrics sidebar including estimated and pricing-unknown flags — `components/RunMetrics.tsx` with `derive/cost.ts`. `null` cost renders *not priced*, `!pricing_complete` renders `≥ $X` rather than a total that omits a model whose price is unknown, and `estimated` is orthogonal to both.
- [x] Agent Trace drawer — observable events only — `components/AgentTraceDrawer.tsx`. Node boundaries, queries, sources retrieved and selected, decisions, validation outcomes. No prompts, no reasoning (rule 26).
- [x] Error and orphan views with retry / resume — `components/ErrorView.tsx`. The orphan copy says what is known — a checkpoint outlived its process — and never why it died, because no field says. Resume carries **no** decision: an abandoned run is waiting for a process, not for an answer.
- [x] Semantic Tailwind color tokens; `aria-live` on status — `index.css` defines four semantic tokens and no call site uses a raw colour. `TopBar` is the `aria-live` region. Status is never colour alone.
- [x] Tests: polling hook with MSW, Human Review panel behaviour — **195 tests across 24 files**, MSW configured `onUnhandledRequest: "error"` so an unstubbed call fails loudly rather than silently.

**Nineteen defects in the plan text rather than in an implementation, plus several in this document's own design spec, in a type name, and in the rulings written to correct the plan — and every one of them produced a confident wrong answer rather than a crash.** The four most instructive:

1. **The clamp banner claimed a raise that never happened, because this document's own design spec told it to.** `docs/ui/DESIGN.md` said the clamp floor is "rendered whenever it differs from the aggregate", so the banner was gated on `clamp_floor !== aggregate_risk` while its text read *"Raised from {aggregate} to {overall}"*. But `overall_risk` is exactly `max(aggregate, floor)` — a model validator refuses both directions — so a floor **below** the aggregate raises nothing and rendered **"Raised from high to high"**. One sentence in a design doc doing two jobs: naming a display condition while implying a claim. The fix mirrors the backend's own condition at `graph/nodes/judgment.py:244`; the doc was corrected first, so the code fix was not made against the spec that caused it.
2. **The plan tab recreated the exact failure the check above it exists to refuse.** It rendered *"Every affected file is addressed by a step"* whenever `unaddressed_with_reason` was empty. That array holds only gaps carrying an **honest documented reason**; a gap without one produces no entry and fails check 8 instead. So a `completed_with_warnings` report could show "every file is addressed" three panels above a Validation panel naming the files that were not. Check 8's own docstring says what it refuses: *"silence — a file that is neither addressed nor mentioned, which is how a partial plan reads as a complete one."* The panel now reads that check's outcome and renders its offenders — mirroring the verdict rather than re-deriving it.
3. **Two decisions, each correct alone, bricked the second interrupt.** The duplicate-submit guard deliberately never clears `submitting` after a success. The panel was rendered without a React `key`. Together, a second question reused the first question's component instance and inherited `submitting=true`, so **question 2 could not be answered** — and sequential interrupts are exactly what the append-only `human_decisions` channel exists to support. All fourteen tests passed, because every one mounted a fresh panel through a helper while production re-renders a mounted one.
4. **A successful resume was invisible, because a set was named for what it was not.** `orphaned` sat in `TERMINAL_STATUSES`, which the poll loop uses to stop scheduling. So a resume reached the backend, the run continued, and the UI never looked again — the server logged success while the user saw a dead button. `orphaned` is not terminal; it is the one stopped-but-**resumable** state, which is why it has a resume affordance at all. The set is now `POLLING_STOPS_ON`, and the hook has a `restart`.

**The pattern worth carrying forward:** every rule-1 defect this phase produced was a rendered **string** or a rendered **style** — never a logic error. All of them typechecked. All of them passed their tests. The recurring shapes were a claim whose subject is wrong, an unmeasured cause stated as fact, silence where absence is itself reportable, and a severity the backend never assigned. `tsc`, `vitest` and `ruff` cannot see any of them. What found them was reading each rendered sentence against the docstring of the field backing it — a review pass, not a tool. One such sweep checked 44 strings in three components and 5 failed.

**Exit, the second clause: demonstrably met 2026-08-26.** *The workflow never appears complete while waiting for input* is a claim about data, and it was driven against the real API over three live runs. At `awaiting_human`, with `pending_decision` present and `completed_steps` = `analyze_repo, inspect_dependency, agentic_rag, assess_risk` and `current_step` = `human_review`, the snapshot's `migration_plan`, `validation` and `final_report` are all **`null`**. A second answer to a settled question returned **409 `thread_not_awaiting_input`**. Killing the backend mid-run and restarting yielded **`orphaned`** with two completed steps; resuming continued rather than restarting, the count growing **2 → 4** rather than resetting. Live telemetry, which no fake-LLM test can establish (rule 24): **6 calls / 4811 tokens / $0.0036224** on run 1 with `estimated: false` and `pricing_complete: true`, `by_model` = `openai/gpt-4.1-mini`; **$0.0056324 total across 11 real calls**. Gates: frontend **195 passed / 24 files**, `tsc -b` and `vite build` clean; backend **1168 passed / 8 skipped**, mypy clean over **145 files**, ruff check and format clean over 170 files; regenerated types produced no diff.

**Exit, the first clause: NOT yet demonstrated.** *The full journey is usable in the browser* needs a human at a browser, and this session has no browser automation, so it is deliberately not ticked. Four items remain: the screenshot of the paused state, visual dominance of the decision panel, keyboard-only submission, and the two-tab duplicate-answer behaviour. Three runs are parked for that pass at zero further cost — one `completed`, one `awaiting_human`, one `orphaned`. **This phase is not marked COMPLETE on a narration of a browser nobody opened.**

**A limitation Phase 11 inherits.** `RunSnapshot` publishes only `thread_id`, `status` and `usage` as required; the other fourteen fields carry Pydantic defaults and so are optional in the generated types, even though `api/runtime.py snapshot_response` populates every one on every call. Every component therefore resolves optionality it can never actually observe, and three separate defects traced to that gap. The model is constructed in exactly one place, so tightening it is contained — but it regenerates the schema under fifteen reviewed tasks, which is why it was recorded rather than done mid-phase.

## Phase 11 — End-to-end and requirements audit

**Worked after Phase 13, except for the CI item below, which gates it.** See the order note near the top. Nothing in this phase changed; only when it happens did.

- [ ] E2E HITL test: start → `AWAITING_HUMAN` → resume → `COMPLETED`, asserting resolving evidence, non-zero usage, applied decision
- [ ] Opt-in live test asserting real `usage_metadata`
- [x] CI: pytest, ruff, mypy, vitest, tsc — `.github/workflows/ci.yml`, three jobs (`backend`, `frontend`, `contract`). **Demonstrated, not merely written:** run 34336340695 on PR #7 is green across all three (backend 1m38s, contract 59s, frontend 29s). The `contract` job is the one not in this item's original wording and it earns its place — `openapi.json` and `schema.d.ts` are generated by the backend, consumed by the frontend, committed, and drift between them is silent, so regenerating both must be a no-op and only a job holding both toolchains can check it.

      **CI found two pre-existing failures on its first two runs, and both were invisible locally.** Recorded here rather than only in commit messages, because each says something about how the nine COMPLETE phases above were verified.

      1. **mypy was failing on a cold cache** — ten errors in two files untouched since Phase 2. `strict = true` implies `--warn-unused-ignores`, and five `add_node` sites carried `type: ignore[call-overload]` where mypy now reports `arg-type`. Warm, it is silence, and silence is what every phase exit claiming "mypy clean" was reading. Fixed in `d59313b`. No new structure was needed to stop it recurring: CI caches pip and npm and never `.mypy_cache`, so it type-checks cold every run. A second trap found while fixing it is recorded beside the ignores — `mypy src/upgradepilot` reports those same ten errors while the project's bare `mypy` reports none, because the overload narrowing depends on what else is in the checked set, so a narrowed run is not evidence either way.

      2. **A test asserted a macOS filesystem property** — `test_accepts_an_nfd_unicode_variant_of_the_allowed_root` re-spells a path in NFD and expects the same inode, which holds on APFS and not on ext4. The two case-variant tests directly above it already carried the `pytest.skip` guard it lacked, and CI confirmed the diagnosis from the other side by reporting 10 skipped against 8 locally. Fixed in `f7ac46a` by adding that guard — **not** by normalising paths in the guard itself, which would have made a security allowlist match a path that is not the file it names on any non-normalising filesystem. The guard is stricter on Linux, never weaker.

      The general lesson, since it applies to every row above this one: this suite had only ever run on one machine, on one operating system, with a warm cache. Nine phases of green were partly a property of that machine.
- [ ] Requirements audit below completed with evidence

### Requirements audit

**Token and cost tracking**
- [ ] Usage captured from real LLM calls (live test)
- [ ] Input / output / total tokens and estimated cost displayed
- [ ] Usage survives graph transitions without double-counting
- [ ] Usage present in API response and in React

**Checkpointing and human-in-the-loop**
- [ ] Checkpointer configured; thread IDs generated
- [ ] State persists across interruption
- [ ] `interrupt()` implemented on a meaningful predicate
- [ ] React renders the decision interface
- [ ] `/resume` continues the same thread; duplicates refused
- [ ] Human decision provably changes downstream generation

**Agentic RAG**
- [ ] ChromaDB knowledge base ingested with resolvable metadata
- [ ] Agent decides whether retrieval is necessary
- [ ] Agent evaluates retrieval quality; deterministic gate can override
- [ ] Agent refines queries and performs multiple iterations
- [ ] Sources preserved, displayed, and cited in the recommendation
- [ ] RAG evaluation documented as an executable golden-set test

## Phase 12 — Demo scenario and polish

**Worked after Phase 13.** See the order note near the top. One item gains a dependency by moving: the pinned demo scenario is what Phase 13.5's exit exercises, so if it is not fixed by then, that exit is demonstrated against an ad hoc scenario and this phase should say so when it lands.

- [ ] Pinned demo: fixture repository, Pydantic v1 → v2, zero-downtime plus deadline constraints
- [ ] A real public Python repository, vendored or cloned at a **fixed commit**, for the demo and the end-to-end path — the deferral recorded in Phase 1 and in spec §12 assumption 5, landing here. The hand-authored fixture stays the basis for analyzer unit tests; this is the second, realistic target
- [ ] Verify the run reliably reaches a meaningful HITL decision
- [ ] Empty, loading, and error states
- [ ] Responsive layout; keyboard-navigable controls; accessible labels
- [ ] Source links resolve
- [ ] Risk information visually unmissable
- [ ] Walkthrough notes covering why the recommendation was made

**Exit:** the full journey — configure, analyze, retrieve, assess, decide, resume, report, trace, cost — runs end to end on the pinned scenario.

---

## Definition of done for Sub-project 1

See spec §13. Summarised: a developer can point UpgradePilot at a repository and a version change, watch it gather real evidence, be asked exactly one meaningful question, answer it, and receive a validated migration plan in which every claim resolves to a real line of code or a real document — with token cost and full trace visible throughout, and a comprehensible error whenever something fails.

---

## Phase 13 — Deployment: Cloud Run backend, Vercel frontend

**Numbered 13, but runs BEFORE Phases 11 and 12.** Prioritised on the project
owner's instruction, 2026-09-09, which rule 2 allows on an explicit direction.
The number is kept so nothing above has to be renumbered; the *order* is what
changed, and this paragraph is the only thing that says so.

The re-ordering is more defensible than it first looks, and one reason is worth
stating because it inverts the obvious objection. Phase 10's exit is pending
*one human pass in a browser*, and Phase 11's headline item is an end-to-end
HITL run. **A deployed URL is what both of those need.** Doing deployment last
would mean building the thing that makes the remaining exits testable after
those exits were supposed to be met.

**Prerequisite, and the only one: MET 2026-09-09.** Phase 11's `CI: pytest,
ruff, mypy, vitest, tsc` is done and green — see that item for the run and for
the two pre-existing failures it caught on the way. Worth noting what that
means for this phase: CI found a cold-cache mypy failure and a macOS-only
test *before* any deployment existed, which is the argument for going early
working as intended rather than as a hope.

**What is knowingly accepted by going early.** The E2E HITL test, the
requirements audit and Phase 12's polish all now land *after* a deployment
exists. So the first deploy is not a claim that the journey works — it is the
environment in which that claim gets tested. Phase 13.5's exit criteria are
written to that standard: they demonstrate the *deployment*, and they do not
stand in for Phase 11's audit.

**Architecture record:** `docs/adr/ADR-002-deployment.md` (**Proposed**). Read it
first; it carries the reasoning, the rejected alternatives, and the three vendor
platform facts the design turns on. This section is the task list only.

**Target shape.** One Cloud Run instance serving the API; the Vite build on
Vercel with a `vercel.json` rewrite in front of `/api`; Clerk as the identity
and access gate; the corpus baked into the image; workspaces on an in-memory
volume; thread state and run ownership in Cloud SQL.

### 13.0 Pre-flight probes

This project retires unknowns with probes before designing around them
(`backend/probes/`, and ADR-001's Phase 0 verification record). Each of these
would change the plan if it came back wrong, which is what earns it a probe.

- [x] **`psycopg` on Python 3.14.** A real `cp314` wheel exists for
      `psycopg-binary`, so `psycopg[binary]` is used and the container needs no
      `apt-get install libpq5`. Installed and exercised against a live
      PostgreSQL 17.11, so this is not a reading of metadata.
- [x] **`sqlite-vec` and `ruff` on `linux/amd64`.** Both ship
      `py3-none-manylinux_2_17_x86_64`. Widened while proving it: **every**
      platform-specific distribution in the venv resolves from a `manylinux`
      x86_64 wheel for CPython 3.14 — nothing needs a source build and nothing
      is `musllinux`-only, which is what makes 13.2's Debian base viable.
      `probes/probe_wheels.py`, which sweeps the installed set by reading
      `WHEEL` tags so a new transitive cannot escape it.
- [x] **`AsyncPostgresSaver` round-trip across a real process restart.**
      `probes/probe_postgres_checkpointer.py`: one interpreter runs to
      `interrupt()` and exits, taking its pool and compiled graph with it; a
      second, which has never seen that graph object, reconstructs the run from
      the checkpoint alone and drives it to completion. The claim ADR-002 D2
      rests on is now measured rather than read.
- [x] **`clerk-backend-api` on Python 3.14.** Installed and pinned at `7.0.0`.
      `cryptography` resolved to `50.0.1` on `abi3`, so the 3.14 floor is
      untouched. What it actually adds to runtime is `cryptography`, `pyjwt`,
      `cffi` and `pycparser` — five new lines in `requirements.lock`, 112 to
      117.

      **A claim in the earlier version of this item was wrong and is corrected
      here rather than quietly edited:** it said adopting this "moves `httpx`
      from a dev-only dependency to a runtime one". It does not.
      `requirements.lock` already carried `httpx==0.28.1`, `httpx2==2.12.0`
      and `httpcore==1.0.9`, because `chromadb`, `langchain-core` and `openai`
      all depend on them. `httpx` has been shipping all along; the `[dev]`
      pin constrains a package that was already in the image. The mistake came
      from reading the SDK's declared requirements and assuming a new name
      meant a new dependency.
- [x] Record resolved versions in ADR-001's verification record (rule 13).
      Three rows added — the pinned versions, the wheel sweep, and the Postgres
      restart — each naming the probe that produces it. `psycopg 3.3.5 ·
      psycopg-binary 3.3.5 · psycopg-pool 3.3.1 ·
      langgraph-checkpoint-postgres 3.1.2 · aiosqlite 0.22.1`, with no conflict:
      the Postgres saver's `langgraph-checkpoint>=4.1.0,<5.0.0` and
      `orjson>=3.11.5` are satisfied by the installed `4.2.0` and `3.12.0`
      without moving either.

**What 13.0 cost, recorded because the probes were wrong before they were
right.** `probe_wheels.py` twice reported absence that was not there: first
missing the `py3-none-<platform>` tag shape that ADR-001 already documents for
macOS, then matching the substring `linux` and so accepting a `musllinux`-only
wheel that cannot load on a Debian base. Both corrections are written into the
probe. The lesson is narrow and worth keeping: a probe that reports a
dependency as unavailable is as capable of being wrong as one that reports it
available, and only the second kind of error is usually looked for.

### 13.1 Checkpointer backend seam — COMPLETE

ADR-001's Benefits claim that "swapping model provider, checkpointer backend, or
repository source each touch one module". This is the first change that tests
that claim, and `graph/checkpointer.py` is the module it named. If the change
spreads beyond it, that benefit was overstated and ADR-001 should say so.

**It held.** `graph/checkpointer.py` is the only module whose behaviour
changed; `config.py` gained a setting and `runtime.py` passes it through,
which is the plumbing the claim implies rather than a counterexample to it.

- [x] `graph/checkpointer.py` — `open_checkpointer` becomes a factory. A new
      `UP_CHECKPOINT_URL` selects `AsyncPostgresSaver`; its absence keeps the
      SQLite path, so local development and the hermetic suite are untouched
      (rule 22).
- [x] `config.py` — add the setting with the validation discipline the existing
      path settings already use. `extra="ignore"` means a mistyped `UP_*` var is a
      silent no-op, so a real validator is the only thing that turns a typo into
      an error instead of a default.
- [x] Declare `aiosqlite` in `pyproject.toml`. `checkpointer.py` imports it
      directly but it currently rides in as a transitive of
      `langgraph-checkpoint-sqlite` — the same class of undeclared dependency the
      pyproject comment already flags for `pyyaml`.
- [x] Decide and implement where `AsyncPostgresSaver.setup()` runs. It needs DDL
      rights once; the lifespan and a one-off migration step are both defensible
      and the choice should be stated, not defaulted.
- [x] Tests for both backends behind the same seam, including that an absent
      `UP_CHECKPOINT_URL` still yields SQLite.

**Health-check ripple, recorded because it is not free.** `routes/health.py`
reports `checkpoint_dir` by stat-ing a filesystem path, which says nothing true
about a Postgres DSN. Renaming the field to something honest regenerates
`frontend/src/api/openapi.json` and `schema.d.ts` and touches the health UI.
`_derive_status` iterates the model's own fields, so the status derivation
absorbs a renamed check safely — that was the defect it was written to fix. The
cost here is the regeneration and the frontend edit, not the logic. A clean
`git diff` after `npm run gen:api` is a gate.

**Done, and it was worse than a rename.** The field is now
`checkpoint_ready`, measured as a writable directory on SQLite and as a
configured DSN on Postgres, and the response publishes `checkpoint_backend`
because one boolean cannot say which of those two claims it is. The UI had
been rendering "storage location writable" — a claim about a database
nobody stat-ed, off a green tick. `checkpoint_backend` deliberately does
**not** live in `HealthChecks`: `_derive_status` requires every field there
to be truthy, so a string would join that `all()` as a permanently-true
value and quietly stop being a check.

**One weakness accepted and named:** on Postgres `checkpoint_ready` cannot
go false, so a database that is down still reports `status: "ok"`. The
endpoint opens no connections by design — a probe must not cost money or
inherit third-party latency — so a reachability check belongs behind its
own endpoint rather than smuggled into this one.

### 13.2 Container image — COMPLETE

Built and pushed:
`europe-west1-docker.pkg.dev/<project>/upgradepilot/backend:f581b31` (also
`:latest`), Cloud Build `6fe87aed`, 1m59s.

- [x] `backend/Dockerfile` on a **Debian** `python:3.14-slim` base, two stages.
      Not Alpine, for two independent reasons: musl invalidates every `cp314`
      wheel, and `services/repo/workspace.py` hardcodes
      `PATH=/usr/bin:/bin:/usr/local/bin` for every git subprocess with `env=`
      replacing the whole environment, so git has to be exactly where Debian
      puts it. `apt-get install git` in both stages, since `slim` omits it.
- [x] Serves `0.0.0.0` on the injected port, `--workers 1`, no `--reload`,
      `--factory`. Runs as uid 10001 rather than root.
- [x] **Lockfile resolved for `linux/amd64`** — `requirements.lock`, 112
      distributions, each with a sha256, installed with `--require-hashes` so an
      unpinned transitive cannot slip in. Generated by
      `scripts/lock_linux_deps.py`, which asks pip to resolve *as if* it were
      the target platform (`--dry-run --report`) rather than freezing this
      macOS arm64 venv. Two things that cost a build each and are written into
      the script: `--platform` matches wheel tags by exact string rather than by
      compatibility, so a single `manylinux_2_28_x86_64` silently excluded every
      `manylinux_2_17` wheel and the resolution failed as
      `ResolutionImpossible` naming `pydantic-core`; and the resolution is
      runtime-only, because CI deliberately keeps floating transitives — that is
      how the `langgraph-sdk` drift behind this phase's first red build was
      noticed at all. The image is what ships, so the image is what is pinned.
- [x] **Ingest runs at build time** behind a BuildKit secret (ADR-002 D3):
      `ingested 19 documents as 30 chunks`, and the built image counts 30 —
      matching the local store exactly.
- [x] `UP_CORPUS_DIR` set explicitly, along with every other store path, all
      absolute. All three default to CWD-relative, and `Settings`' `.env` lookup
      is too, so an absolute value is the difference between a stated
      configuration and one that depends on how uvicorn was invoked.
- [x] `.dockerignore` **and** `.gcloudignore`. The second is not a duplicate and
      was not in the original plan: `gcloud builds submit` does not read
      `.dockerignore`, so the first submit uploaded **634 MiB across 20,297
      files**, almost all of it the arm64 `.venv` the image cannot use. With
      `.gcloudignore` it uploads 106 files and 728 KiB.

**`UP_EMBEDDING_MODEL` is baked into the runtime stage, and that is a
correctness constraint rather than a default.** Retrieval compares a query
vector against stored vectors; produce the two with different models and the
similarity numbers are meaningless while everything still returns 200 and cites
documents. The corpus ships inside the image, so the model that built it ships
with it. A deploy that overrides this without rebuilding has silently broken
retrieval and nothing in the product would say so.

**Four verification steps in `cloudbuild.yaml`, because assembling an image
correctly and having it work are different claims.** Each is a real assertion
that fails the build:

1. **No provider key in the image history.** A secret mount is not supposed to
   survive, the failure would be silent, and the artefact is about to be pushed
   to a registry. Reported: `image history carries no provider key`.
2. **The baked collection is non-empty.** `/api/health` answers `ok` over an
   empty collection, so counting is the only way to know. Reported: `corpus
   chunks in image: 30`. Counts through `chromadb` directly, importing
   `COLLECTION_NAME` so a rename cannot leave it counting a collection that no
   longer exists and passing for the wrong reason.
3. **The image actually starts and serves.** The only check that exercises the
   `CMD` — `--factory`, the injected port, binding `0.0.0.0` rather than
   localhost — each a single-token mistake that Cloud Run would surface as an
   opaque startup timeout. Reported:
   `{"checkpoint_backend": "sqlite", "checks": {"checkpoint_ready": true,
   "chroma_dir": true, "llm_configured": false}, "status": "degraded"}`.
4. **It reports its own state honestly with no key.** `degraded`, not a failed
   boot and not `ok` — `open_runtime` keeps a startup failure on the runtime
   object rather than raising into uvicorn, and this pins that behaviour.

**Three build failures worth recording, since each was a real defect in the
first version rather than flakiness.** `${PROJECT_ID}` is not expanded inside a
user substitution's *default value*, so the image reference was rejected
unparsed. `availableSecrets` alone does not grant a step a secret — the step
must also declare `secretEnv`, because `$$LLM_API_KEY` is escaped past Cloud
Build's own view of the script. And ingest failed as "The embedding provider
could not be reached" with an unset base URL, which selects OpenAI direct while
holding an OpenRouter key: the error names reachability and the cause is
configuration.

**Not established:** the image size. `gcloud artifacts` returned 0 bytes for it
and it was not worth another detour, so it is unmeasured rather than estimated.
It matters for cold-start latency under ADR-002 D5 and should be measured before
that default is relied on.

### 13.3 Identity and access — Clerk — COMPLETE except one dashboard setting

The API has no authentication of any kind today, and `POST /api/agent/start`
clones a caller-supplied URL and spends tokens. **Clerk** is the gate (ADR-002
D4), chosen over a shared secret because the next thing planned after deployment
is GitHub sign-in, and a shared secret would be built and then thrown away.

Scope here is **the gate only**. Public repository URLs already work — `RepoInput`
accepts a `url` and `tests/repo/test_clone_live.py` clones a real public
repository over `https`. Clerk adds *who the user is*; it does not by itself add
private repositories. Cloning private repositories with Clerk's stored GitHub
token stays Sub-project 2 work, unblocked rather than done here.

- [x] `@clerk/react` in the frontend, with GitHub as the social connection.
      New dependency, so rule 12 wants the reason stated and rule 13 wants the
      resolved version recorded.
- [ ] **BLOCKED ON A HUMAN. Sign-ups restricted to invitation or an allowed domain in the Clerk
      dashboard.** This is what actually makes the deployment private. Clerk with
      open sign-up is a login page, not an access control.
- [x] `client.ts` attaches the Clerk session token as a bearer header. It is the
      only module in the frontend that calls `fetch`, by design, so this is one
      edit rather than a sweep — the Phase 10 decision paying off.
- [x] **The frontend reads its first environment variable**
      (`VITE_CLERK_PUBLISHABLE_KEY`). It currently reads none, and that property
      is load-bearing in ADR-002 D4's reasoning about why no `VITE_API_BASE_URL`
      is needed. Note the change where it is now false, rather than leaving the
      ADR overstating it.
- [x] `clerk-backend-api` in the backend, verifying the session token via
      `authenticate_request`. Per rule 20 a rejection produces a typed
      `AppError`, which means a new `ErrorCode` member and its own tests.
- [x] Health stays reachable unauthenticated, or a TCP startup probe is used —
      decide which, and say why, rather than discovering it from a failing probe.
- [x] `vercel.json` rewrite for `/api/*` → Cloud Run. Plain rewrite, **not**
      Routing Middleware: Clerk's token comes from the browser, so nothing needs
      injecting server-side. This is a simplification Clerk buys.
- [ ] `UP_CORS_ORIGINS` set to the Vercel production domain (13.4). The rewrite keeps the
      browser same-origin so CORS is never exercised, but ADR-001 is explicit
      that a wildcard is not a decision anyone would make on purpose.

**Run ownership, and why it cannot wait for Sub-project 3.** A shared secret has
no concept of a second user: one trusted proxy, one tenant, and every run
implicitly the caller's. Clerk creates real users, and
`GET /api/agent/status/{thread_id}` and `POST /api/agent/resume` check nothing
about who is asking. **Authentication without ownership is a downgrade** — it
replaces "nobody can get in" with "anyone who is in can read and resume anyone
else's run." So this lands with the gate, not after it (ADR-002 D6).

- [x] A `run_owners` table in the same Cloud SQL instance 13.1 provisions, mapping
      `thread_id` to a Clerk user id, written when a run starts.
- [x] Ownership enforced on `status` and `resume`. A thread owned by someone else
      must be indistinguishable from one that does not exist — a distinct
      "forbidden" response confirms the thread id is real, which is the one thing
      an enumerating caller wants to learn.
- [x] Tests: two users, and neither can see or resume the other's run. This is the
      assertion that would have failed silently before Clerk existed.

**Frontend, done. Three things worth recording.**

`@clerk/react` at `6.15.1`, **not** `@clerk/clerk-react` — the latter is the
name this plan and two of my own messages used, and it is wrong. The bundle
grew 247 kB → 378 kB raw, 75 kB → 109 kB gzipped, which is the cost of the
gate and is worth knowing before ADR-002 D5's cold-start default is relied on.

**`ClerkProvider` is mounted only when a publishable key exists**, so local
development stays ungated and matches a backend that runs open without
`CLERK_SECRET_KEY`. The provider renders a blank sign-in surface when handed
no key, so an unconditional mount would present an unusable screen instead of
an obvious misconfiguration.

**The two keys can disagree**, because they live in different places and
deploy separately: a gated backend behind an ungated build is a real
configuration whose only symptom is every request answering 401 while the app
offers no way to sign in. The health panel now carries a row for exactly that,
reusing `Check` rather than inventing a banner — which is what
`/api/health`'s `auth_required` was added for.

**Two claims of mine that measurement contradicted, corrected in place rather
than quietly.** A code comment asserted that spreading the auth header after
`init.headers` stopped `json()` from dropping the token; reversing the spread
changes nothing, because `content-type` and `authorization` do not collide and
object spread merges them identically either way. The test written to prove
that ordering was therefore vacuous, and now says so instead of implying a
mutation could break it. Separately, `npm install` surfaced a high-severity
`js-yaml` advisory — **not** from Clerk, but from `openapi-typescript`, a dev
dependency; pinned via a nested `overrides` entry exactly as ADR-001 already
does for `undici`, back to zero vulnerabilities.

**What remains is a dashboard setting nobody can commit.** Invitation-only
sign-up is the single control that makes this deployment private rather than
merely authenticated, it lives outside version control and outside CI, and
every other decision in ADR-002 D4 assumes it. Until it is set, the gate is a
login page.

### 13.4 Production configuration

- [ ] `--max-instances=1`, asserted by whatever applies the configuration rather
      than left to an autoscaling default. This is the deployment's load-bearing
      correctness setting and its least visible one: the run registry is
      in-process, so a second instance makes half of all status polls report
      `ORPHANED` and offer to restart running work.
- [ ] `--no-cpu-throttling` (instance-based billing) with `--min-instances=0`.
      The billing mode is a correctness requirement, not a cost tweak — `start`
      returns 202 and the graph runs on a background task, which the default
      request-based billing throttles the instant the response is sent.
- [ ] `UP_ALLOWED_LOCAL_ROOTS` **empty**. The committed `.env.example` ships
      `/Users/nzrsrd/Code`; local-path analysis is meaningless on a server and
      ADR-001 records the setting as an arbitrary-read surface.
- [ ] `UP_WORKSPACE_DIR` on an in-memory volume with an explicit size cap. Writes
      count against the instance memory limit, so budget `UP_MAX_REPO_BYTES`
      (50 MB) × `UP_MAX_CONCURRENT_RUNS` plus depth-100 git history — consider
      lowering concurrency to 2 and sizing memory at 2 GiB. Nothing shared: the
      startup sweep `rmtree`s `repo-*` older than an hour under this directory.
- [ ] Provider key via Secret Manager and `--set-secrets`, never a plain env var,
      **and a hard spend cap set at the provider.** The gate in 13.3 is the only
      thing between a reachable URL and the token budget; the cap is the only
      thing that bounds the damage if the gate fails.
- [ ] Give `RunRegistry.drain()` a bounded timeout. It currently awaits every
      in-flight run with no limit, against a 10-second non-configurable kill —
      an unbounded drain there is a truncated shutdown rather than a deliberate
      one.
- [ ] A deploy runbook in the README: the ingest step, the required settings, and
      the single-instance constraint with its reason.

### 13.5 Exit criteria

Rules 9–11: a phase is done when its exit criteria are demonstrably met, with
output shown. Not one of these is met by the corresponding code existing.

- [ ] **A paused HITL run survives a full Cloud Run revision replacement and
      resumes to `COMPLETED` on the same `thread_id`.** This is the entire
      justification for ADR-002 D2 and must be demonstrated rather than asserted.
- [ ] `/api/health` reports `status: "ok"` **and** a real run returns resolving
      citations. Both halves: the endpoint deliberately never opens the store, so
      it reports `ok` over an empty collection and cannot on its own show that the
      baked corpus is populated.
- [ ] An unauthenticated request to the Cloud Run URL is rejected, shown against
      the deployed service and not against a local test client.
- [ ] **A signed-in user cannot read or resume another user's run**, demonstrated
      with two real Clerk accounts against the deployed service. Not a unit test:
      the failure this guards against is a deployment-shaped one.
- [ ] Sign-up by an uninvited account is refused. Clerk with open registration
      would make every criterion above vacuous.
- [ ] A public GitHub URL, pasted into the deployed UI by a signed-in user, runs
      to a report with resolving citations. This is the capability the
      prioritisation was for, and it is the one that proves the deployment
      earns its place.
- [ ] Two instances are shown to be impossible to reach by configuration, or the
      constraint is shown to be enforced. A correctness setting nobody verified
      is a correctness setting nobody has.

**Exit:** a private URL an invited person signs in to with GitHub, pastes a
public repository link into, is asked and answers one real question on, and
receives a report from — where a redeploy mid-decision does not lose their run,
where they cannot see anyone else's, and where every figure on screen still
traces to a real line of code or a real corpus document.

**What this exit does not claim.** It does not stand in for Phase 11. The
requirements audit, the opt-in live usage test and the golden-set evaluation are
still unchecked, and a deployment demonstrating one journey is not the
whole-surface audit Phase 11 specifies. It also says nothing about private
repositories, which need Sub-project 2. Both are ordinary consequences of going
early and are recorded here so the exit is not read as more than it is.
