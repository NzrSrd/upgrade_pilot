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

### D11. The analyzer grades a tracked method call by receiver resolution, not by module-level imports

Spec §7.1's original rule graded `.dict()`, `.json()`, `.parse_obj()`, `.copy()`, `.schema()` as medium confidence "in a module that imports the dependency and defines models". Taken literally, that rule graded all four of the fixture's `service.py` tracked calls LOW: `service.py` imports its models from a first-party module (`app.models`) rather than importing the dependency directly, and defines no models of its own. The fixture exists specifically to distinguish a genuine medium-confidence call from the low-confidence trap in `util.py` (a plain class with its own unrelated `.dict()` method), and the module-level rule collapsed those two tiers into one — the exact distinction the fixture was built to preserve.

The code instead builds a repository-wide `ModelIndex` (`services/analysis/models_index.py`) in a first pass, over every candidate module at once, to a **fixed point**: `class Base(BaseModel)` in one file and `class Customer(Base)` in another is the ordinary shape of a real project, and a single pass over an arbitrary file order finds one class or the other depending on which came first, not both. A second pass (`usage.py`) then grades each tracked call by what its receiver resolves to — through the module's alias map and the index — medium when the receiver names an indexed model class, low otherwise.

**Measured, not merely decided:** `test_medium_confidence_symbols_equal_the_documented_set` (`tests/analysis/test_analyzer_end_to_end.py`) asserts `EXPECTED_MEDIUM_CONFIDENCE_SYMBOLS == ("copy", "dict", "parse_obj", "schema")` for equality against the analyzer's real output over the fixture, and `test_util_py_is_never_reported` asserts `util.py`'s absence from the output separately. Under the module-level rule as written, the first assertion would fail (those four calls would grade LOW, not MEDIUM).

### D12. Candidate file selection is two-phase, not one

Phase A byte-scans every `.py` file for the dependency's import root and `ast.parse`s only the hits. Phase B then byte-scans the files phase A rejected for the model class names phase A's parse discovered, catching a consumer that imports a model from a first-party module and never names the dependency itself.

Phase B and the index rebuild **alternate to a fixed point**, not once each. The rebuild over the expanded module set is what discovers a model defined in a module phase B admitted, and those newly discovered names are exactly what the next expansion has to search for. Running each stage once truncated every inheritance chain longer than one link: `Link0(BaseModel)` in one module, `Link1(Link0)` in a second, and a third module using only `Link1` was never searched for -- landing in neither `analyzed_files` nor `skipped_files` (the `analyzed + skipped <= total` validator is `<=`) and drawing no confidence reducer, so the report claimed a completeness it did not have. The loop terminates on its own for the same reason the `ModelIndex` fixed point does -- `expand_candidates` only considers files not already handled, a finite set that strictly shrinks whenever a pass adds anything -- and is additionally capped at `MAX_EXPANSION_PASSES` to bound cost against untrusted input. Hitting the cap emits a confidence reducer rather than truncating silently.

**Measured, not merely decided:** `test_a_three_link_model_chain_reaches_its_consumer` and `test_a_chain_deeper_than_the_expansion_cap_says_so_rather_than_truncating` (`tests/unit/test_analyzer_assembly.py`) both fail against the single-expansion version.

**Measured, not merely decided:** in this project's own fixture, `service.py` passed the one-phase byte filter only because its module docstring happened to contain the word "pydantic" — a word with no import-time meaning. Deleting that one word from the docstring removed four findings from the analyzer's output while the entire test suite stayed green: a silent failure, which is the exact category CLAUDE.md's rule 1 exists to prevent. `test_phase_b_is_what_finds_service_py_not_its_docstring` (`tests/unit/test_analysis_candidates.py`) performs that exact rewrite and asserts the file is still found — it fails if phase B is removed, and fails for the right reason. `test_phase_b_finds_a_consumer_that_never_names_the_dependency` covers the more general case (`consumer.py`, which never mentions "pydantic" anywhere).

**Honest residue, recorded rather than hidden:** `broken.py`, the fixture's deliberately unparseable file, is reachable only because a comment inside it names the dependency (`# pydantic: this file is deliberately unparseable ...`) — structurally the same accident as `service.py`'s docstring. This is judged acceptable where `service.py`'s was not, and the reason is the failure mode rather than the mechanism: deleting that comment turns `test_the_unparseable_file_becomes_a_skipped_record_not_an_exception` red immediately, because `broken.py` would then be invisible to both phases and its `SkippedFile` record would disappear from the suite's expectations. A fixture dependency that fails loudly on removal is tolerable; a production one that fails silently, as `service.py`'s did before phase B existed, is not.

## Security considerations

- Accepting a URL *or* a filesystem path is an arbitrary-read surface. Mitigations: URL scheme allowlist, credentials-in-URL rejected, local paths confined to `ALLOWED_LOCAL_ROOTS`, symlinks escaping the root skipped, hard file-count and byte caps enforced before analysis.
- `ALLOWED_LOCAL_ROOTS` confines **both** doors: a local-path `RepoRef` and a `file://` clone URL. Confining only the former would have left the allowlist trivially bypassable, because a `file://` URL is a local-disk read that merely looks remote — see the `file://` row in the verification record below for the two git behaviours that make that so. `clone_repository` therefore takes `allowed_local_roots` as a **required** keyword argument rather than defaulting it: a new call site must state the filesystem policy it is cloning under, and mypy refuses the call if it does not.
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
- The hardened git environment (`GIT_CONFIG_NOSYSTEM=1`, `GIT_CONFIG_GLOBAL=/dev/null`, `GIT_CONFIG_SYSTEM=/dev/null`) now covers `git rev-parse` and `git log` as well as `git clone`, so a developer's global `init.templateDir` or `core.hooksPath` can never reach a repository this tool touches. Accepted cost: a **global** `safe.directory` allowance is no longer consulted either, so a checkout owned by a different uid fails closed with a clear error instead of being read. A container mounting a repository owned by another uid will hit this. Failing closed is the right default for a tool whose input is a user-supplied path, but it is a real behaviour change and is recorded here rather than discovered in an issue.

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

**Status: verified 2026-08-24; the two live-key rows closed 2026-08-25.** Every row below is filled from either a command run in one of those sessions or an assertion in a test file that has been read. The usage-metadata and structured-output rows were recorded as `UNVERIFIED` rather than guessed for as long as no key was available (development rule 9); they were closed on 2026-08-25 by pointing the stack at OpenRouter, and both now state that scope explicitly — a pass against one OpenAI-compatible endpoint is not a claim about another.

| Probe | Finding |
|---|---|
| Interpreter floor | `requires-python = ">=3.14"`, and mypy's `python_version = "3.14"` to match. **Do not relax this.** `Workspace.iter_files` depends on `Path.rglob` defaulting to `recurse_symlinks=False`, which arrived in **3.13**. On 3.12 nothing leaks — the containment check still rejects an escaped file — but a symlink pointing at its own ancestor makes that generator non-terminating, and `enforce_caps` cannot stop it because caps are checked per yielded file. That is a hang reachable from user-supplied repository content on a declared-supported interpreter, which is why the floor moved off the previously-declared 3.12. It went to 3.14 rather than 3.13 because 3.14.5 is the only interpreter this code has ever run on; 3.13 would trade a false claim for an untested one. The mypy pin was actively wrong, not merely stale: a call to `rglob("*.py", recurse_symlinks=False)` is **rejected** at `--python-version 3.12` (`Unexpected keyword argument "recurse_symlinks"`) and **accepted** at 3.14 — so the old pin type-checked this code against a stdlib it does not run on. Widening mypy from `models`+`services` to the whole `src/upgradepilot` at the same time surfaced no new errors (14 → 20 files, "Success"). *Source: commands run this session (`mypy --strict --python-version 3.12` and `3.14` over a two-line reproduction, and `.venv/bin/python -m mypy` before and after the change).* |
| `file://` is a local-disk read, twice over | Two git behaviours found and closed on this branch, both on git 2.50.1 (Apple Git-155). (1) git **percent-decodes** a `file://` path, so `file://.../a%20b` reads the directory `a b` — a guard comparing the raw URL text against an allowlist would be comparing a different string from the one git opens. (2) git **ignores a `file://` URL's host entirely**: `file://otherhost/path` reads the *server's* own local disk and exits 0, with no network involved and no error about the unreachable host. Together these are why `ALLOWED_LOCAL_ROOTS` must govern `file://` clones and not only local-path refs — a `file://` URL that looks remote is a local read, and the path it reads is the decoded one. *Source: commands run against git 2.50.1 while closing these on this branch; enforced by `services/repo/guards.py` and covered in `tests/unit/test_clone.py`.* |
| Interpreter | Python `3.14.5 (v3.14.5:5607950ef23, May 10 2026)`, Apple Clang 21.0.0, macOS arm64 (Darwin 25.5.0). The only interpreter this project runs on, and the only one able to run it — but **not** the only interpreter on the machine: `/usr/bin/python3` also exists and is Python 3.9.6 (Apple's system Python). An earlier version of this row claimed it was the only interpreter available, which was simply wrong. There is no 3.12 or 3.13 anywhere on this machine, which is the fact that actually matters for the floor recorded below. No `uv` (checked `PATH`, `/opt/homebrew/bin`, `/usr/local/bin`, `~/.local/bin`, `~/.cargo/bin`, and `backend/uv.lock` — none present), no `pyenv`. All backend tooling therefore runs as `backend/.venv/bin/python -m <tool>`. *Source: commands run this session (`.venv/bin/python --version`, `/usr/bin/python3 --version`, `which -a python3 python3.12 python3.13 python3.14`, `ls /Library/Frameworks/Python.framework/Versions/`, `which uv`/`pyenv`, `ls` of the candidate install paths).* |
| Pinned versions | `chromadb 1.5.9 · fastapi 0.141.1 · langchain-core 1.6.0 · langchain-openai 1.6.0 · langgraph 1.2.11 · langgraph-checkpoint-sqlite 3.1.1 · openai 3.3.1 · pydantic 2.13.4 · pydantic-settings 2.15.0 · tiktoken 0.14.0 · uvicorn 0.52.4 · pytest 9.1.1 · pytest-asyncio 1.4.0` — all confirmed installed at exactly these versions in `backend/.venv`. The venv holds 121 third-party packages plus the local editable `upgradepilot` project (122 lines from `pip list --format=freeze`). Every one of the 122 is installed from a wheel — each `.dist-info` carries a `WHEEL` file — and the platform tags break down as 85 pure-Python (`py3-none-any`/`py2.py3-none-any`), 29 `cp314`, 6 older-ABI `abi3` macOS arm64/universal2 wheels, and 2 that are platform-locked despite a `py3` prefix (`ruff` and `sqlite-vec`, both `py3-none-macosx_11_0_arm64`). Those last two were previously counted as pure-Python on the strength of that prefix, which was wrong in the same direction as the withdrawn claim below: `ruff` ships a Rust binary and `sqlite-vec` a C extension, so neither is pure Python and neither is portable off macOS arm64. An earlier version of this row went further and said "none compiled from source during install"; that claim is withdrawn, because the cited `pip list`/`wc -l` cannot show it and **no** installed metadata can: a wheel pip builds locally from an sdist is recorded identically to one downloaded prebuilt. Whether anything was compiled during install is therefore not asserted here — it would need the install log, which was not kept. *Source: commands run this session (`.venv/bin/python -m pip list --format=freeze`, `wc -l`, and a `grep` of `^Tag:` across `.venv/lib/python3.14/site-packages/*.dist-info/WHEEL`).* **Frontend dev-only pins, added Phase 10 Task 1:** `openapi-typescript 6.7.6` (generates `frontend/src/api/schema.d.ts` from the checked-in `openapi.json` — the fix for hand-mirrored interfaces drifting from Pydantic response models, per rule 12 and spec §10) · `@testing-library/react 16.3.2` and `@testing-library/user-event 14.6.6` (spec §11's frontend test layer: render components and simulate interaction the way a user performs it, not as a synthetic event) · `@testing-library/jest-dom 7.0.1` (assertion vocabulary such as `toBeDisabled`, absent which every component test would hand-write DOM assertions) · `msw 2.15.0` (spec §11's HTTP-layer test double) · `jsdom 29.1.1` (the DOM implementation RTL's `environment: "jsdom"` needs to render components outside a browser). All six confirmed installed at exactly these versions via `node -e "require('./node_modules/<pkg>/package.json').version"` this session. **Deviation from the brief, recorded because it changed a plan:** the brief's Step 3 did not pin an `openapi-typescript` major version; the latest published release at the time, `7.13.0`, declares a peer on `typescript@^5.x` and, when actually invoked, throws `TypeError: Cannot read properties of undefined (reading 'createKeywordTypeNode')` — not a soft peer-range warning but a hard runtime break, because this project's already-pinned `typescript@^7.0.2` is TypeScript's native rewrite, whose npm package no longer exposes the classic Compiler API at its root import (`require("typescript")` resolves to `./lib/version.cjs`, version info only; the AST factory moved to the explicitly `unstable` `typescript/unstable/ast/factory` path) — the exact API `openapi-typescript@7.x` needs. `openapi-typescript@6.7.6`, the last 6.x release, generates TypeScript source as strings rather than through the Compiler AST and declares no `typescript` peer at all, so it was pinned instead; verified by running `npm run gen:api` against the real `openapi.json` and confirming the generated `schema.d.ts` carries the "do not make direct changes" banner and the same `components["schemas"][...]` shape the brief's `types.ts` indexes into. Downgrading the project's own `typescript` pin to accommodate `7.x` would have been the larger, unauthorized deviation — it is the version every later frontend task's `tsc -b` runs against — so it was not done. `openapi-typescript@6.7.6` pulls a transitively vulnerable `undici@5.29.0` (moderate/high advisories, exercised only by its optional remote-`$ref`-fetch code path, which this project's local-file-only `gen:api` invocation never reaches); a nested-scope `overrides` entry pins that one transitive copy to `undici@^6.21.2` (resolved `6.28.0`) without touching the top-level `typescript` pin, and `npm audit` reports zero vulnerabilities after the override. *Source: commands run this session (`npm view openapi-typescript peerDependencies` / `dependencies` across the 6.x and 7.x lines, `npm run gen:api` against both, direct inspection of the installed `typescript@7.0.2` package's `exports` map, and `npm audit` before/after the `undici` override).* |
| Usage metadata surface | **Verified against OpenRouter**, 2026-08-25. `message.usage_metadata` is populated on a plain `invoke()`: `{'input_tokens': 14, 'output_tokens': 2, 'total_tokens': 16, ...}`, with `response_metadata['token_usage']` carrying the same three figures under OpenAI's `prompt_tokens`/`completion_tokens` names. The LangChain-native surface is present, so Phase 4's `TrackedLLM` can read `usage_metadata` as its primary path. **Scope of this claim, stated precisely:** the endpoint exercised was `https://openrouter.ai/api/v1` with model `openai/gpt-4.1-mini`, not OpenAI direct. OpenRouter serves the same API and `langchain-openai` cannot tell the difference, but this run proves nothing about OpenAI's own endpoint, and the row must not be read as if it did. The `response_metadata['token_usage']` fallback stays in `TrackedLLM` regardless — it is now known to be populated too, so it costs nothing to keep. *Source: `.venv/bin/python probes/probe_llm.py` and `pytest --live tests/llm` (2 passed), both run this session against a real key.* |
| Structured output + usage | **Verified against OpenRouter**, 2026-08-25, replacing the previous source-reading inference. `with_structured_output(Verdict, include_raw=True).invoke(...)` returns keys `['parsed', 'parsing_error', 'raw']`; `parsed` is a real `Verdict` instance, `parsing_error` is `None`, and `result['raw'].usage_metadata` is populated (`{'input_tokens': 60, 'output_tokens': 38, 'total_tokens': 98, ...}`). The hazard in spec §9.4 — structured output swallowing the raw message and with it the usage — does not occur on this stack. Same scope caveat as the row above: OpenRouter, not OpenAI direct. *Source: `probes/probe_llm.py` and `tests/llm/test_usage_metadata_live.py::test_structured_output_with_include_raw_preserves_usage`, run with `--live` this session.* |
| Checkpointer round-trip | Proven by `backend/tests/graph/test_langgraph_contract.py`. `test_interrupt_exposes_payload_and_pauses` shows `ainvoke` returns an `__interrupt__` payload and the graph pauses with `state.next == ("review",)`. `test_resume_continues_same_thread_and_applies_decision` shows `Command(resume=...)` on the same `thread_id` continues execution and applies the resumed decision, leaving `state.next == ()`. `test_state_survives_a_new_saver_instance` shows the checkpoint is durable: a *new* `AsyncSqliteSaver.from_conn_string` opened over the same sqlite file after the first `async with` block has closed still reports `state.next == ("review",)` and can resume correctly — the data outlives the connection that wrote it, not merely the in-memory graph object. This is disk durability across a closed connection; it is not a process restart, since the test runs in one process. *Source: read of `backend/tests/graph/test_langgraph_contract.py`; confirmed passing via `pytest -rs` this session (19 passed, 2 skipped).* |
| Thread isolation | Proven by `test_threads_are_isolated` in the same file: two threads (`t1`, `t2`) on one compiled graph advance independently — `t1` is driven through interrupt and resume to completion while `t2` is only invoked once and is still paused (`state.next == ("review",)`) with its own independent `trace`. No state from one thread appears in the other. *Source: read of `backend/tests/graph/test_langgraph_contract.py`; confirmed passing via `pytest -rs` this session.* |
| ChromaDB | Proven by `backend/tests/knowledge/test_chroma_contract.py` and directly reproduced this session: (1) a `PersistentClient` opened fresh over an existing directory sees data seeded by an earlier client instance (`test_persistent_client_survives_restart`) — durability beyond the writing client's lifetime, not the same client object re-reading its own memory. Note this is a new client in the same process, not a process restart. (2) List-valued metadata (`affected_symbols`) is **accepted** and round-trips as a real Python `list`, not rejected and not flattened to a string (`test_list_valued_metadata_is_accepted_and_round_trips`). (3) `$contains` against list-valued metadata is **exact-element**, not substring: a filter for `Config` does not match a document tagged `["ConfigDict"]`, and a filter for `valid` does not match `["validator"]` (`test_symbol_filter_matches_whole_elements_not_substrings`). (4) `$in` against list-valued metadata returns `[]` rather than erroring or matching (`test_in_operator_does_not_work_on_list_metadata`) — it must never be used for this purpose. (5) A single-clause `$and`/`$or` raises `ValueError`, reproduced directly this session: `ValueError: Expected where value for $and or $or to be a list with at least two where expressions, got [{'a': 1}] in get.` (6) Collection names must be 3–512 characters from `[a-zA-Z0-9._-]`, reproduced directly this session: a 2-character name raises `InvalidArgumentError`, a 3-character name succeeds, a 513-character name raises the same error, and a 512-character name succeeds. *Source: read of `backend/tests/knowledge/test_chroma_contract.py` (items 1–4) plus ad hoc scripts run against the pinned `chromadb==1.5.9` this session (items 5–6).* |
| Embeddings | **Reachability verified against OpenRouter**, 2026-08-25; token accounting still deferred to Phase 3. `POST https://openrouter.ai/api/v1/embeddings` with `{"model": "openai/text-embedding-3-small", "input": "hello"}` returns HTTP 200, `model: "text-embedding-3-small"`, a **1536-dimension** float vector, and `usage: {'prompt_tokens': 1, 'total_tokens': 1, 'cost': 2e-08}`. **The finding that matters here is a near-miss, recorded because it would have changed a plan:** OpenRouter's `/api/v1/models` catalog lists 417 models and **zero** embedding models, and reading the catalog alone supports the conclusion that Phase 3 would need OpenAI direct for its ChromaDB embedding function. That conclusion is wrong — the endpoint proxies embeddings whether or not the catalog advertises them. The catalog is not evidence about the endpoint. **Both of the questions this row left open are now closed, in Phase 3 (2026-08-25).** (a) The client-library path works through the configured base URL — not `langchain-openai`'s `OpenAIEmbeddings` in the end, but the `openai` client directly, which is what `services/knowledge/embeddings.py` ships; the indirection `langchain-openai` adds buys nothing for an embedding call and hides `response.usage`. Measured through the shipping code against OpenRouter with `openai/text-embedding-3-small`: **1536 dimensions**, 15 tokens for a one-sentence input, and the whole 19-document corpus (30 chunks) embedding in a single request for **6191 tokens** — so a full ingest is one request and a negligible cost, which is worth knowing because it means rebuild-on-every-ingest is affordable and `ingest.py` relies on that. Proven by `tests/knowledge/test_embeddings_live.py` (3 tests, `@pytest.mark.live`), run this session. (b) Token accounting: `OpenAIEmbedding` records an `EmbeddingCall(model, texts, tokens)` per request from the provider's own `usage`, never an estimate — a gateway omitting `usage` contributes zero rather than a guess, so Phase 4 can still tell 'measured zero' from 'never reported'. Aggregating those into `UsageSummary` stays Phase 4, alongside the same work for chat calls; what could not be deferred is the *capture*, since a token count not recorded at the call is gone. Same scope caveat as the rows above: OpenRouter, not OpenAI direct. *Source: `curl` against both endpoints this session.* |

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

### Finding not anticipated by the design: unregistered types silently degrade to dicts

Found in Phase 4 while wiring the checkpointer. LangGraph 1.2.11 logs
"Deserializing unregistered type ... This will be blocked in a future version"
for every type it has not been told about — which is all of
`upgradepilot.models`.

"Blocked" undersells the behaviour, and that is the finding. Measured directly
against the pinned version with strict msgpack: deserialization does **not**
raise. It returns a plain `dict`. So a resumed run would carry dictionaries
wherever it expects Pydantic models, and D4's honesty invariants — the ones
this design deliberately encodes in types rather than in prompts — would
simply be absent from it: `BreakingChange.source` no longer required,
`RiskFactor.evidence`'s `min_length=1` no longer enforced, `LLMCall`'s
agreement between cost and basis no longer checked. Nothing raises at the
point of loss, and the first symptom appears somewhere else entirely.

**Rule adopted:** the checkpointer is always constructed through
`graph.checkpointer.open_checkpointer`, whose serializer registers an
allowlist **derived by walking `upgradepilot.models`** — models and enums
both, since an unregistered `StrEnum` degrades to a bare string and
`call.cost_basis is CostBasis.UNKNOWN` quietly stops being true. Derived
rather than hand-listed because a hand-list is what a model added in a later
phase gets forgotten from, and forgetting has no symptom until a resume.

One trap worth recording, because it cost a passing test that proved nothing:
`JsonPlusSerializer().with_msgpack_allowlist(types)` is a **silent no-op**.
The default allowlist is the sentinel `True` (permissive), and the method
returns `self` unchanged rather than narrowing it. The allowlist must be
passed to the constructor. *Source: `backend/tests/graph/test_checkpoint_serde.py`
and direct experiments run this session against the pinned
`langgraph 1.2.11`.*

### Finding not anticipated by the design: a loop bound written by a node body stops advancing when that body fails

Found in Phase 6 and again, independently, in Phase 8 — which is why it is
recorded as a rule rather than as two incidents.

`traced` discards a failed node body's update. That is correct: a half-built
update is not trustworthy, and rule 20's contract is an `AppError` plus a
trace event, not a partial write. The consequence nobody anticipated is that
**any counter a node body writes stops advancing the moment that body raises
an unexpected exception** — and both of this system's loops were bounded on
exactly such a counter.

The RAG loop bounded on `iteration`, written by `plan_retrieval`. The plan
repair loop bounded on `plan_attempts`, written by `generate_plan`. In both
cases an exception in the body left the counter where it was and the router
sent the run round again, forever: the run never completes, the API never
returns, and the only symptom is a checkpoint file growing on disk. Measured
both times, with a scripted model that ran out of responses standing in for
the bug.

**Rule adopted:** a loop bound is derived from `agent_trace`, never from a
channel a node body writes. `traced` emits a `node_started` event for every
node execution, before the body runs and regardless of what it does, so
counting those advances unconditionally. See
`graph/rag/state.rounds_started` and `graph/build.attempts_started`.

### Finding not anticipated by the design: `traced` swallowed LangGraph's control flow

Found in Phase 7, the first time a node called `interrupt()`.

`interrupt()` pauses a run by *raising* `GraphInterrupt`. Rule 20's catch-all
converted it into `AppError(INTERNAL)`: the graph recorded "an internal error
occurred while running human_review", carried on to the end, and produced a
complete report for a question nobody was ever asked. Nothing looked wrong
except one extra error in a channel nothing was reading yet.

**Rule adopted:** `traced` re-raises `langgraph.errors.GraphBubbleUp` before
its own handlers. `GraphBubbleUp` rather than `GraphInterrupt` specifically,
because `ParentCommand` and `GraphDelegate` are control flow too, and a
handler naming only the one we happened to hit would swallow the next one
silently. A paused run is not a failed one.

### Finding not anticipated by the design: `StateSnapshot.next` cannot answer "is this run finished?"

Found in Phase 9 while building spec §9.2's status ladder, whose second rung
is worded "checkpoint next == ()". Measured against the pinned LangGraph,
`next == ()` is true at **two** different moments, and the ladder cannot tell
them apart:

| moment | `next` | `tasks[*].interrupts` |
|---|---|---|
| input written, first node not yet scheduled | `()` | none |
| paused on a first `interrupt()` | `('human_review',)` | one |
| paused on a **re-asked** question | `()` | one |
| finished | `()` | none |

Two separate wrong answers follow. A ladder testing `next == ()` for
completion tells a client polling a second after `start` that the run is
`COMPLETED` — empty trace, no report — and the client stops polling. A ladder
testing `next` for "awaiting a human" reports a re-asked question as finished,
so the question is never answered and a partial report is presented as final.

**Rules adopted.** "Awaiting a human" is read from `tasks[*].interrupts`
(`graph/inspect.is_awaiting_human`), which is correct in every row above.
"Finished" is read from `final_report`, which `finalize` sets and nothing else
does, and which `traced` guarantees every path reaches. *Source:
`backend/probes/probe_interrupt.py` and
`tests/graph/test_human_in_the_loop.py::test_a_re_asked_question_still_reads_as_awaiting_a_human`,
which pins the measurement so that a LangGraph change fixing `next` turns a
test red rather than leaving a stale workaround in place.*

### Amendment to D7: the interrupt predicate is an ordering, not a weighting

D7 says the human-in-the-loop fires on a predicate. Phase 7 implemented it and
found that the *recommendation* beside the question needs the same
discipline. The first version scored strategies by summing weighted penalties,
and it recommended the **highest**-effort strategy to a user who had asked to
minimise effort, because the lowest-risk option's risk saving outweighed its
effort cost at whatever weights happened to be written down. Every candidate
fix was a matter of choosing a bigger number, which is the signal that the
model was wrong rather than the numbers.

**Rule adopted:** a stated preference reorders the comparison rather than
reweighting it. `services/strategy/catalog.ranking_priority` puts the axes the
user spoke about first and compares lexicographically, so there is nothing
left to tune and no weight that can invert a stated preference.

### Amendment to D5: the workspace does not survive the interrupt

D5 abstracts repository access behind `Workspace`. Phase 5 wired it into the
graph and the lifetime question became concrete: a run pauses at
`human_review` and may be resumed days later, quite possibly by a different
process after a restart, and a remote clone re-opened on resume is a
*different* checkout of a branch that may have moved.

**Rule adopted:** `analyze_repo` opens and closes the workspace inside its own
node, and every file, line and version fact the report prints is captured into
state there. No later node reads the repository. The consequence is that spec
§8.4's checks 2 and 3 — worded "the file exists in the workspace" — resolve
against `RepoAnalysis.citable_paths()` / `.citable_lines()` instead. That is a
strengthening rather than a compromise: "exists on disk" would accept any path
in the repository including one nothing here ever read, while the analysis
record is exactly the set of locations this system is entitled to name.

Findings that contradict this ADR must be raised and the ADR amended before implementation proceeds, per development rule 14.
