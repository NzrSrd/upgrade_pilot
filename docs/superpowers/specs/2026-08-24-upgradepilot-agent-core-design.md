# UpgradePilot — Agent Core Design (Spec 1)

- **Date:** 2026-08-24
- **Status:** Approved for planning
- **Scope:** Sub-project 1 of 3 — the agent core
- **Supersedes:** the storage, repository-access, and API-contract portions of the original brief where they conflict (see *Corrections to the original brief*)

---

## 1. Problem

A developer facing a dependency upgrade has one question: *"What will break, how risky is it, and what should I do about it?"* Answering it today means reading a migration guide, grepping the codebase, guessing at blast radius, and making a judgement call about strategy with no written record of why.

UpgradePilot takes a repository and a version change and produces an evidence-backed migration plan: which files are affected and at which lines, which documented breaking changes those usages actually collide with, how confident the assessment is and why, and a step-by-step strategy that reflects the developer's own constraints.

The governing constraint on the whole design: **every claim must trace to either a real line of code or a real corpus document.** Where that is not possible, the system says so rather than filling the gap with plausible prose.

## 2. Scope boundary

The full product decomposes into three sub-projects. This spec covers **Sub-project 1 only**.

| | Sub-project | Contents |
|---|---|---|
| **1** | **Agent core (this spec)** | Repository analysis, ChromaDB knowledge base, agentic RAG subgraph, risk assessment, HITL interrupt/resume, plan generation and deterministic validation, token/cost tracking, FastAPI layer, four React views, full test suite. Repository sources: shallow clone of a public URL, or a local path. No authentication, no accounts, no persisted history. |
| **2** | Repository sources & GitHub | Authenticated clones, private repositories, GitHub OAuth, credential handling, GitHub-API-sourced metadata. |
| **3** | Accounts & history | PostgreSQL, UpgradePilot user accounts, analysis history and replay, Postgres-backed checkpointer, Postgres-backed run registry (which lifts Spec 1's single-worker constraint). |

Every capability being graded lands in Sub-project 1, which is why it is built and proven first.

## 3. Decisions locked during design

| # | Decision | Rationale |
|---|---|---|
| 1 | Full product eventually includes history, accounts, and GitHub OAuth | Product owner's call; sequenced into Sub-projects 2 and 3 |
| 2 | Decompose into three specs, agent core first | Retires the novel LangGraph/RAG risk before auth plumbing |
| 3 | Demo and build target is **Pydantic v1 → v2** against a real public repository | A genuine major-version migration with extensive real primary-source documentation. Corpus is real; citations resolve. |
| 4 | Deterministic evidence, LLM judgment | AST supplies file/line facts; the LLM supplies queries, relevance judgement, and prose. File and line claims cannot be hallucinated. |
| 5 | Repository ingestion: shallow clone of public URL **or** local path, both resolving to a working tree | One shape for the analyzer, no credentials needed, and `git log` comes free for churn signals |
| 6 | Agentic RAG loop is a **compiled LangGraph subgraph** | Clean encapsulation; the cost is parent-graph trace visibility, mitigated by an explicit wrapper node and shared trace channel |
| 7 | No curated symbol → breaking-change index | A hand-written mapping would make retrieval decorative. The join is evidence-driven. |
| 8 | Progress transport: background execution + `GET /status` polling | Genuinely live, trivially testable, and makes the checkpointer load-bearing beyond HITL |

## 4. Corrections to the original brief

The original brief contained three internal contradictions and one architectural gap. Resolutions:

1. **Storage.** The brief described storage as checkpointer + ChromaDB, then later introduced PostgreSQL for history and OAuth tokens. Resolved: three stores with three distinct jobs, PostgreSQL deferred to Sub-project 3.
2. **Repository access.** "Enter a public GitHub repository" versus "start local-only." Resolved: both, via one `RepositorySource` abstraction resolving to a working tree.
3. **API contract versus UI.** A blocking `POST /start` cannot drive a live step-by-step activity timeline. Resolved: `202` + background task + status polling.
4. **Metadata filtering.** An early design draft assumed `where={"affected_symbols": {"$in": [...]}}`; a later revision of this document asserted that ChromaDB metadata values must be scalars and that `$contains` applies only to document text. Both were wrong: probing the pinned `chromadb==1.5.9` showed list-valued metadata is accepted and `$contains` matches its elements exactly, while `$in` silently returns nothing. Resolved in §7.2 — list-valued `affected_symbols` filtered with `$contains`, never `$in`.

---

## 5. System shape

Four layers with one-way dependencies. `services/` never imports LangGraph; `graph/` never imports FastAPI. The analyzer and knowledge base are therefore testable with no graph and no HTTP, which is where most of the test value lives.

```
api/  →  graph/  →  services/  →  models/
```

```
upgradepilot/
├── CLAUDE.md  PLANNING.md  README.md
├── docs/
│   ├── adr/ADR-001-system-architecture.md
│   └── superpowers/specs/
├── backend/
│   ├── src/upgradepilot/
│   │   ├── api/          routes.py  schemas.py  errors.py
│   │   ├── graph/        state.py  build.py  nodes/  rag_subgraph/
│   │   ├── services/     repo/  knowledge/  usage/  registry.py
│   │   ├── models/       domain types, no I/O
│   │   └── config.py     pydantic-settings
│   ├── corpus/           source markdown for ingestion
│   ├── tests/            unit/  knowledge/  graph/  api/  e2e/  fixtures/
│   └── pyproject.toml
└── frontend/             Vite + React + TS + Tailwind
```

### 5.1 Three stores, three jobs

- **ChromaDB** — semantic knowledge. Migration docs, changelogs, ADRs. Persistent local directory.
- **LangGraph checkpointer** — thread/run state. Enables interrupt/resume *and* serves `GET /status`. `AsyncSqliteSaver` from `langgraph-checkpoint-sqlite`: durable across restarts, no server process. Sub-project 3 swaps in the Postgres saver.
- **PostgreSQL** — not present in Spec 1.

### 5.2 Version discipline

Phase 0 installs, resolves, and *verifies* the dependency set before any application code exists. The probes and their results are recorded in ADR-001. Specifically verified rather than assumed:

- which usage-metadata surface is populated on the resolved `langchain-core`
- whether `with_structured_output(..., include_raw=True)` preserves usage metadata
- `AsyncSqliteSaver` interrupt/resume round-trip on the resolved `langgraph`
- ChromaDB persistence, and the semantics of scalar *and* list-valued metadata filtering

### 5.3 Configuration

`pydantic-settings`, `.env` plus a committed `.env.example`. No secrets in git. Notable settings: `ALLOWED_LOCAL_ROOTS` (governing local-path refs **and** `file://` clone URLs), `MAX_REPO_FILES`, `MAX_REPO_BYTES`, `CLONE_DEPTH` (default 100), `MAX_RAG_ITERATIONS`, `MAX_CONCURRENT_RUNS` (default 4), `MODEL_PRICING`.

---

## 6. State and data contracts

`MigrationState` is a `TypedDict` with `Annotated` reducers — LangGraph's channel model requires it — while every value inside is a Pydantic model. Validation where data lives, reducers where merging happens.

```python
class MigrationState(TypedDict):
    # inputs — set once
    thread_id: str
    repo_ref: RepoRef                 # url-or-path + resolved commit sha
    dependency: DependencySpec        # name, current, target
    constraints: UserConstraints      # typed, not a free-form dict

    # evidence
    repo_analysis: RepoAnalysis | None
    affected_files: list[AffectedFile]
    symbol_inventory: SymbolInventory | None
    breaking_changes: list[BreakingChange]
    rag_context: RagContext | None

    # append-only channels
    rag_queries:       Annotated[list[RagQuery], operator.add]
    rag_evaluations:   Annotated[list[RagEvaluation], operator.add]
    retrieved_sources: Annotated[list[SourceRef], merge_sources_by_id]
    llm_calls:         Annotated[list[LLMCall], operator.add]
    agent_trace:       Annotated[list[TraceEvent], operator.add]
    errors:            Annotated[list[AppError], operator.add]

    # judgment
    risk_analysis: RiskAnalysis | None
    pending_decision: InterruptPayload | None
    human_decisions: Annotated[list[HumanDecision], operator.add]
    migration_plan: MigrationPlan | None
    validation: ValidationReport | None
```

### 6.1 Two deliberate departures from the brief's state list

**`token_usage` and `estimated_cost` are not stored.** They are derived by a pure function over `llm_calls`. A stored running total double-counts the moment a node re-executes after resume — LangGraph replays the interrupted node, and an incrementing counter silently inflates. Append-only records keyed by `call_id` make aggregation idempotent and unit-testable with no graph involved.

**`retrieved_sources` uses a custom reducer** merging by `source_id` and keeping the highest relevance. The brief's "avoid duplicate sources where possible" becomes structural rather than a rule each call site must remember.

### 6.2 Honesty as a type invariant

- `BreakingChange.source: SourceRef` — required, non-optional.
- `RiskFactor.evidence: list[EvidenceRef]` — `min_length=1`.
- `EvidenceRef` is a discriminated union of `RepoEvidence(file, line)` and `DocEvidence(source_id, chunk_id)`.

A breaking change or risk factor citing nothing is **unconstructable**. "Do not fabricate sources" stops being a prompt instruction the model may ignore and becomes a `ValidationError`.

### 6.3 Domain models

`RepoRef`, `Workspace`, `Manifest`, `DetectedVersion`, `DependencyRole`, `RepoAnalysis`, `UsageSite`, `AffectedFile`, `SkippedFile`, `SymbolInventory`, `SourceRef`, `BreakingChange`, `RagQuery`, `RagEvaluation`, `RagContext`, `EvidenceRef`, `RiskFactor`, `RiskAnalysis`, `DecisionOption`, `InterruptPayload`, `HumanDecision`, `DecisionApplication`, `MigrationStep`, `MigrationPlan`, `ValidationReport`, `LLMCall`, `UsageSummary`, `TraceEvent`, `AppError`, `RunSnapshot`, `FinalReport`.

### 6.4 Subgraph state mapping

`RAGState` is its own `TypedDict`: child-only loop fields (`iteration`, `candidates`, `max_iterations`, `uncovered_symbols`) plus the shared `agent_trace`, `llm_calls`, `rag_queries`, and `rag_evaluations` channels under identical names and reducers.

The parent's `agentic_rag` node is an **explicit wrapper** rather than a bare compiled-graph node: it maps parent → `RAGState`, invokes the subgraph, maps outputs back, and merges the trace. The wrapper is also where subgraph failure is caught and converted into an `AppError` instead of killing the run.

### 6.5 Status is derived, never stored

`RunStatus` is computed from the checkpoint plus the run registry (§9.2). A stored status field would drift from reality on crash.

---

## 7. Evidence layer

### 7.1 Repository access and analysis

`RepoRef` resolves to a `Workspace` — the only thing the analyzer ever sees. Two resolvers:

- **Shallow clone** — `git clone --depth 100 --single-branch` into a temp workspace. Depth 100 rather than 1, because churn signals need history.
- **Local path** — used read-only in place, no copy.

`Workspace` exposes `files(pattern)`, `read_text(path)`, `git_log(paths, limit)`, `commit_sha`, `cleanup()`.

**Guards**, because accepting a URL or filesystem path is an arbitrary-read surface:

- URL scheme allowlist (`https`, `git`); credentials-in-URL rejected
- local paths must resolve under a configured `ALLOWED_LOCAL_ROOTS` — and so must the path in a `file://` clone URL. The setting governs **both** doors, not just the local-path ref: git treats a `file://` URL as a local-disk read, ignoring the URL's host entirely, so confining only the local-path ref would leave the allowlist bypassable by spelling the same path as a URL.
- the two forms differ on one point, and the difference is deliberate: a `file://` **URL** containing a space must percent-encode it as `%20`, because an unencoded space is invalid per RFC 3986 and the URL guard enforces that, while a `LocalRepoRef` **path** still accepts a raw space. This matters more than it sounds: `/Users/me/My Documents/repo` is an ordinary macOS path, so the local-path form is the one to reach for when a path has spaces in it. Note also that git percent-*decodes* the path it opens, so `file://.../a%20b` reads the directory `a b` — the guard and git agree on the decoded path, not the raw text.
- symlinks resolving outside the workspace root are skipped
- hard caps on file count and total bytes, enforced before analysis begins
- temp workspaces cleaned on run completion and on startup sweep

**Candidate file selection** uses a cheap byte-substring scan for the dependency name over `.py` files, then `ast.parse` only on hits. Stdlib only — no external binary, and a 5,000-file repository is never fully parsed.

**Version detection** has a precedence order and a confidence label, because a lockfile pin and a `^1.10` specifier are not the same fact:

| Source | Confidence |
|---|---|
| `poetry.lock` / `uv.lock` / `Pipfile.lock` / `requirements*.txt` with `==` | `exact` |
| `pyproject.toml` specifier (`[project.dependencies]`, `[tool.poetry.dependencies]`) | `range` |
| `requirements*.txt` with a range | `range` |
| absent | `DependencyNotFound` error — never a guess |

Two consequences the brief did not anticipate:

- **Discrepancy handling.** If the detected version contradicts the user's stated `current_version`, that is surfaced as a prominent warning carried into the final report, and may raise a `DISCREPANCY_RESOLUTION` decision. It is never silently overridden in either direction.
- **`DependencyRole: DIRECT | TRANSITIVE_ONLY`.** Pydantic is frequently pulled in by FastAPI rather than declared. "You do not directly control this pin" materially changes the migration story and caps confidence.

**AST analysis** builds an alias map from `Import` / `ImportFrom`, then walks for usage, labelling every finding with confidence:

| Pattern | Confidence |
|---|---|
| `class X(BaseModel)` — model definition | high |
| `@validator` / `@root_validator` decorator | high |
| nested `class Config:` inside a model | high |
| `Optional[T]` field with no default inside a model | high |
| `.dict()`, `.json()`, `.parse_obj()`, `.copy()`, `.schema()` in a module that imports the dependency and defines models | medium |
| the same calls elsewhere | low |

**Symbol confidence is derived from its sites.** A *symbol* is high-confidence if at least one of its usage sites is high-confidence; medium if its best site is medium; low otherwise. This matters because the sufficiency gate (§7.3) and `evidence_coverage` (§8.1) are both defined over high-confidence symbols, and a symbol commonly has sites at mixed confidence.

The medium/low tiers exist because those are generic method names; pretending otherwise would manufacture false findings. Unparseable files become counted `SkippedFile` records that appear in the report and cap confidence — silently dropping them would make coverage look better than it is.

Churn comes from a single `git log --name-only -n 100` call, not one call per file.

Test locations are detected by path convention (`tests/`, `test_*.py`, `*_test.py`) and mapped to affected files where a correspondence is findable.

### 7.2 Knowledge base

The corpus is authored **one document per breaking change**. YAML frontmatter becomes metadata:

```yaml
source_id: pydantic-v2-migration#validator-renamed
title: "@validator replaced by @field_validator"
source_type: migration_guide      # | changelog | adr | upgrade_report | compat_note
dependency: pydantic
from_version: "1.x"
to_version: "2.0"
to_version_major: 2
affected_symbols: [validator, root_validator]
severity: high
url_or_reference: https://docs.pydantic.dev/latest/migration/
created_at: 2026-08-24
tags: [validators, api-rename]
```

Body carries what changed, the old form, the new form, and a migration note.

**ChromaDB metadata: corrected.** An earlier version of this section claimed Chroma metadata values must be scalars (`str`, `int`, `float`, `bool`), that a list-valued `affected_symbols` is therefore not filterable, and that `$contains` applies to document text rather than metadata. That claim was wrong and this replaces it. Probed directly against the pinned `chromadb==1.5.9` (`backend/tests/knowledge/test_chroma_contract.py`, `backend/probes/probe_chroma.py`):

- **Scalar metadata filters** (`dependency`, `source_type`, `to_version_major`) are used for coarse narrowing, as before.
- **`affected_symbols` is stored as a real list**, not a delimited string, and is filtered with `$contains` (`$or` across a `where` clause for several symbols at once) — so the symbol join is performed by retrieval in the database, not by a Python post-pass over returned candidates.
- **`$contains` is exact-element, not substring**, which is what makes filtering on it safe: a filter for `Config` does not match a document tagged `ConfigDict`, and a filter for `valid` does not match `validator`.
- **`$in` does not work against list-valued metadata** — it returns an empty result rather than erroring — and must not be used; `$contains` is the only operator that behaves correctly here.

Nothing hardcodes `validator → field_validator`.

**Embeddings:** `text-embedding-3-small`. Embedding calls are cheap but non-zero and enter the cost table separately from chat calls — otherwise "estimated cost" is simply wrong. Tests use a deterministic fake embedding function; unit tests touch no network.

**Golden evaluation set:** roughly 15 query → expected-`source_id` pairs, checked in and asserted in CI against recall@5 and MRR floors. This is the "RAG evaluation is documented" requirement as an executable test rather than a paragraph.

### 7.3 The RAG subgraph

```
plan_retrieval → retrieve → evaluate_retrieval → [sufficient?]
      ↑                                            ├─ no  → plan_retrieval (iter+1)
      └────────────────────────────────────────────┘
                                                   └─ yes → build_context
```

- **`plan_retrieval`** — the LLM sees the dependency, both versions, the *actual* symbol inventory, and on later passes the prior evaluation's `missing_topics`. It emits queries with scalar filters and a one-line observable rationale. It also decides whether retrieval is warranted at all: zero usage sites means skip, recorded as an explicit decision rather than a silent no-op.
- **`retrieve`** — per query: filtered similarity search, symbol-overlap annotation, dedup by `chunk_id`, append to `retrieved_sources`.
- **`evaluate_retrieval`** — the LLM grades coverage per symbol, then **a deterministic gate overrides it**: if any *high-confidence* symbol has zero candidate chunk mentioning it, `sufficient = False` regardless of what the model said. The model cannot declare victory over a mechanical gap.
- **`build_context`** — constructs `BreakingChange` objects, each with its mandatory `SourceRef`. Symbols with no supporting evidence go to `unknowns`, never into `breaking_changes`.

Loop bound: `MAX_RAG_ITERATIONS` (default 3). Chroma unreachable → `AppError(KB_UNAVAILABLE)` and an empty context flagged `evidence_available: False`, which §8.1 turns into a hard confidence ceiling. Absent evidence must not be able to produce a confident answer.

---

## 8. Judgment layer

### 8.1 `assess_risk` — factors computed, narrative generated, decision prepared

Risk factors are extracted mechanically, each carrying evidence refs. The LLM does not assign factor levels; it synthesizes prose over a factor set it cannot invent.

`assess_risk` also owns strategy enumeration: it scores the candidate strategies and builds the complete `InterruptPayload` into `pending_decision`, so that `human_review` can be a thin interrupt-only node. That division is required rather than stylistic — see §8.2 and ADR-001 on pre-interrupt side effects being billed twice.

| Factor | Derived from |
|---|---|
| `breaking_change_exposure` | high-confidence usage sites matched to retrieved `BreakingChange`s, weighted by documented severity |
| `blast_radius` | affected files ÷ total analyzed Python files |
| `test_coverage_of_affected` | fraction of affected files with a locatable corresponding test |
| `churn_on_affected` | recent commit activity on affected paths |
| `analysis_coverage` | skipped/unparseable files, share of low-confidence sites |
| `evidence_coverage` | fraction of high-confidence symbols with a documented change |
| `constraint_pressure` | zero-downtime, deadline, effort from `UserConstraints` |

Levels come from a documented threshold table, making each level reproducible and unit-testable without an LLM.

**Deterministic clamps then override the model:**

- `overall_risk` cannot be set *below* the maximum severity among confirmed high-confidence breaking-change exposures. The model cannot downplay a documented break the AST proved is in use.
- `confidence` ceilings apply when: `evidence_available == False` (≤ 0.3); skipped files exceed 10%; `DependencyRole` is `TRANSITIVE_ONLY`; any high-confidence symbol lacks documented evidence.

The LLM may attach `qualitative_notes`, which carry no weight in the level. A confident answer with no evidence behind it is structurally unreachable.

### 8.2 `human_review` — the interrupt fires on a predicate

Candidate strategies (compatibility layer / staged rollout / direct
migration) are enumerated, scored, and turned into a complete
`InterruptPayload` **by `assess_risk`**, which stores it in
`pending_decision`. `human_review` is deliberately thin: it reads
`pending_decision`, calls `interrupt()`, and validates the returned
decision. It performs no LLM call.

This division is required, not stylistic. A node that interrupts
re-executes from the top on resume, so any LLM call placed before its
`interrupt()` would be billed twice while only one usage record
survives — recorded cost would understate real spend. See ADR-001.

**The graph interrupts only when ≥2 strategies remain viable *and* they differ on an axis the stated constraints do not already settle.** If constraints decide it, no interrupt occurs: a trace event records "resolved by constraints, no human input required" and flow continues to `generate_plan`. This is the brief's conditional edge, and it is what prevents degeneration into a ceremonial dialog.

Four typed decision kinds, no free-form questions:

1. `STRATEGY_CHOICE` — compatibility layer versus direct migration
2. `RISK_ACCEPTANCE` — high-severity finding on thin evidence: blocking, or proceed with mitigation?
3. `SCOPE_TRADEOFF` — deadline versus full migration
4. `DISCREPANCY_RESOLUTION` — detected version ≠ stated version, or transitive-only pin

`InterruptPayload` carries `question_id`, `kind`, `reason`, `question`, `evidence: list[EvidenceRef]`, `options: list[DecisionOption]`, `recommendation_id | None`, and `consequences_if_unanswered`. Each `DecisionOption` has `id`, `label`, `summary`, `risk_level`, `effort`, `downtime`, `consequences`, and `supporting_evidence`.

**Resume input is untrusted.** `interrupt()` returns whatever HTTP handed it, so the node validates against `HumanDecision` and re-interrupts on an unknown `selected_option_id` rather than proceeding with garbage. `human_decisions` being an append channel means multiple sequential interrupts work naturally.

### 8.3 `generate_plan`

Every `MigrationStep` must reference an affected file or carry `rationale_evidence`; any file a step names must exist in the workspace.

The human's influence is made structural: `MigrationPlan.human_decisions_applied: list[DecisionApplication{decision_id, how_it_changed_the_plan}]`. A test asserts that resuming the same checkpoint with the opposite option yields a different `strategy_id` — which is how "human decision affects downstream generation" gets *verified* rather than claimed.

### 8.4 `validate_plan` — no LLM, and a real gate

1. Every `SourceRef` resolves in Chroma.
2. Every `RepoEvidence` file exists and the line is within range.
3. Every file named in a plan step exists in the workspace.
4. Every `RiskFactor` has at least one resolving evidence ref.
5. `overall_risk` ≥ max confirmed breaking-change severity (the clamp holds).
6. `confidence` respects all applicable ceilings.
7. Plan is non-empty and steps are contiguously ordered.
8. Every high-confidence affected file is addressed by a step, or appears in `unaddressed_with_reason`.
9. A human decision existing implies `human_decisions_applied` is non-empty.
10. Zero-downtime constraint implies no step marked `requires_downtime`.

On failure: one bounded retry into `generate_plan` with the failed checks as repair input. Still failing → `finalize` with `COMPLETED_WITH_WARNINGS` and the failures shown in the report. Never silently passes; never loops forever.

`finalize` is a pure function: assemble `FinalReport`, derive `UsageSummary` from `llm_calls`, stamp the commit sha.

### 8.5 Topology

```
START → analyze_repo → inspect_dependency → agentic_rag ⟨subgraph⟩ → assess_risk
                                                                          │
                                              ┌───────────────────────────┴─┐
                                       decision needed?                     │
                                         yes │                              │ no
                                    human_review ─interrupt/resume─┐        │
                                                                    ▼        ▼
                                                             generate_plan ◄─┘
                                                                    │
                                                             validate_plan
                                                          passed │      │ failed (≤1 retry)
                                                                 ▼      └──► generate_plan
                                                             finalize → END
```

---

## 9. API layer and run lifecycle

### 9.1 Endpoints

| Endpoint | Returns |
|---|---|
| `GET /api/health` | `{status, version, checks: {chroma_dir, checkpoint_dir, llm_configured}}`, where `status` is `"ok"` when every check is true and `"degraded"` otherwise — **derived** from `checks`, never asserted alongside them. Checks configuration and local stores; does **not** call the model provider — a health probe should not cost money or inherit its latency. The field names were `chroma`/`checkpointer` in an earlier version of this table; they are `chroma_dir`/`checkpoint_dir` to match the implementation, and the spec was the side that changed. `chroma`/`checkpointer` implied the *stores* had been probed, whereas what is actually checked — deliberately, so the probe stays free and local — is whether each store's **directory** exists and is writable, or could be created. The narrower name is the one that does not overclaim. The key check was `openai_configured` until 2026-08-25; it is `llm_configured` now that the provider is configuration rather than an assumption (§12 assumption 4). |
| `POST /api/agent/start` | **202** `{thread_id, status: "running", poll_url}` |
| `GET /api/agent/status/{thread_id}` | `RunSnapshot` — status, current step, completed steps, trace, usage, evidence so far, `pending_decision` when interrupted, `final_report` when done, errors |
| `POST /api/agent/resume` | **202**, same shape as start. **409** if not awaiting input, **404** unknown thread, **422** invalid decision |

`RunSnapshot` is one response model serving all states, so the frontend renders a single shape and never branches on which endpoint replied.

### 9.2 Run lifecycle and its honest limitation

A `RunRegistry` maps `thread_id → RunHandle{asyncio.Task, started_at, phase}`. Status derivation, in order:

```
checkpoint has interrupts                 → AWAITING_HUMAN
checkpoint next == ()                     → COMPLETED | COMPLETED_WITH_WARNINGS
registry task raised                      → FAILED (+ AppError)
registry task awaiting the semaphore      → QUEUED
registry task running                     → RUNNING
no registry entry, checkpoint incomplete  → ORPHANED
```

The ladder is evaluated in order, so a terminal checkpoint always wins over whatever the registry believes.

The registry is in-process; the checkpoint is on disk. **If the server restarts mid-run the checkpoint survives but the task does not** — so rather than reporting `RUNNING` forever behind a spinner that never resolves, the run reports `ORPHANED` and offers resume-from-last-checkpoint.

Two consequences recorded in ADR-001 rather than discovered later:

- **Spec 1 must run single-worker.** An in-memory registry is wrong under `uvicorn --workers 2`: two processes, two registries, half the status lookups blind. Sub-project 3 moves the registry into Postgres and lifts this.
- Concurrent runs are capped by a semaphore (`MAX_CONCURRENT_RUNS`, default 4). Beyond the cap, status is `QUEUED` — a real state, not a lie about running.

### 9.3 Error taxonomy

`AppError{code, message, detail, node, retryable}`. `message` goes to the client; `detail` goes to logs correlated by `thread_id`. A centralized FastAPI exception handler performs the mapping:

| Code | HTTP |
|---|---|
| `INVALID_REPO_URL`, `DEPENDENCY_NOT_FOUND`, `VERSION_INVALID`, `INVALID_DECISION` | 422 |
| `LOCAL_PATH_FORBIDDEN` | 403 |
| `REPO_TOO_LARGE` | 413 |
| `THREAD_NOT_FOUND` | 404 |
| `THREAD_NOT_AWAITING_INPUT` | 409 |
| `LLM_RATE_LIMITED` | 429 |
| `REPO_UNAVAILABLE`, `LLM_UNAVAILABLE` | 502 |
| `KB_UNAVAILABLE` | 503 |
| `INTERNAL` | 500 |

Every node classifies its failures as **recoverable** (one retrieval query failed → append `AppError`, emit trace event, continue degraded, confidence ceiling applies) or **fatal** (repo unavailable → terminal `FAILED`). "Do not silently swallow" is enforced by the rule that a caught exception always produces both a state `AppError` and a trace event; a bare `except: pass` fails review.

CORS is configured from settings with an explicit origin allowlist.

### 9.4 Token and cost tracking

All model access goes through one `TrackedLLM` service — `invoke_structured(node, prompt, schema) -> (parsed, LLMCall)`. Nothing else in the codebase touches a chat model, so there is exactly one place usage can be missed.

Three hazards, handled explicitly:

1. **Which usage surface is populated is version-dependent.** The extractor reads `AIMessage.usage_metadata` first, falls back to `response_metadata["token_usage"]`, and if neither is present records the call with `tokens_estimated=True` from a tiktoken count — **surfaced in the UI as estimated, never passed off as exact**. Phase 0 probes the pinned versions and records the finding in ADR-001.
2. **`with_structured_output()` can swallow the raw message**, and with it the usage metadata — an easy path to reporting zero tokens. Mitigation: `with_structured_output(Schema, include_raw=True)`, returning both the parsed object and the raw `AIMessage`. Verified in Phase 0.
3. **Pricing lives in settings**, not as code constants: `MODEL_PRICING: {model: {input_per_1m, output_per_1m}}`. An unknown model yields `cost = None` plus a `pricing_unknown` flag — never a fabricated `$0.00`.

   **Measured refinement (2026-08-25).** OpenRouter returns the real charge
   for every call in `response_metadata["token_usage"]["cost"]` — observed
   `8.8e-06` on a 16-token completion, and `2e-08` on an embedding. OpenAI
   direct returns no such field. So `TrackedLLM` should prefer a
   provider-reported cost where one is present and fall back to
   `MODEL_PRICING` where it is not, marking the difference: a measured charge
   and a table lookup are not the same fact and the report should not print
   them as though they were. The table is still required — it is the only
   path when the provider stays silent — but it stops being the *primary*
   one. Phase 4 owns this.

Embedding calls are recorded as `LLMCall` with `kind=EMBEDDING` and zero output tokens, so estimated cost includes retrieval spend rather than quietly omitting it.

`UsageSummary` is derived, never stored, and carries `by_model`, `by_node`, `estimated`, and `pricing_complete`. `by_node` is worth the few extra lines: "where did the tokens actually go" is the second question a developer asks.

---

## 10. Frontend

One route. View selection derives from `RunSnapshot.status`:

| status | view |
|---|---|
| `idle` | Configuration |
| `queued`, `running` | Activity |
| `awaiting_human` | Human Review, rendered above a still-visible, still-incomplete timeline so the workflow can never look finished while waiting |
| `completed`, `completed_with_warnings` | Report |
| `failed`, `orphaned` | Error, with retry or resume-from-checkpoint |

Persistent left sidebar (config summary and run metrics), main workspace, top bar with a status pill and the Agent Trace drawer trigger. Components: `ConfigurationForm`, `ActivityTimeline`, `EvidencePanel`, `HumanReviewPanel`, `ReportView`, `AgentTraceDrawer`, `RunMetrics`.

**All server state flows through one `useRunPolling(threadId)` hook.** 1s interval while non-terminal, stops on terminal status, backs off on network error, aborts on unmount. Because each snapshot is *complete* rather than incremental, there is no client-side accumulation or merge logic — the concrete payoff of polling over SSE, and what keeps orchestration out of React.

Two dependency decisions:

- **No form library.** Controlled inputs plus a small validate function mirroring backend rules; the backend stays authoritative and its 422 detail renders inline. Six fields do not earn a form library.
- **`openapi-typescript` as a dev dependency**, generating TS types from FastAPI's OpenAPI schema. Hand-mirrored interfaces drifting from Pydantic response models is a real bug class, eliminated for one dev-only package.

Semantic color tokens live in the Tailwind config (`risk-high`, `risk-medium`, `risk-low`, `pending-input`) rather than raw colors at call sites. `aria-live` on the status region announces the transition into Human Review. Lucide icons: `GitBranch`, `Database`, `Search`, `ShieldAlert`, `CheckCircle`, `Clock`, `Coins`, `FileText`, `UserCheck`.

The Agent Trace drawer exposes observable events — node boundaries, queries issued, sources retrieved and selected, decisions recorded, validation outcomes. It does not expose internal prompts or private reasoning.

Duplicate resume is blocked three ways: disabled button, local `submitting` flag, and the server's 409 — the last being the only real guarantee.

---

## 11. Testing

Six layers.

1. **Unit — no LLM, no network** (the bulk). AST analyzer against checked-in fixture files exercising each usage kind, including a deliberately unparseable one. Version detection across every manifest type. Risk threshold table. Each of validation's ten checks with a passing and a failing case. Reducers, including dedup-by-`source_id`. **Usage aggregation across a simulated resume**, asserting no double-count. Cost calc with an unknown model asserting `None` rather than `$0.00`. Path guards: traversal, symlink escape, size caps.
2. **Knowledge base** — real Chroma, deterministic fake embedding function, fixture corpus. Scalar filters, `$contains` symbol filtering (including the negative direction: a prefix-colliding symbol must not match), and the golden set asserted against recall@5 and MRR floors.
3. **Graph — scripted fake chat model** returning queued structured responses with synthetic usage metadata. Paths: happy; RAG refinement (assert two query rounds); max-iteration cutoff; **deterministic gate overriding an LLM that falsely claims sufficiency**; interrupt (assert `__interrupt__`, persisted checkpoint, `AWAITING_HUMAN`); resume via `Command(resume=…)`; **decision-flip — same checkpoint, opposite option, different `strategy_id`**; no-interrupt when constraints decide; validation-failure retry; confidence ceiling when evidence is absent; two interleaved threads with no state bleed.
4. **API** — `httpx.AsyncClient` over ASGI. Every status code in the taxonomy; response schemas asserted against the Pydantic models.
5. **One E2E HITL test** — fake LLM, real Chroma, real vendored repo fixture, real SQLite checkpointer. start → poll to `AWAITING_HUMAN` → resume → poll to `COMPLETED`, asserting evidence refs resolve, usage is non-zero, and the decision is applied.
6. **One opt-in live test**, `@pytest.mark.live`, skipped without `--live` and a key. Makes exactly one real call and asserts `usage_metadata` is populated.

Layer 6 exists for a specific reason: with a fake LLM supplying synthetic usage metadata, **every token-tracking test can pass while the real extractor is broken and the counter reads zero.** One real call closes that gap.

Frontend: Vitest and React Testing Library over the polling hook (with MSW) and `HumanReviewPanel` (renders options, disables on submit).

CI: `pytest`, `ruff`, `mypy`, `vitest`, `tsc --noEmit`.

**mypy scope.** Strict over the whole of `src/upgradepilot`. This clause previously sanctioned "strict over `models/` and `services/`", which left `__init__.py`, `config.py` and all four files under `api/` unchecked — `config.py` being the module everything else imports — so `strict = true` was doing visibly less than it appeared to. Widening it cost nothing: all six previously-excluded files pass strict as they stand.

`tests` is **not** in scope, and that is a measured decision rather than the old omission carried forward: adding it reports 130 errors across 13 files. Most are ordinary test-code looseness (22 unannotated functions, plus `arg-type` and `call-overload` noise from calling LangGraph and Chroma with literal dicts where the stubs want `RunnableConfig`), but 8 of them are in `tests/fixtures/sample_repo/` — the deliberately Pydantic-v1, deliberately-unparseable fixture tree, whose contents must never be "fixed". Bringing `tests` under mypy therefore requires excluding that fixture first, and is its own piece of work rather than a config edit.

---

## 12. Assumptions

1. Python repositories only. The analyzer is AST-based and Python-specific; other ecosystems are out of scope for Spec 1.
2. The knowledge corpus is authored and ingested by the project, not crawled at runtime.
3. A single backend process serves all runs (§9.2).
4. The model provider speaks the **OpenAI API**; which vendor serves it is
   configuration. `llm_base_url` selects the endpoint and `llm_api_key` is
   read from `OPENROUTER_API_KEY`, `OPENAI_API_KEY` or `UP_LLM_API_KEY`, in
   that order. The `TrackedLLM` seam still means swapping to a provider that
   does *not* speak this API touches one module.

   **Amends the original assumption 4**, which named OpenAI as the only
   provider. The project is developed against OpenRouter, and Phase 0's
   usage-metadata and structured-output probes were closed there rather than
   against OpenAI direct — recorded that way in ADR-001, because a pass
   against one endpoint is not a claim about the other. Two consequences
   worth carrying: model identifiers are provider-scoped and disagree
   (`openai/gpt-4.1-mini` against `gpt-4.1-mini`), and a gateway is one more
   hop that can fail, which §9.3's taxonomy already covers as
   `LLM_UNAVAILABLE` (2026-08-25).
5. Analyzer unit tests run against a hand-authored miniature project
   (`backend/tests/fixtures/sample_repo/`) built into a temp directory with
   real git history by `build_sample_repo()`. This keeps assertions small,
   readable, and precisely targeted at each usage pattern. A real public
   repository is pinned by commit in Phase 12 for the demo and end-to-end
   path.

   **Amends the original assumption 5**, which specified a real public
   repository vendored and pinned for analyzer unit tests. That vendoring
   would make assertions large and brittle without testing anything
   additional, so unit tests use the hand-authored fixture above instead;
   the originally-intended real, pinned repository still exists, but is
   deferred to Phase 12's demo and end-to-end path (recorded when Task 13
   built the fixture, 2026-08-24).

## 13. Definition of done for Spec 1

1. `GET /api/health` reports store and configuration status.
2. A public repository URL or an allowed local path is analyzed into structured evidence with file/line usage sites and confidence labels.
3. The dependency and its current version are detected from a manifest, with confidence and role, or a clear `DEPENDENCY_NOT_FOUND` error.
4. The corpus is ingested into ChromaDB with resolvable source metadata; the golden set meets its recall and MRR floors.
5. The RAG subgraph performs multiple retrieval iterations when the first result set is insufficient, and the deterministic gate can override a model claiming sufficiency.
6. Risk assessment produces factors with resolving evidence refs, and the clamps and confidence ceilings hold under test.
7. The graph interrupts only when a genuine tradeoff exists, and does not interrupt when constraints decide.
8. React renders the decision interface; `POST /resume` continues the same thread; a second resume returns 409.
9. Flipping the decision on the same checkpoint changes the generated strategy — asserted by test.
10. `validate_plan` passes and fails for the right reasons, with a bounded repair retry.
11. Token and cost figures are derived from real `LLMCall` records, include embeddings, flag estimates and unknown pricing, and appear in the sidebar.
12. The agent trace is visible and expandable, exposing observable events only.
13. Every error code in §9.3 maps to its status and renders a comprehensible message.
14. A server restart mid-run surfaces `ORPHANED` rather than a hanging spinner.
15. The full test suite passes, including the E2E HITL flow.
