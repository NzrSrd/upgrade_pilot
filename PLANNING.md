# PLANNING.md — UpgradePilot

What we are building and in what order. Architecture rationale lives in `docs/adr/ADR-001-system-architecture.md`; the detailed design for the current sub-project is `docs/superpowers/specs/2026-08-24-upgradepilot-agent-core-design.md`.

**Development rule:** complete a phase, demonstrate its exit criteria, update this file, then move on. At each step ask: *what is the smallest implementation that proves this part of the architecture?* Build that, test it, iterate.

---

## Sub-project structure

| | Sub-project | Status |
|---|---|---|
| **1** | **Agent core** — analysis, RAG, risk, HITL, plan, cost, API, UI | **In progress** |
| 2 | Repository sources & GitHub — authenticated clones, private repos, OAuth | Not started |
| 3 | Accounts & history — PostgreSQL, users, saved analyses, Postgres checkpointer and run registry | Not started |

Sub-project 1 contains every graded capability, which is why it comes first. Sub-projects 2 and 3 get their own specs when 1 is complete.

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
- [ ] An `LLMRateLimitedError` / `LLMUnavailableError` taxonomy (Phase 4).
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
- [ ] *[review]* **EARLY Phase 3 work**, ranked ahead of the line above because both feed an honesty channel rather than a grading one: `candidates.py:213` (phase B's `SkippedFile` append, which feeds `skipped_ratio` and therefore the `analysis_coverage` risk factor and its confidence ceiling) and `manifests.py:404-407` (the unreadable-manifest path behind that reducer). A silent failure in either makes the report claim more coverage than it has.
- [ ] *[review]* Mutation survivors that are genuine gaps but produce no wrong claim today: X5 (a model class nested inside another class), U4 (`Optional` inside a nested `Config`), M2 (`pyproject.toml` case-folding), M13 (`old.pyproject.toml` classifying as a manifest), A12 (`AffectedFile.is_test` entirely unbound).
- [ ] *[review, found while resolving `AliasEntry.is_module`]* `_receiver_is_model`'s "a module receiver is excluded by the shape of its origin" argument has one residue, reproduced by execution: `import a.b` binds `a` with origin `a.b`, so if package `a`'s `__init__.py` defines `class b(BaseModel)` then `a.dict()` grades MEDIUM on a receiver that is a module. Contrived — it needs a class named exactly like a sibling submodule — and the cost is a confidence grade, never a wrong citation. The fix consumes `AliasEntry.is_module` through a new `AliasMap` accessor (`origin_of` returns a string and loses which entry won), and adopting it reopens ruling 1279's deliberate decision not to special-case a module receiver. Not taken unilaterally for that reason.
- [ ] `broken.py`'s fixture comment names the dependency ("`# pydantic: ...`"), which is structurally the same accident phase B exists to close for `service.py` — acceptable here only because `broken.py` fails loudly (a test goes red immediately) if that comment is removed, unlike `service.py`'s docstring, which failed silently. Worth a real first-party consumer fixture that reaches `broken.py` only through phase B, the way `consumer.py` does for `service.py`, if the distinction ever needs to be demonstrated rather than merely argued.
- [ ] *[final fix round 2, finding 3]* `models_index.build_model_index`'s `dotted_targets` set is keyed on `dotted_module`, which is not injective (`src/app/models.py` and `app/models.py` both give `app.models`), so a transitive base or a first-party import naming that dotted path genuinely cannot say which of the colliding files it means. `models_index.colliding_dotted_modules` now detects the collision and `analyze_repository` reports it as a confidence reducer, but the ambiguity itself is deliberately NOT resolved by this fix — only recorded. Choosing between indexing both colliding files, indexing neither, or capping the confidence of an attribution that lands in a collision is a design decision with real trade-offs in both directions (indexing both over-reports; indexing neither under-reports; capping confidence changes the grading scheme), and belongs to Phase 3, with the reducer already in place to make the problem visible in the meantime.

## Phase 3 — Knowledge base

- [ ] Document metadata schema and frontmatter format
- [ ] Corpus authored one breaking change per document — real Pydantic v1→v2 primary sources, plus a small number of authored ADRs and upgrade reports representing internal engineering guidance
- [ ] Ingestion: parse frontmatter, chunk, embed, persist — scalar metadata for the coarse fields, `affected_symbols` as a real list
- [ ] `affected_symbols` stored as a **list-valued** metadata field, filtered in the database with `$contains` (exact-element) and never with `$in`
- [ ] Retrieval with scalar metadata filters for coarse narrowing, plus the `$contains` symbol join
- [ ] Symbol coverage annotation over retrieved candidates, and the deterministic sufficiency gate
- [ ] Source metadata returned with every result
- [ ] Deterministic fake embedding function for tests
- [ ] Golden evaluation set (~15 cases) with recall@5 and MRR floors asserted in CI
- [ ] Tests: retrieval, scalar filtering, `$contains` symbol filtering including the negative direction (a prefix-colliding symbol must not match), source metadata fidelity, Chroma-unavailable handling

**Exit:** a migration question returns relevant evidence with source metadata that resolves, and the golden set meets its floors.

## Phase 4 — Graph foundation

- [ ] `MigrationState` with reducers, including `merge_sources_by_id`
- [ ] `TrackedLLM` service: usage extraction with fallback, tiktoken estimation path, `include_raw=True`
- [ ] `MODEL_PRICING` in settings; unknown model yields `cost = None`
- [ ] `UsageSummary` derivation as a pure function
- [ ] `TraceEvent` emission helper
- [ ] `AsyncSqliteSaver` wired
- [ ] Skeleton nodes wired end to end with stub logic
- [ ] Scripted fake chat model for tests
- [ ] Tests: reducers, usage aggregation across a simulated resume with no double-count, unknown-model pricing, thread isolation

**Exit:** a graph executes start to finish over stubs with checkpointed state, and usage aggregation is proven idempotent.

## Phase 5 — Agentic RAG subgraph

- [ ] `RAGState` and shared channel mapping
- [ ] `plan_retrieval` — query generation from the symbol inventory; retrieval-necessary decision
- [ ] `retrieve` — filtered search, symbol annotation, dedup
- [ ] `evaluate_retrieval` — LLM coverage grading
- [ ] Deterministic sufficiency gate overriding the model
- [ ] Conditional edge with `MAX_RAG_ITERATIONS` bound
- [ ] `build_context` — `BreakingChange` construction with mandatory sources; uncovered symbols to `unknowns`
- [ ] Explicit wrapper node with subgraph failure handling
- [ ] Tests: refinement path with two query rounds, iteration cutoff, gate overriding a falsely-sufficient model, `KB_UNAVAILABLE` degradation

**Exit:** the agent performs multiple retrieval iterations when the first result set is insufficient, and can name which sources informed the outcome.

## Phase 6 — Risk assessment

- [ ] Mechanical factor extraction for all seven factors
- [ ] Documented threshold table
- [ ] LLM narrative synthesis over a fixed factor set
- [ ] `overall_risk` clamp against confirmed breaking-change severity
- [ ] Confidence ceilings: no evidence, skipped files, transitive-only, uncovered symbols
- [ ] Tests: each factor, each threshold boundary, each clamp and ceiling

**Exit:** risk output is traceable to repository and corpus evidence, and no clamp or ceiling can be bypassed by the model.

## Phase 7 — Human-in-the-loop

- [ ] Strategy enumeration and scoring
- [ ] Interrupt predicate: ≥2 viable strategies differing on an axis constraints do not settle
- [ ] Four typed decision kinds
- [ ] `InterruptPayload` with reason, evidence, options, tradeoffs, consequences
- [ ] `interrupt()` call and `Command(resume=...)` handling
- [ ] Resume-payload validation; re-interrupt on unknown option
- [ ] Tests: interrupt fires, checkpoint persists, resume continues the same thread, no-interrupt when constraints decide, invalid decision rejected, multiple sequential interrupts

**Exit:** a genuine tradeoff pauses the graph with enough context to decide; a settled question does not pause it at all.

## Phase 8 — Plan generation and validation

- [ ] `generate_plan` with per-step evidence requirements
- [ ] `human_decisions_applied` with `how_it_changed_the_plan`
- [ ] All ten deterministic validation checks
- [ ] Bounded single repair retry
- [ ] `COMPLETED_WITH_WARNINGS` terminal path
- [ ] `finalize` as a pure function
- [ ] Tests: every check with a passing and failing case, the repair retry, and the decision-flip test asserting a different `strategy_id`

**Exit:** plans are validated for real, failures are visible, and the human decision demonstrably changes the output.

## Phase 9 — API layer

- [ ] Pydantic request and response models; `RunSnapshot` as the single response shape
- [ ] `POST /api/agent/start` returning 202
- [ ] `GET /api/agent/status/{thread_id}` deriving status from checkpoint plus registry
- [ ] `POST /api/agent/resume` with 409 / 404 / 422 behaviour
- [ ] `RunRegistry` with concurrency semaphore and `QUEUED` state
- [ ] `ORPHANED` detection and resume-from-checkpoint
- [ ] Centralized error handler over the full taxonomy
- [ ] CORS from settings
- [ ] Tests: every status code, schema assertions, orphan detection

**Exit:** all backend functionality is reachable through the documented contract, verified manually before frontend work begins.

## Phase 10 — Frontend

- [ ] `openapi-typescript` type generation from the live schema
- [ ] `useRunPolling` hook with backoff, terminal-stop, unmount abort
- [ ] Status-derived view routing
- [ ] Configuration form with inline 422 rendering
- [ ] Activity timeline with expandable steps
- [ ] Evidence panel with relevance and source references
- [ ] Human Review panel over a still-incomplete timeline; triple duplicate-submit guard
- [ ] Report view: risk, confidence, affected files, breaking changes, evidence, plan, mitigations, decisions
- [ ] Persistent metrics sidebar including estimated and pricing-unknown flags
- [ ] Agent Trace drawer — observable events only
- [ ] Error and orphan views with retry / resume
- [ ] Semantic Tailwind color tokens; `aria-live` on status
- [ ] Tests: polling hook with MSW, Human Review panel behaviour

**Exit:** the full journey is usable in the browser and the workflow never appears complete while waiting for input.

## Phase 11 — End-to-end and requirements audit

- [ ] E2E HITL test: start → `AWAITING_HUMAN` → resume → `COMPLETED`, asserting resolving evidence, non-zero usage, applied decision
- [ ] Opt-in live test asserting real `usage_metadata`
- [ ] CI: pytest, ruff, mypy, vitest, tsc
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
