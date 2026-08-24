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

**Status: verified 2026-08-24.** Every row below is filled from either a command run this session or an assertion in a test file that has been read. Two rows remain genuinely unverified because they require a live `OPENAI_API_KEY`, which this environment does not have; they are recorded as unverified rather than guessed, per development rule 9.

| Probe | Finding |
|---|---|
| Interpreter | Python `3.14.5 (v3.14.5:5607950ef23, May 10 2026)`, Apple Clang 21.0.0, macOS arm64 (Darwin 25.5.0). Only interpreter available on this machine; no `uv` (checked `PATH`, `/opt/homebrew/bin`, `/usr/local/bin`, `~/.local/bin`, `~/.cargo/bin`, and `backend/uv.lock` — none present), no `pyenv`. All backend tooling therefore runs as `backend/.venv/bin/python -m <tool>`. *Source: commands run this session (`.venv/bin/python --version`, `which uv`/`pyenv`, `ls` of the candidate install paths).* |
| Pinned versions | `chromadb 1.5.9 · fastapi 0.141.1 · langchain-core 1.6.0 · langchain-openai 1.6.0 · langgraph 1.2.11 · langgraph-checkpoint-sqlite 3.1.1 · openai 3.3.1 · pydantic 2.13.4 · pydantic-settings 2.15.0 · tiktoken 0.14.0 · uvicorn 0.52.4 · pytest 9.1.1 · pytest-asyncio 1.4.0` — all confirmed installed at exactly these versions in `backend/.venv`. The venv holds 121 third-party packages plus the local editable `upgradepilot` project (122 lines from `pip list --format=freeze`); all resolved as prebuilt wheels for cp314 on macOS arm64 — none compiled from source during install. *Source: commands run this session (`.venv/bin/python -m pip list --format=freeze`, `wc -l`).* |
| Usage metadata surface | **UNVERIFIED** — requires a live `OPENAI_API_KEY`. There is no `backend/.env` and no `OPENAI_API_KEY` in the environment; a placeholder key would produce auth failures rather than a clean skip, so none was fabricated. `probes/probe_llm.py` has never been run. The probe is written and the guarding test exists (`backend/tests/llm/test_usage_metadata_live.py`, marked `@pytest.mark.live`, confirmed to skip cleanly with reason "needs --live and a real OPENAI_API_KEY" under `pytest -rs`). Phase 4's `TrackedLLM` extractor must retain its `response_metadata["token_usage"]` fallback until this is closed. *Source: commands run this session (checked for `.env` and the env var; ran `pytest -rs` and observed the skip) plus the test file itself.* |
| Structured output + usage | **UNVERIFIED** — same blocker as above; `probe_llm.py`'s structured-output branch has never executed against a real API key. As source-reading rather than execution: `langchain_openai/chat_models/base.py` line 2758 shows `with_structured_output(..., include_raw=True)` returns `RunnableMap(raw=llm) | parser_with_fallback`, so `raw` is the unmodified `AIMessage` from the underlying chat model and should carry `usage_metadata` the same way a plain `invoke()` does — but this is an inference from source, not a demonstrated result. *Source: read of `backend/.venv/lib/python3.14/site-packages/langchain_openai/chat_models/base.py:2758`.* |
| Checkpointer round-trip | Proven by `backend/tests/graph/test_langgraph_contract.py`. `test_interrupt_exposes_payload_and_pauses` shows `ainvoke` returns an `__interrupt__` payload and the graph pauses with `state.next == ("review",)`. `test_resume_continues_same_thread_and_applies_decision` shows `Command(resume=...)` on the same `thread_id` continues execution and applies the resumed decision, leaving `state.next == ()`. `test_state_survives_a_new_saver_instance` shows the checkpoint is durable: a *new* `AsyncSqliteSaver.from_conn_string` opened over the same sqlite file after the first `async with` block has closed still reports `state.next == ("review",)` and can resume correctly — this is genuine process-restart durability, not just in-memory continuity. *Source: read of `backend/tests/graph/test_langgraph_contract.py`; confirmed passing via `pytest -rs` this session (19 passed, 2 skipped).* |
| Thread isolation | Proven by `test_threads_are_isolated` in the same file: two threads (`t1`, `t2`) on one compiled graph advance independently — `t1` is driven through interrupt and resume to completion while `t2` is only invoked once and is still paused (`state.next == ("review",)`) with its own independent `trace`. No state from one thread appears in the other. *Source: read of `backend/tests/graph/test_langgraph_contract.py`; confirmed passing via `pytest -rs` this session.* |
| ChromaDB | Proven by `backend/tests/knowledge/test_chroma_contract.py` and directly reproduced this session: (1) a `PersistentClient` opened fresh over an existing directory sees data seeded by an earlier client instance (`test_persistent_client_survives_restart`) — genuine restart durability, not the same client object. (2) List-valued metadata (`affected_symbols`) is **accepted** and round-trips as a real Python `list`, not rejected and not flattened to a string (`test_list_valued_metadata_is_accepted_and_round_trips`). (3) `$contains` against list-valued metadata is **exact-element**, not substring: a filter for `Config` does not match a document tagged `["ConfigDict"]`, and a filter for `valid` does not match `["validator"]` (`test_symbol_filter_matches_whole_elements_not_substrings`). (4) `$in` against list-valued metadata returns `[]` rather than erroring or matching (`test_in_operator_does_not_work_on_list_metadata`) — it must never be used for this purpose. (5) A single-clause `$and`/`$or` raises `ValueError`, reproduced directly this session: `ValueError: Expected where value for $and or $or to be a list with at least two where expressions, got [{'a': 1}] in get.` (6) Collection names must be 3–512 characters from `[a-zA-Z0-9._-]`, reproduced directly this session: a 2-character name raises `InvalidArgumentError`, a 3-character name succeeds, a 513-character name raises the same error, and a 512-character name succeeds. *Source: read of `backend/tests/knowledge/test_chroma_contract.py` (items 1–4) plus ad hoc scripts run against the pinned `chromadb==1.5.9` this session (items 5–6).* |
| Embeddings | Deferred to Phase 3. `probe_llm.py` probes chat completion and structured output only; it never probed `text-embedding-3-small` or embedding token accounting. No claim is made about embeddings reachability here. *Source: read of `backend/probes/probe_llm.py`, which contains no embedding call.* |

### Finding not anticipated by the design: pre-interrupt side effects are billed twice

A node that calls `interrupt()` re-executes from the top on resume. Work
placed before the `interrupt()` call therefore runs twice, while state
writes from the aborted pass are discarded.

The append-only usage design (D9) is unaffected — no double-count reaches
state. But real spend doubles while only one call record survives, so
recorded usage would *undercount* actual cost.

**Rule adopted:** a node that interrupts performs no LLM call, no HTTP
request, and no file write before its `interrupt()` call. Strategy
enumeration and interrupt-payload construction belong to `assess_risk`;
`human_review` only calls `interrupt()` and validates the returned
decision. Locked by `backend/tests/graph/test_langgraph_contract.py`.

Findings that contradict this ADR must be raised and the ADR amended before implementation proceeds, per development rule 14.
