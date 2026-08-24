# ADR-001: UpgradePilot System Architecture

- **Status:** Accepted
- **Date:** 2026-08-24
- **Deciders:** Project owner
- **Related:** `docs/superpowers/specs/2026-08-24-upgradepilot-agent-core-design.md`

## Context

UpgradePilot answers one developer question: *before I upgrade this dependency, what will break, how risky is it, what evidence supports that, and what should I do?*

That framing sets the architectural priority. The product's value is **trustworthy evidence**, not fluent prose. An answer that sounds authoritative but cites a file that does not exist, or a breaking change nobody documented, is worse than no answer — it costs the developer a wasted afternoon and their trust. Every significant decision below is a consequence of that priority.

The system must also demonstrate LangGraph orchestration, LangChain LLM integration, agentic RAG, human-in-the-loop via `interrupt()`, checkpointed thread state, and token/cost tracking. The requirement we hold ourselves to is that each of these exists because it solves a real problem in this product, not because it is on a list.

## Decision

### D1. Three sub-projects, agent core first

The full product — agent, GitHub/OAuth, accounts/history — decomposes into three specs built in that order. Every graded capability lands in the first, so the novel LangGraph/RAG risk is retired before effort goes into authentication plumbing.

### D2. Four layers, one-way dependencies

`api/ → graph/ → services/ → models/`. `services/` never imports LangGraph; `graph/` never imports FastAPI. The repository analyzer and knowledge base are consequently testable with neither a graph nor an HTTP client, which is where the majority of test value lives.

### D3. Three stores, three distinct jobs

The original brief blurred these, so they are stated explicitly:

| Store | Job | Spec 1 implementation |
|---|---|---|
| ChromaDB | semantic knowledge for retrieval | persistent local directory |
| LangGraph checkpointer | thread/run state; enables interrupt/resume **and** serves status polling | `AsyncSqliteSaver` |
| PostgreSQL | accounts, analysis history | absent — arrives in Sub-project 3 |

The checkpointer is explicitly **not** a general-purpose knowledge store. Thread state and semantic knowledge are separate concerns with separate lifetimes.

### D4. Deterministic evidence, LLM judgment

Python's `ast` module supplies usage facts with real file and line numbers. The LLM generates retrieval queries, judges relevance, and writes narrative and plan prose. It never produces a file path or line number, and it never sets a risk factor's level.

The invariant is enforced in the type system rather than in prompts: `BreakingChange.source` is a required `SourceRef`, and `RiskFactor.evidence` has `min_length=1`. An uncited claim is unconstructable — a `ValidationError`, not a style violation.

### D5. Repository source abstraction

`RepoRef` resolves to a `Workspace` — the only thing the analyzer ever sees. Spec 1 provides a shallow-clone resolver (public URLs, no credentials) and a local-path resolver. Sub-project 2 adds authenticated clones without touching the analyzer.

Shallow clone at depth 100 rather than depth 1, because churn signals require history. This also makes `git log` available directly, which the GitHub REST API would have required several paginated calls to supply.

### D6. Agentic RAG as a compiled subgraph, with no curated symbol index

The retrieval loop is a compiled LangGraph subgraph: `plan_retrieval → retrieve → evaluate_retrieval → [sufficient?] → plan_retrieval | build_context`.

Critically, **there is no hand-written symbol → breaking-change table.** Such a table would make retrieval decorative — the agent would be retrieving evidence for conclusions already reached in a Python dict. Instead the corpus is chunked one breaking change per document with `affected_symbols` metadata, and the AST-discovered symbols drive both the queries and the coverage evaluation. The join is performed by retrieval.

A deterministic gate sits on top of the LLM's sufficiency judgement: if any high-confidence symbol has zero retrieved chunk mentioning it, evidence is insufficient regardless of what the model claims. The model cannot declare victory over a mechanical gap.

### D7. Human-in-the-loop fires on a predicate, not on a schedule

`interrupt()` is called only when at least two migration strategies remain viable **and** they differ on an axis the user's stated constraints do not settle. When constraints decide the matter, the graph records that fact in the trace and continues. This is what separates a meaningful decision point from a ceremonial confirmation dialog.

Resume payloads arrive from HTTP and are therefore treated as untrusted: validated against `HumanDecision`, re-interrupting on an unknown option rather than proceeding with garbage. `POST /resume` against a thread not awaiting input returns 409, so duplicate submission is refused server-side rather than merely discouraged by a disabled button.

### D8. Background execution with status polling

`POST /start` returns 202 immediately; the graph runs as a background task; `GET /status/{thread_id}` reads the latest checkpoint.

A blocking POST cannot drive a live activity timeline — this was an unresolved gap in the original brief. SSE was considered and rejected for Spec 1 (see A3). Polling has a further benefit: because each snapshot is complete rather than incremental, the frontend needs no accumulation or merge logic, which keeps orchestration out of React as required.

### D9. Usage is recorded as append-only call records, never as running totals

`llm_calls` is an append-only channel; `UsageSummary` is a derived pure function. A stored running total double-counts as soon as a node re-executes after resume, because LangGraph replays the interrupted node. Deriving the total makes it idempotent and unit-testable without a graph.

All model access passes through a single `TrackedLLM` service, so there is exactly one place where usage can be missed. Pricing lives in settings; an unknown model produces `cost = None` and a `pricing_unknown` flag, never a fabricated `$0.00`. Embedding calls are recorded too — omitting them would make "estimated cost" wrong.

### D10. Status is derived, never stored

`RunStatus` is computed from the checkpoint plus an in-process run registry. A stored status field drifts from reality on crash. The derivation includes an `ORPHANED` state for the case where the server restarted mid-run: the checkpoint survived, the task did not. Reporting `ORPHANED` with a resume affordance is better than a spinner that never resolves.

## Security considerations

- Accepting a URL *or* a filesystem path is an arbitrary-read surface. Mitigations: URL scheme allowlist, credentials-in-URL rejected, local paths confined to `ALLOWED_LOCAL_ROOTS`, symlinks escaping the root skipped, hard file-count and byte caps enforced before analysis.
- The analyzer extracts only what it needs. Whole repositories are never placed in prompts — this bounds both privacy exposure and token cost.
- Secrets live in `.env`, never in git. `.env.example` is committed.
- `AppError` separates a user-facing `message` from a technical `detail`; only the former reaches the client, while the latter is logged correlated by `thread_id`.

## Alternatives considered

**A1. LLM-centric repository analysis** — hand file contents to the model and ask what breaks. Rejected: non-reproducible findings, high per-run token cost, and file/line citations that can be confidently wrong. That last failure mode is the one the product exists to avoid.

**A2. Curated symbol → breaking-change index** — reliable and demo-safe. Rejected: it becomes the real source of truth, leaving retrieval as ornament, and the honest answer to "is the RAG necessary?" becomes "no." Kept as a documented fallback only if the golden-set floors prove unreachable, in which case this ADR is amended to say so plainly.

**A3. Server-Sent Events for progress** — smoother UI and the more idiomatic choice for a developer tool. Deferred rather than rejected: it costs background-task lifecycle management, an event schema, reconnection handling, and materially harder endpoint tests, for a run lasting well under a minute. Polling at 1s is visually equivalent here. Revisit if runs grow long enough that per-token streaming matters.

**A4. ReAct tool-calling agent for retrieval** — maximum model autonomy. Rejected for Spec 1: unbounded iteration without extra guards, run-to-run variance, and a trace that must be reconstructed from tool-call messages.

**A5. Explicit parent-graph nodes for the RAG loop instead of a subgraph** — better parent-topology visibility and a free trace. Not chosen; the subgraph's encapsulation was preferred, with an explicit wrapper node compensating for the trace-propagation loss.

**A6. GitHub REST API without cloning** — no disk usage and closest to the brief's literal wording. Rejected: rate limits become a day-one concern, broad file scanning needs many calls, and git history costs extra requests.

**A7. Zip upload** — best for a hosted multi-machine deployment. Deferred: loses git history entirely, which removes the churn signal.

## Consequences

**Accepted costs**

- Spec 1 runs single-worker. The in-memory run registry is wrong under multiple uvicorn workers. Documented as a constraint; Sub-project 3 moves the registry into Postgres and lifts it.
- The analyzer is Python-only and heuristic. Generic method names (`.dict()`, `.json()`) are confidence-labelled rather than treated as certain, so some findings are explicitly uncertain — which is the honest outcome, not a defect.
- Authoring one corpus document per breaking change is more discipline up front than dumping a migration guide in whole.
- The subgraph choice requires an explicit state-mapping wrapper.

**Benefits**

- Most logic is testable with no LLM and no network.
- Fabricated file paths and uncited breaking changes are structurally impossible.
- The checkpointer earns its place twice: interrupt/resume *and* status polling.
- Swapping model provider, checkpointer backend, or repository source each touch one module.

**Obligations created**

- The corpus and its golden evaluation set must be maintained together; a new corpus document without a golden-set case is incomplete work.
- `MODEL_PRICING` in settings must be kept current, or cost figures degrade to `pricing_unknown`.
- One opt-in live test must remain in the suite, because fake-LLM tests can pass while real usage extraction is broken.

## Phase 0 verification record

**Status: pending Phase 0 execution.** This ADR is not complete until the table below is filled from actual probe runs. Nothing in the design may rely on an unverified assumption about these.

| Probe | What must be recorded |
|---|---|
| Pinned versions | Resolved versions of `langgraph`, `langgraph-checkpoint-sqlite`, `langchain-core`, `langchain-openai`, `chromadb`, `fastapi`, `pydantic` |
| Usage metadata surface | Whether `AIMessage.usage_metadata` is populated, or the `response_metadata["token_usage"]` fallback is required |
| Structured output + usage | Whether `with_structured_output(Schema, include_raw=True)` preserves usage metadata |
| Checkpointer round-trip | `AsyncSqliteSaver` interrupt then resume on the same thread, state intact |
| Thread isolation | Two concurrent threads, no state bleed |
| ChromaDB | Persistence across process restart; scalar metadata filtering behaviour |
| Embeddings | `text-embedding-3-small` reachable; token accounting for embedding calls |

Findings that contradict this ADR must be raised and the ADR amended before implementation proceeds, per development rule 14.
