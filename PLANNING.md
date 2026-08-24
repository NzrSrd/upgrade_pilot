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

- [ ] `SymbolInventory`/`AffectedFile` constraints that need a real consumer:
      PEP 503 normalisation of `DependencySpec.name` (it is an exact-match corpus
      key, so normalisation changes retrieval), and `commit_count=0` currently
      conflating "unknown" with "no churn".
- [ ] `RiskCategory` member names have drifted from spec §8.1, and three of them
      overstate their scope. Rename with the analyzer, so the names describe what
      is actually computed.
- [ ] Citation paths accept absolute and `..` forms; `EvidenceRef` should require
      a repo-relative path once the analyzer is the only producer.
- [ ] `RepoAnalysis.languages` is bounded but still mutable in place, and is
      unspecified in the spec. Fixing the mutability is a shape change.
- [ ] Naive vs aware datetimes across the models.
- [ ] The fixture's expectation tuples bind **one way**: every listed symbol must
      exist, but nothing catches someone *shortening* a tuple, which would
      silently narrow the documented claim while the suite stayed green. The
      analyzer's own test must assert its findings **equal** those tuples exactly,
      which closes both directions.
- [ ] The analyzer must detect `.gitmodules` and surface "submodule content not
      analysed" as an explicit confidence reducer. `git clone` does not fetch
      submodules, so a repository whose real code lives in them would otherwise
      analyse as nearly empty and report low risk having never seen the code.
- [ ] Bring `tests` under mypy. Measured cost: 130 errors across 13 files, 8 of
      them inside `tests/fixtures/sample_repo/`, whose contents must never be
      "fixed" — so the fixture needs excluding first. See spec §11.

**Deferred to the phase that owns the surface:**

- [ ] Wire `sweep_stale` into a FastAPI lifespan (Phase 9). It is implemented and
      tested but has no caller; the startup-only contract is in its docstring.
- [ ] `sweep_stale` does not guard the workspace root itself vanishing between
      `exists()` and `iterdir()`. Individual entries are guarded.
- [ ] An `LLMRateLimitedError` / `LLMUnavailableError` taxonomy (Phase 4).
- [ ] `api/app.py` calls `create_app()` at import time.
- [ ] `_NON_INTERACTIVE_GIT_ENV` hardcodes `PATH` to `/usr/bin:/bin:/usr/local/bin`
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


- [ ] Manifest detection across `pyproject.toml`, `requirements*.txt`, `poetry.lock`, `uv.lock`, `Pipfile.lock`
- [ ] Version detection with precedence and confidence label; `DependencyNotFound` when absent
- [ ] `DependencyRole` direct vs transitive-only
- [ ] Stated-versus-detected discrepancy detection
- [ ] Byte-substring candidate prefilter, then `ast.parse`
- [ ] Alias map from `Import` / `ImportFrom`
- [ ] Usage detection with confidence tiers per spec §7.1
- [ ] `SkippedFile` records for unparseable files
- [ ] Churn from a single `git log --name-only` call
- [ ] Test location detection
- [ ] Language mix for `RepoAnalysis.languages` by counting file extensions over the workspace — the field exists and defaults to `{}`, so without this it stays empty and any UI reading it shows nothing. A sixth item in Phase 1's scope note ("detect repository languages") deferred here without a corresponding line; this is that line
- [ ] `SymbolInventory` and `AffectedFile` assembly
- [ ] Tests: fixture files for every usage kind, an unparseable file, every manifest type

**Exit:** given the fixture repository and `pydantic`, the analyzer returns structured evidence with real file/line usage sites and honest confidence labels.

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
