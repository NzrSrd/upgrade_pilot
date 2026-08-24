# UpgradePilot Phase 0–1 Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish a verified, runnable baseline (Phase 0) and a tested repository-access layer with the project's core typed domain models (Phase 1).

**Architecture:** Four layers with one-way dependencies — `api/ → graph/ → services/ → models/`. Phase 0 proves every external assumption with committed probe scripts before application code exists. Phase 1 builds `models/` (pure, no I/O) and `services/repo/` (the `RepoRef → Workspace` abstraction that is the only thing the analyzer will ever see), so both are fully testable with no graph and no HTTP.

**Tech Stack:** Python 3.14.5, FastAPI, LangGraph 1.x, LangChain, ChromaDB, Pydantic v2 · React 19, Vite 8, TypeScript 7, Tailwind CSS 4, Lucide.

**Spec:** `docs/superpowers/specs/2026-08-24-upgradepilot-agent-core-design.md`

## Global Constraints

Every task's requirements implicitly include this section.

**Toolchain (verified on this machine, 2026-08-24)**
- Python **3.14.5** is the only interpreter installed. No `uv`, no `pyenv`. Use `python3 -m venv` and `pip`.
- Node **24.13.1**, npm **11.8.0**, git **2.50.1**. Platform: macOS arm64 (Darwin 25.5.0).
- `OPENAI_API_KEY` is **not** currently in the environment. Task 5 is blocked until it is placed in `backend/.env`.

**Backend pins** — all resolved and verified installable on cp314 with wheels; nothing compiles from source.

```
fastapi==0.141.1
uvicorn==0.52.4
langgraph==1.2.11
langgraph-checkpoint-sqlite==3.1.1
langchain-core==1.6.0
langchain-openai==1.6.0
openai==3.3.1
chromadb==1.5.9
pydantic==2.13.4
pydantic-settings==2.15.0
tiktoken==0.14.0
```

Dev: `pytest==9.1.1`, `pytest-asyncio==1.4.0`, `pytest-cov==7.1.0`, `httpx==0.28.1`, `ruff==0.16.4`, `mypy==2.3.1`.

**Frontend pins:** `react@19.2.8`, `react-dom@19.2.8`, `vite@8.2.2`, `typescript@7.0.2`, `@vitejs/plugin-react@6.1.0`, `tailwindcss@4.3.3`, `@tailwindcss/vite@4.3.3`, `lucide-react@1.34.0`, `vitest@4.1.11`, `openapi-typescript@7.13.0`.

**Tailwind is v4 — CSS-first.** There is **no** `tailwind.config.js` and **no** `postcss.config.js`. Configuration is `@import "tailwindcss";` plus an `@theme { }` block in CSS. Do not follow Tailwind v3 instructions.

**Verified LangGraph 1.x API** — use exactly these imports and shapes:
- `from langgraph.types import interrupt, Command`
- `from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver`
- `AsyncSqliteSaver.from_conn_string(path)` is an **async context manager**
- First `ainvoke` returns a dict containing `__interrupt__` → `list[Interrupt]`, each with `.value`, `.id`, `.interrupt_id`, `.from_ns`
- `await app.aget_state(cfg)` → `.next` (tuple), `.interrupts` (list), `.values` (dict)
- Resume with `await app.ainvoke(Command(resume=payload), cfg)`

**Hard design rule discovered by probe (Task 3 re-verifies it):** when a node calls `interrupt()`, the node body **re-executes from the top on resume**. Side effects placed before the `interrupt()` call therefore run **twice**, while state writes from the aborted pass are **discarded**. Consequence: **a node that interrupts must perform no LLM call, no HTTP request, and no file write before its `interrupt()` call.** All payload computation belongs in the preceding node.

**Verified pydantic-settings 2.15.0 behaviour** (relied on by Tasks 1 and 12):
- Complex-typed fields (`tuple`, `frozenset`, `list`) have their env values **JSON-decoded before field validators run**. A comma-separated string therefore raises `SettingsError` unless the field is annotated `Annotated[T, NoDecode]`. All collection settings use `NoDecode`.
- An explicit `Field(alias=...)` **bypasses `env_prefix`**: `openai_api_key` is read from `OPENAI_API_KEY`, never `UP_OPENAI_API_KEY`.
- Explicit init kwargs take priority over env and `.env`, which is what lets tests build a `Settings` by hand.

**Code rules (from CLAUDE.md)**
- `services/` must not import LangGraph. `graph/` must not import FastAPI. `models/` imports neither.
- Never `except: pass`. A caught exception produces an `AppError` and a trace event.
- Derived values (`UsageSummary`, `RunStatus`) are computed, never stored.
- Prefer typed models over dicts. `dict[str, Any]` in a signature needs justification.
- Unit tests touch no network and no LLM.

**Commit after every task.** Run `ruff check` and `mypy` before each commit.

---

# Phase 0 — Environment and architecture validation

## File Structure (Phase 0)

| File | Responsibility |
|---|---|
| `backend/pyproject.toml` | Package metadata, exact pins, pytest/ruff/mypy config |
| `backend/.env.example` | Documented environment contract (committed) |
| `backend/src/upgradepilot/__init__.py` | Package marker, version string |
| `backend/src/upgradepilot/config.py` | `Settings` via pydantic-settings — the only place env vars are read |
| `backend/src/upgradepilot/api/app.py` | FastAPI app factory + CORS |
| `backend/src/upgradepilot/api/routes/health.py` | `GET /api/health` |
| `backend/tests/api/test_health.py` | Health contract test |
| `backend/probes/probe_langgraph.py` | Interrupt/resume, thread isolation, side-effect rule |
| `backend/probes/probe_chroma.py` | Persistence across restart, scalar metadata filtering |
| `backend/probes/probe_llm.py` | Usage-metadata surface, structured output + usage |
| `frontend/` | Vite scaffold |

---

### Task 1: Backend scaffold, settings, and health endpoint

**Files:**
- Create: `backend/pyproject.toml`
- Create: `backend/.env.example`
- Create: `backend/src/upgradepilot/__init__.py`
- Create: `backend/src/upgradepilot/config.py`
- Create: `backend/src/upgradepilot/api/__init__.py`
- Create: `backend/src/upgradepilot/api/app.py`
- Create: `backend/src/upgradepilot/api/routes/__init__.py`
- Create: `backend/src/upgradepilot/api/routes/health.py`
- Test: `backend/tests/api/test_health.py`

**Interfaces:**
- Consumes: nothing (first task).
- Produces:
  - `upgradepilot.config.Settings` — pydantic-settings model; fields listed in the code below.
  - `upgradepilot.config.get_settings() -> Settings` — cached accessor, used by every later task instead of reading env directly.
  - `upgradepilot.api.app.create_app() -> FastAPI` — app factory used by Task 2's dev server and all later API tests.
  - `upgradepilot.__version__ : str`

- [ ] **Step 1: Create the virtualenv and package skeleton**

```bash
cd /Users/nzrsrd/Code/upgrade_pilot
mkdir -p backend/src/upgradepilot/api/routes backend/tests/api backend/probes
python3 -m venv backend/.venv
backend/.venv/bin/python -m pip install --quiet --upgrade pip
```

- [ ] **Step 2: Write `backend/pyproject.toml`**

```toml
[build-system]
requires = ["setuptools>=77"]
build-backend = "setuptools.build_meta"

[project]
name = "upgradepilot"
version = "0.1.0"
description = "Dependency upgrade risk and migration planning"
requires-python = ">=3.12"
dependencies = [
    "fastapi==0.141.1",
    "uvicorn==0.52.4",
    "langgraph==1.2.11",
    "langgraph-checkpoint-sqlite==3.1.1",
    "langchain-core==1.6.0",
    "langchain-openai==1.6.0",
    "openai==3.3.1",
    "chromadb==1.5.9",
    "pydantic==2.13.4",
    "pydantic-settings==2.15.0",
    "tiktoken==0.14.0",
]

[project.optional-dependencies]
dev = [
    "pytest==9.1.1",
    "pytest-asyncio==1.4.0",
    "pytest-cov==7.1.0",
    "httpx==0.28.1",
    "ruff==0.16.4",
    "mypy==2.3.1",
]

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
markers = [
    "live: makes a real network call; skipped unless --live is passed",
]
addopts = "-q"

[tool.ruff]
line-length = 100
src = ["src", "tests"]

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM", "TID"]

[tool.mypy]
python_version = "3.12"
strict = true
files = ["src/upgradepilot/models", "src/upgradepilot/services"]
```

- [ ] **Step 3: Write `backend/.env.example`**

```bash
# OpenAI — required for Task 5 probes and all LLM work
OPENAI_API_KEY=sk-replace-me
UP_CHAT_MODEL=gpt-4.1-mini
UP_EMBEDDING_MODEL=text-embedding-3-small

# Local stores
UP_CHROMA_DIR=./.chroma
UP_CHECKPOINT_DB=./checkpoints.db

# Repository access guards
UP_ALLOWED_LOCAL_ROOTS=/Users/nzrsrd/Code
UP_ALLOWED_URL_SCHEMES=https,git
UP_MAX_REPO_FILES=5000
UP_MAX_REPO_BYTES=52428800
UP_CLONE_DEPTH=100
UP_WORKSPACE_DIR=./.workspaces

# Graph and run limits
UP_MAX_RAG_ITERATIONS=3
UP_MAX_CONCURRENT_RUNS=4

# API
UP_CORS_ORIGINS=http://localhost:5173
```

- [ ] **Step 4: Write the failing test `backend/tests/api/test_health.py`**

```python
from fastapi.testclient import TestClient

from upgradepilot.api.app import create_app


def test_health_reports_ok_and_checks() -> None:
    client = TestClient(create_app())
    response = client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert set(body["checks"]) == {"chroma_dir", "checkpoint_dir", "openai_configured"}
    assert isinstance(body["version"], str) and body["version"]


def test_health_does_not_require_an_api_key(monkeypatch) -> None:
    """A health probe must never depend on, or spend money at, OpenAI."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    from upgradepilot.config import get_settings

    get_settings.cache_clear()
    client = TestClient(create_app())
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["checks"]["openai_configured"] is False
    get_settings.cache_clear()
```

- [ ] **Step 4b: Write `backend/tests/unit/test_settings.py`**

These pin three pydantic-settings behaviours the rest of the plan relies on. All three were verified against 2.15.0; without the `NoDecode` annotation the first test raises `SettingsError`.

```python
from pathlib import Path

from upgradepilot.config import Settings


def test_comma_separated_env_values_parse_into_collections(monkeypatch) -> None:
    """Complex-typed env values are JSON-decoded unless NoDecode is set."""
    monkeypatch.setenv("UP_ALLOWED_LOCAL_ROOTS", "/tmp/a,/tmp/b")
    monkeypatch.setenv("UP_ALLOWED_URL_SCHEMES", "https,git")
    monkeypatch.setenv("UP_CORS_ORIGINS", "http://localhost:5173")

    settings = Settings(_env_file=None)

    assert settings.allowed_local_roots == (Path("/tmp/a"), Path("/tmp/b"))
    assert settings.allowed_url_schemes == frozenset({"https", "git"})
    assert settings.cors_origins == ("http://localhost:5173",)


def test_api_key_is_read_without_the_up_prefix(monkeypatch) -> None:
    """An explicit alias bypasses env_prefix."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-unprefixed")
    monkeypatch.delenv("UP_OPENAI_API_KEY", raising=False)
    assert Settings(_env_file=None).openai_configured is True

    monkeypatch.delenv("OPENAI_API_KEY")
    monkeypatch.setenv("UP_OPENAI_API_KEY", "sk-prefixed")
    assert Settings(_env_file=None).openai_configured is False


def test_explicit_kwargs_override_the_environment(monkeypatch) -> None:
    """Relied on by every test that builds a Settings by hand."""
    monkeypatch.setenv("UP_MAX_REPO_FILES", "7")
    assert Settings(_env_file=None).max_repo_files == 7
    assert Settings(_env_file=None, max_repo_files=99).max_repo_files == 99
```

- [ ] **Step 5: Run the test to verify it fails**

Run from `backend/`: `.venv/bin/python -m pytest tests/ -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'upgradepilot'` (nothing is installed or written yet). Create `tests/unit/` and `tests/api/` with `__init__.py` files first.

- [ ] **Step 6: Install the package and dev dependencies**

```bash
cd /Users/nzrsrd/Code/upgrade_pilot/backend
.venv/bin/python -m pip install -q -e ".[dev]"
.venv/bin/python -c "import fastapi, langgraph, chromadb; print('imports ok')"
```

Expected: `imports ok`. If any wheel fails to build, stop and report — the pins in Global Constraints were verified installable, so a failure means the environment changed.

- [ ] **Step 7: Write `backend/src/upgradepilot/__init__.py`**

```python
__version__ = "0.1.0"
```

- [ ] **Step 8: Write `backend/src/upgradepilot/config.py`**

```python
"""Application configuration. The only place environment variables are read."""

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="UP_",
        extra="ignore",
    )

    # OpenAI. An explicit alias bypasses env_prefix entirely, so this is read
    # from OPENAI_API_KEY and *not* from UP_OPENAI_API_KEY. Verified.
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    chat_model: str = "gpt-4.1-mini"
    embedding_model: str = "text-embedding-3-small"

    # Local stores
    chroma_dir: Path = Path("./.chroma")
    checkpoint_db: Path = Path("./checkpoints.db")
    workspace_dir: Path = Path("./.workspaces")

    # Repository access guards.
    # NoDecode is required: pydantic-settings JSON-decodes complex-typed env
    # values *before* field validators run, so a comma-separated string would
    # raise SettingsError. NoDecode disables that decode and lets _split_csv
    # handle the value. Verified against pydantic-settings 2.15.0.
    allowed_local_roots: Annotated[tuple[Path, ...], NoDecode] = ()
    allowed_url_schemes: Annotated[frozenset[str], NoDecode] = frozenset({"https", "git"})
    max_repo_files: int = 5000
    max_repo_bytes: int = 50 * 1024 * 1024
    clone_depth: int = 100

    # Graph and run limits
    max_rag_iterations: int = 3
    max_concurrent_runs: int = 4

    # API
    cors_origins: Annotated[tuple[str, ...], NoDecode] = ("http://localhost:5173",)

    @field_validator("allowed_local_roots", "allowed_url_schemes", "cors_origins", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        """Accept comma-separated strings from .env for collection fields."""
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    @property
    def openai_configured(self) -> bool:
        return bool(self.openai_api_key)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 9: Write `backend/src/upgradepilot/api/routes/health.py`**

```python
from fastapi import APIRouter
from pydantic import BaseModel

from upgradepilot import __version__
from upgradepilot.config import get_settings

router = APIRouter()


class HealthChecks(BaseModel):
    chroma_dir: bool
    checkpoint_dir: bool
    openai_configured: bool


class HealthResponse(BaseModel):
    status: str
    version: str
    checks: HealthChecks


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Liveness plus local-store readiness.

    Deliberately does not call OpenAI: a health probe must not cost money
    or inherit third-party latency.
    """
    settings = get_settings()
    checks = HealthChecks(
        chroma_dir=settings.chroma_dir.parent.exists(),
        checkpoint_dir=settings.checkpoint_db.parent.exists(),
        openai_configured=settings.openai_configured,
    )
    return HealthResponse(status="ok", version=__version__, checks=checks)
```

- [ ] **Step 10: Write `backend/src/upgradepilot/api/app.py`**

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from upgradepilot import __version__
from upgradepilot.api.routes import health
from upgradepilot.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="UpgradePilot", version=__version__)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    app.include_router(health.router, prefix="/api")
    return app


app = create_app()
```

Also create empty `backend/src/upgradepilot/api/__init__.py` and `backend/src/upgradepilot/api/routes/__init__.py`.

- [ ] **Step 11: Run the tests to verify they pass**

Run from `backend/`: `.venv/bin/python -m pytest tests/ -v`
Expected: 5 passed (2 health + 3 settings).

- [ ] **Step 12: Verify the server actually starts**

```bash
cd /Users/nzrsrd/Code/upgrade_pilot/backend
cp -n .env.example .env
.venv/bin/uvicorn upgradepilot.api.app:app --port 8000 &
sleep 3
curl -s http://localhost:8000/api/health | python3 -m json.tool
kill %1
```

Expected: JSON with `"status": "ok"`. `openai_configured` will be `false` until a real key is placed in `.env`.

- [ ] **Step 13: Lint, typecheck, and commit**

```bash
cd /Users/nzrsrd/Code/upgrade_pilot/backend
.venv/bin/ruff check src tests
.venv/bin/ruff format --check src tests || .venv/bin/ruff format src tests
.venv/bin/mypy
cd /Users/nzrsrd/Code/upgrade_pilot
git add backend/ && git commit -m "feat(backend): scaffold FastAPI app, settings, and health endpoint

Pins verified installable on Python 3.14.5 with wheels only.
Health check deliberately omits any OpenAI call."
```

---

### Task 2: Frontend scaffold with Tailwind v4

**Files:**
- Create: `frontend/package.json`, `frontend/vite.config.ts`, `frontend/tsconfig.json`
- Create: `frontend/index.html`
- Create: `frontend/src/main.tsx`, `frontend/src/App.tsx`, `frontend/src/index.css`

**Interfaces:**
- Consumes: Task 1's running backend at `http://localhost:8000` (proxied).
- Produces: a dev server on `:5173` proxying `/api` to the backend, and the semantic risk color tokens (`--color-risk-high`, `--color-risk-medium`, `--color-risk-low`, `--color-pending-input`) that every later view uses via `text-risk-high`, `bg-pending-input`, etc.

- [ ] **Step 1: Scaffold with Vite**

```bash
cd /Users/nzrsrd/Code/upgrade_pilot
npm create vite@latest frontend -- --template react-ts
cd frontend
npm install
```

- [ ] **Step 2: Install pinned dependencies**

```bash
cd /Users/nzrsrd/Code/upgrade_pilot/frontend
npm install react@19.2.8 react-dom@19.2.8 lucide-react@1.34.0
npm install -D vite@8.2.2 typescript@7.0.2 @vitejs/plugin-react@6.1.0 \
  tailwindcss@4.3.3 @tailwindcss/vite@4.3.3 vitest@4.1.11 openapi-typescript@7.13.0
```

- [ ] **Step 3: Write `frontend/vite.config.ts`**

Tailwind v4 is wired as a **Vite plugin**. There is no PostCSS config and no `tailwind.config.js`.

```ts
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://localhost:8000", changeOrigin: true },
    },
  },
});
```

- [ ] **Step 4: Write `frontend/src/index.css` with the theme tokens**

```css
@import "tailwindcss";

@theme {
  --color-risk-high: oklch(0.58 0.20 25);
  --color-risk-medium: oklch(0.75 0.16 75);
  --color-risk-low: oklch(0.68 0.15 150);
  --color-pending-input: oklch(0.80 0.15 90);
  --color-surface: oklch(0.99 0 0);
  --color-surface-sunken: oklch(0.96 0 0);
}

@media (prefers-color-scheme: dark) {
  @theme {
    --color-surface: oklch(0.20 0.01 260);
    --color-surface-sunken: oklch(0.16 0.01 260);
  }
}
```

- [ ] **Step 5: Write `frontend/src/App.tsx` as a smoke test of the whole chain**

```tsx
import { useEffect, useState } from "react";
import { ShieldAlert, CheckCircle } from "lucide-react";

type Health = { status: string; version: string };

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/health")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then(setHealth)
      .catch((e: Error) => setError(e.message));
  }, []);

  return (
    <main className="min-h-screen bg-surface p-8 font-sans">
      <h1 className="text-2xl font-semibold">UpgradePilot</h1>
      <p className="mt-1 text-sm opacity-70">Dependency upgrade risk agent</p>

      <div className="mt-6 flex items-center gap-2 rounded-lg bg-surface-sunken p-4">
        {error ? (
          <>
            <ShieldAlert className="size-5 text-risk-high" />
            <span>Backend unreachable: {error}</span>
          </>
        ) : health ? (
          <>
            <CheckCircle className="size-5 text-risk-low" />
            <span>
              Backend {health.status} · v{health.version}
            </span>
          </>
        ) : (
          <span className="opacity-60">Checking backend…</span>
        )}
      </div>
    </main>
  );
}
```

- [ ] **Step 6: Ensure `frontend/src/main.tsx` imports the stylesheet**

```tsx
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./index.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
```

- [ ] **Step 7: Verify the build and the live chain**

```bash
cd /Users/nzrsrd/Code/upgrade_pilot/frontend
npx tsc --noEmit
npm run build
```

Expected: typecheck clean, build succeeds.

Then, with the backend running from Task 1 Step 12, run `npm run dev` and load `http://localhost:5173`. Expected: a green check reading "Backend ok · v0.1.0". This proves Vite, React, Tailwind v4, Lucide, and the API proxy all work together.

- [ ] **Step 8: Commit**

```bash
cd /Users/nzrsrd/Code/upgrade_pilot
git add frontend/ && git commit -m "feat(frontend): scaffold Vite + React 19 + Tailwind 4 with API proxy

Tailwind v4 CSS-first: no tailwind.config.js, no postcss.config.js.
Semantic risk tokens defined in @theme for later views."
```

---

### Task 3: LangGraph probe — interrupt, resume, isolation, and the side-effect rule

This probe is committed, not throwaway. It is the executable evidence behind ADR-001's verification table and it guards the design rule in Global Constraints.

**Files:**
- Create: `backend/probes/probe_langgraph.py`
- Test: `backend/tests/graph/test_langgraph_contract.py`

**Interfaces:**
- Consumes: `langgraph==1.2.11`, `langgraph-checkpoint-sqlite==3.1.1`.
- Produces: `backend/tests/graph/test_langgraph_contract.py` — a regression test that fails loudly if a LangGraph upgrade changes interrupt/resume semantics. Later graph tasks depend on the API shape it locks in.

- [ ] **Step 1: Write the failing contract test `backend/tests/graph/test_langgraph_contract.py`**

```python
"""Locks the LangGraph 1.x interrupt/resume contract the design depends on.

If a LangGraph upgrade changes these semantics, this test fails and the
design rules in the plan's Global Constraints must be re-derived.
"""

import operator
from typing import Annotated, TypedDict

import pytest
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt


class DemoState(TypedDict):
    trace: Annotated[list[str], operator.add]
    decision: str | None


def _build_graph(side_effects: list[str]) -> StateGraph:
    def first(_state: DemoState) -> dict:
        return {"trace": ["first"]}

    def review(_state: DemoState) -> dict:
        # Stands in for a billed LLM call placed before interrupt().
        side_effects.append("billed_work")
        answer = interrupt({"question": "pick one", "options": ["a", "b"]})
        return {"trace": ["review"], "decision": answer["selected"]}

    def last(state: DemoState) -> dict:
        return {"trace": [f"last:{state['decision']}"]}

    graph = StateGraph(DemoState)
    graph.add_node("first", first)
    graph.add_node("review", review)
    graph.add_node("last", last)
    graph.add_edge(START, "first")
    graph.add_edge("first", "review")
    graph.add_edge("review", "last")
    graph.add_edge("last", END)
    return graph


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "ckpt.sqlite")


async def test_interrupt_exposes_payload_and_pauses(db_path):
    side_effects: list[str] = []
    async with AsyncSqliteSaver.from_conn_string(db_path) as saver:
        app = _build_graph(side_effects).compile(checkpointer=saver)
        config = {"configurable": {"thread_id": "t1"}}

        result = await app.ainvoke({"trace": [], "decision": None}, config)

        assert "__interrupt__" in result
        assert result["__interrupt__"][0].value == {
            "question": "pick one",
            "options": ["a", "b"],
        }

        state = await app.aget_state(config)
        assert state.next == ("review",)
        assert len(state.interrupts) == 1


async def test_resume_continues_same_thread_and_applies_decision(db_path):
    side_effects: list[str] = []
    async with AsyncSqliteSaver.from_conn_string(db_path) as saver:
        app = _build_graph(side_effects).compile(checkpointer=saver)
        config = {"configurable": {"thread_id": "t1"}}

        await app.ainvoke({"trace": [], "decision": None}, config)
        resumed = await app.ainvoke(Command(resume={"selected": "b"}), config)

        assert resumed["trace"] == ["first", "review", "last:b"]
        assert resumed["decision"] == "b"

        state = await app.aget_state(config)
        assert state.next == ()
        assert len(state.interrupts) == 0


async def test_side_effects_before_interrupt_run_twice_but_writes_do_not(db_path):
    """The reason a node that interrupts must do no LLM work before interrupt().

    The node body re-executes on resume, so pre-interrupt side effects are
    billed twice, while the aborted pass's state writes are discarded.
    """
    side_effects: list[str] = []
    async with AsyncSqliteSaver.from_conn_string(db_path) as saver:
        app = _build_graph(side_effects).compile(checkpointer=saver)
        config = {"configurable": {"thread_id": "t1"}}

        await app.ainvoke({"trace": [], "decision": None}, config)
        assert side_effects == ["billed_work"]

        resumed = await app.ainvoke(Command(resume={"selected": "a"}), config)

        assert side_effects == ["billed_work", "billed_work"], "side effect ran twice"
        assert resumed["trace"].count("review") == 1, "aborted pass wrote no state"


async def test_threads_are_isolated(db_path):
    side_effects: list[str] = []
    async with AsyncSqliteSaver.from_conn_string(db_path) as saver:
        app = _build_graph(side_effects).compile(checkpointer=saver)
        first_cfg = {"configurable": {"thread_id": "t1"}}
        second_cfg = {"configurable": {"thread_id": "t2"}}

        await app.ainvoke({"trace": [], "decision": None}, first_cfg)
        await app.ainvoke(Command(resume={"selected": "a"}), first_cfg)
        await app.ainvoke({"trace": [], "decision": None}, second_cfg)

        first_state = await app.aget_state(first_cfg)
        second_state = await app.aget_state(second_cfg)

        assert first_state.values["trace"] == ["first", "review", "last:a"]
        assert second_state.values["trace"] == ["first"]
        assert second_state.next == ("review",)


async def test_state_survives_a_new_saver_instance(db_path):
    """Checkpoint durability: a fresh connection sees the interrupted state."""
    side_effects: list[str] = []
    config = {"configurable": {"thread_id": "t1"}}

    async with AsyncSqliteSaver.from_conn_string(db_path) as saver:
        app = _build_graph(side_effects).compile(checkpointer=saver)
        await app.ainvoke({"trace": [], "decision": None}, config)

    async with AsyncSqliteSaver.from_conn_string(db_path) as saver:
        app = _build_graph(side_effects).compile(checkpointer=saver)
        state = await app.aget_state(config)
        assert state.next == ("review",)

        resumed = await app.ainvoke(Command(resume={"selected": "b"}), config)
        assert resumed["decision"] == "b"
```

- [ ] **Step 2: Run the test to verify it passes**

```bash
cd /Users/nzrsrd/Code/upgrade_pilot/backend
mkdir -p tests/graph && touch tests/graph/__init__.py
.venv/bin/python -m pytest tests/graph/test_langgraph_contract.py -v
```

Expected: 5 passed. This test is written to pass immediately — it documents an external contract rather than driving new code, so there is no red phase. If any assertion fails, **stop**: the pinned LangGraph behaves differently from what the design assumes, and ADR-001 plus the Global Constraints rule must be amended before continuing.

- [ ] **Step 3: Write `backend/probes/probe_langgraph.py` as the human-runnable version**

```python
"""Phase 0 probe: LangGraph interrupt/resume contract.

Run: backend/.venv/bin/python probes/probe_langgraph.py
Records the findings that go into ADR-001's verification table.
"""

import asyncio
import operator
import tempfile
from pathlib import Path
from typing import Annotated, TypedDict

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

SIDE_EFFECTS: list[str] = []


class State(TypedDict):
    trace: Annotated[list[str], operator.add]
    decision: str | None


def review(_state: State) -> dict:
    SIDE_EFFECTS.append("billed_work")
    answer = interrupt({"question": "pick one", "options": ["a", "b"]})
    return {"trace": ["review"], "decision": answer["selected"]}


def build() -> StateGraph:
    graph = StateGraph(State)
    graph.add_node("review", review)
    graph.add_edge(START, "review")
    graph.add_edge("review", END)
    return graph


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "probe.sqlite")
        async with AsyncSqliteSaver.from_conn_string(db) as saver:
            app = build().compile(checkpointer=saver)
            config = {"configurable": {"thread_id": "probe"}}

            first = await app.ainvoke({"trace": [], "decision": None}, config)
            print(f"interrupt payload      : {first['__interrupt__'][0].value}")

            state = await app.aget_state(config)
            print(f"paused at              : {state.next}")
            print(f"side effects (pass 1)  : {len(SIDE_EFFECTS)}")

            resumed = await app.ainvoke(Command(resume={"selected": "b"}), config)
            print(f"side effects (resumed) : {len(SIDE_EFFECTS)}  <- twice: no LLM before interrupt()")
            print(f"state writes           : {resumed['trace']}  <- once: aborted pass discarded")
            print(f"decision applied       : {resumed['decision']}")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 4: Run the probe and capture its output**

```bash
cd /Users/nzrsrd/Code/upgrade_pilot/backend
.venv/bin/python probes/probe_langgraph.py
```

Expected output shape:

```
interrupt payload      : {'question': 'pick one', 'options': ['a', 'b']}
paused at              : ('review',)
side effects (pass 1)  : 1
side effects (resumed) : 2  <- twice: no LLM before interrupt()
state writes           : ['review']  <- once: aborted pass discarded
decision applied       : b
```

Save this output — Task 6 pastes it into ADR-001.

- [ ] **Step 5: Commit**

```bash
cd /Users/nzrsrd/Code/upgrade_pilot
git add backend/probes/probe_langgraph.py backend/tests/graph/
git commit -m "test(graph): lock LangGraph 1.x interrupt/resume contract

Contract test plus runnable probe. Proves the design rule that a node
which interrupts must do no billed work before its interrupt() call:
pre-interrupt side effects run twice, aborted-pass writes are discarded."
```

---

### Task 4: ChromaDB probe — persistence and metadata filtering

> **Superseded by what this task actually found — read this before reusing anything below.** This task was planned on the assumption that list-valued metadata is *rejected* by ChromaDB, and therefore that symbol matching had to be a post-retrieval Python re-rank over a delimited string. **Running the probe refuted that.** Against the pinned `chromadb==1.5.9`, list-valued metadata is accepted and round-trips as a real `list`; `$contains` matches its elements exactly (`Config` does not match `ConfigDict`); and `$in` silently returns nothing and must never be used. So the symbol join is a database predicate, not a Python post-pass, and `affected_symbols` is stored as a real list, not a delimited string. The corrected design is spec §7.2 and §4 correction 4; the executed test is `backend/tests/knowledge/test_chroma_contract.py`, which asserts the opposite of the code sketched in the steps below. The step text and code blocks are left as the historical record of what was planned — they are **not** the design, and the delimited-string/re-rank approach in them must not be revived by a later phase.

Spec §7.2 depends on two ChromaDB facts: a persistent client survives process restart, and metadata filtering behaves as the retrieval design assumes. Both are verified here rather than assumed — and the second came back different from the assumption, which is what the probe was for.

**Files:**
- Create: `backend/probes/probe_chroma.py`
- Test: `backend/tests/knowledge/test_chroma_contract.py`

**Interfaces:**
- Consumes: `chromadb==1.5.9`.
- Produces: `fake_embedding_function()` in the test module — a deterministic, network-free embedding function that **Phase 3 will import for all knowledge-base tests**. Signature: a callable accepting `list[str]` and returning `list[list[float]]` of dimension 16.

- [ ] **Step 1: Write the failing contract test `backend/tests/knowledge/test_chroma_contract.py`**

```python
"""Locks the ChromaDB facts spec §7.2 depends on.

1. A PersistentClient survives process restart (simulated by a new client
   over the same directory).
2. Scalar metadata filters work.
3. List-valued metadata is rejected — which is *why* symbol matching is
   post-retrieval re-ranking rather than a database predicate.
   [SUPERSEDED: false. List-valued metadata is accepted; the join is a
   `$contains` where-clause. See the banner at the top of this task.]
"""

import hashlib

import chromadb
import pytest
from chromadb.api.types import Documents, EmbeddingFunction, Embeddings

DIM = 16


class DeterministicEmbedding(EmbeddingFunction[Documents]):
    """Hash-based embeddings: stable, offline, and good enough to rank exact repeats first."""

    def __call__(self, input: Documents) -> Embeddings:
        vectors = []
        for text in input:
            digest = hashlib.sha256(text.lower().encode()).digest()
            vectors.append([digest[i] / 255.0 for i in range(DIM)])
        return vectors

    @staticmethod
    def name() -> str:
        return "deterministic-test-embedding"


def fake_embedding_function() -> DeterministicEmbedding:
    """Shared by every knowledge-base test from Phase 3 onward."""
    return DeterministicEmbedding()


DOCS = [
    (
        "pydantic-v2#validator",
        "@validator was replaced by @field_validator in Pydantic v2.",
        {"dependency": "pydantic", "to_version_major": 2, "source_type": "migration_guide",
         "symbols": "|validator|root_validator|"},
    ),
    (
        "pydantic-v2#config",
        "class Config was replaced by model_config = ConfigDict(...).",
        {"dependency": "pydantic", "to_version_major": 2, "source_type": "migration_guide",
         "symbols": "|Config|"},
    ),
    (
        "sqlalchemy-2#select",
        "Legacy Query API gives way to select() in SQLAlchemy 2.0.",
        {"dependency": "sqlalchemy", "to_version_major": 2, "source_type": "changelog",
         "symbols": "|Query|"},
    ),
]


def _seed(client) -> None:
    collection = client.get_or_create_collection(
        "migrations", embedding_function=fake_embedding_function()
    )
    collection.add(
        ids=[d[0] for d in DOCS],
        documents=[d[1] for d in DOCS],
        metadatas=[d[2] for d in DOCS],
    )


def test_persistent_client_survives_restart(tmp_path):
    path = str(tmp_path / "chroma")
    _seed(chromadb.PersistentClient(path=path))

    reopened = chromadb.PersistentClient(path=path)
    collection = reopened.get_collection(
        "migrations", embedding_function=fake_embedding_function()
    )
    assert collection.count() == 3


def test_scalar_metadata_filter_narrows_results(tmp_path):
    client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
    _seed(client)
    collection = client.get_collection(
        "migrations", embedding_function=fake_embedding_function()
    )

    result = collection.query(
        query_texts=["validator renamed"],
        n_results=3,
        where={"dependency": "pydantic"},
    )
    returned_ids = result["ids"][0]

    assert returned_ids, "filter returned nothing"
    assert all(i.startswith("pydantic-v2#") for i in returned_ids)
    assert "sqlalchemy-2#select" not in returned_ids


def test_source_metadata_round_trips(tmp_path):
    client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
    _seed(client)
    collection = client.get_collection(
        "migrations", embedding_function=fake_embedding_function()
    )

    result = collection.get(ids=["pydantic-v2#validator"])
    metadata = result["metadatas"][0]

    assert metadata["source_type"] == "migration_guide"
    assert metadata["to_version_major"] == 2
    # Symbols travel as a delimited string, parsed after retrieval.
    # [SUPERSEDED: they travel as a real list. See this task's banner.]
    assert [s for s in metadata["symbols"].split("|") if s] == ["validator", "root_validator"]


def test_list_valued_metadata_is_rejected(tmp_path):
    """The constraint behind spec §7.2: symbol matching cannot be a where-clause."""
    client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
    collection = client.get_or_create_collection(
        "migrations", embedding_function=fake_embedding_function()
    )

    with pytest.raises(Exception) as excinfo:
        collection.add(
            ids=["bad"],
            documents=["list metadata attempt"],
            metadatas=[{"affected_symbols": ["validator", "Config"]}],
        )
    assert excinfo.value is not None
```

- [ ] **Step 2: Run the test**

```bash
cd /Users/nzrsrd/Code/upgrade_pilot/backend
mkdir -p tests/knowledge && touch tests/knowledge/__init__.py
.venv/bin/python -m pytest tests/knowledge/test_chroma_contract.py -v
```

Expected: 5 passed. As in Task 3 there is no red phase — this documents an external contract.

If `test_list_valued_metadata_is_rejected` **fails** because Chroma 1.5.9 now accepts lists, that is good news but it changes the design: report it, and spec §7.2 may be simplified to use a real metadata predicate. Do not silently adapt.

- [ ] **Step 3: Write `backend/probes/probe_chroma.py`**

```python
"""Phase 0 probe: ChromaDB persistence and metadata filtering.

Run: backend/.venv/bin/python probes/probe_chroma.py
"""

import hashlib
import tempfile
from pathlib import Path

import chromadb
from chromadb.api.types import Documents, EmbeddingFunction, Embeddings


class DeterministicEmbedding(EmbeddingFunction[Documents]):
    def __call__(self, input: Documents) -> Embeddings:
        return [
            [hashlib.sha256(t.lower().encode()).digest()[i] / 255.0 for i in range(16)]
            for t in input
        ]

    @staticmethod
    def name() -> str:
        return "deterministic-probe-embedding"


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / "chroma")
        ef = DeterministicEmbedding()

        client = chromadb.PersistentClient(path=path)
        collection = client.get_or_create_collection("probe", embedding_function=ef)
        collection.add(
            ids=["a", "b"],
            documents=["validator renamed to field_validator", "Query API replaced by select"],
            metadatas=[
                {"dependency": "pydantic", "to_version_major": 2, "symbols": "|validator|"},
                {"dependency": "sqlalchemy", "to_version_major": 2, "symbols": "|Query|"},
            ],
        )
        print(f"seeded                 : {collection.count()} documents")

        reopened = chromadb.PersistentClient(path=path).get_collection("probe", embedding_function=ef)
        print(f"survives restart       : {reopened.count()} documents")

        filtered = reopened.query(
            query_texts=["validator"], n_results=2, where={"dependency": "pydantic"}
        )
        print(f"scalar filter result   : {filtered['ids'][0]}")

        try:
            reopened.add(ids=["c"], documents=["x"], metadatas=[{"symbols": ["a", "b"]}])
            print("list metadata          : ACCEPTED (design may be simplified)")
        except Exception as exc:  # noqa: BLE001 - probe reports the class deliberately
            print(f"list metadata          : REJECTED ({type(exc).__name__})")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the probe and save its output for ADR-001**

```bash
cd /Users/nzrsrd/Code/upgrade_pilot/backend
.venv/bin/python probes/probe_chroma.py
```

- [ ] **Step 5: Commit**

```bash
cd /Users/nzrsrd/Code/upgrade_pilot
git add backend/probes/probe_chroma.py backend/tests/knowledge/
git commit -m "test(knowledge): lock ChromaDB persistence and metadata contract

Confirms scalar filters work and list metadata is rejected, which is why
symbol matching is post-retrieval re-ranking. Adds the deterministic
offline embedding function Phase 3 tests will share."
# [SUPERSEDED: the probe refuted this; the committed message differs.
#  See this task's banner.]
```

---

### Task 5: LLM probe — usage metadata and structured output

**Blocked until `OPENAI_API_KEY` is present in `backend/.env`.** It was not in the environment when this plan was written. This is the only task in Phase 0 that spends money — well under one cent.

**Files:**
- Create: `backend/probes/probe_llm.py`
- Test: `backend/tests/llm/test_usage_metadata_live.py`

**Interfaces:**
- Consumes: `langchain-openai==1.6.0`, `langchain-core==1.6.0`, `upgradepilot.config.get_settings`.
- Produces: the empirical answer to which usage surface is populated, which **Phase 4's `TrackedLLM` extractor depends on**. Also produces the `--live` pytest option and the `live` marker skip behaviour used by every later live test.

- [ ] **Step 1: Add the `--live` option in `backend/tests/conftest.py`**

```python
import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--live",
        action="store_true",
        default=False,
        help="run tests that make real network calls",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--live"):
        return
    skip = pytest.mark.skip(reason="needs --live and a real OPENAI_API_KEY")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)
```

- [ ] **Step 2: Write `backend/tests/llm/test_usage_metadata_live.py`**

```python
"""One real call, asserting the usage surface Phase 4's TrackedLLM will read.

Every other token-tracking test uses a fake model with synthetic usage
metadata, so all of them can pass while the real extractor is broken and
the counter reads zero. This test is the only thing that closes that gap.
"""

import pytest
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from upgradepilot.config import get_settings

pytestmark = pytest.mark.live


class Verdict(BaseModel):
    sufficient: bool = Field(description="whether the evidence suffices")
    reason: str


@pytest.fixture
def model() -> ChatOpenAI:
    settings = get_settings()
    if not settings.openai_configured:
        pytest.skip("OPENAI_API_KEY not configured")
    return ChatOpenAI(model=settings.chat_model, api_key=settings.openai_api_key, temperature=0)


def test_plain_invoke_populates_usage_metadata(model: ChatOpenAI) -> None:
    message = model.invoke("Reply with the single word: ok")

    assert message.usage_metadata is not None, "usage_metadata absent; fallback path required"
    assert message.usage_metadata["input_tokens"] > 0
    assert message.usage_metadata["output_tokens"] > 0
    assert message.usage_metadata["total_tokens"] == (
        message.usage_metadata["input_tokens"] + message.usage_metadata["output_tokens"]
    )


def test_structured_output_with_include_raw_preserves_usage(model: ChatOpenAI) -> None:
    """The hazard in spec §9.4: structured output can swallow the raw message."""
    structured = model.with_structured_output(Verdict, include_raw=True)
    result = structured.invoke("Is one sentence enough evidence for a migration? Answer briefly.")

    assert set(result) >= {"raw", "parsed"}
    assert isinstance(result["parsed"], Verdict)

    usage = result["raw"].usage_metadata
    assert usage is not None, "include_raw did not preserve usage metadata"
    assert usage["input_tokens"] > 0 and usage["output_tokens"] > 0
```

- [ ] **Step 3: Verify the test is skipped without `--live`**

```bash
cd /Users/nzrsrd/Code/upgrade_pilot/backend
mkdir -p tests/llm && touch tests/llm/__init__.py
.venv/bin/python -m pytest tests/llm/ -v
```

Expected: 2 skipped. This confirms the marker plumbing works before any money is spent.

- [ ] **Step 4: Put a real key in `.env`, then run live**

```bash
cd /Users/nzrsrd/Code/upgrade_pilot/backend
grep -q '^OPENAI_API_KEY=sk-replace-me$' .env && echo "EDIT .env FIRST — key is still the placeholder"
.venv/bin/python -m pytest tests/llm/ -v --live
```

Expected: 2 passed. If `test_plain_invoke_populates_usage_metadata` fails, Phase 4's extractor must use the `response_metadata["token_usage"]` fallback — record that in ADR-001 and report it.

- [ ] **Step 5: Write `backend/probes/probe_llm.py`**

```python
"""Phase 0 probe: which usage surface the pinned langchain-core populates.

Run: backend/.venv/bin/python probes/probe_llm.py
Requires a real OPENAI_API_KEY in backend/.env. Costs a fraction of a cent.
"""

from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from upgradepilot.config import get_settings


class Verdict(BaseModel):
    sufficient: bool
    reason: str


def main() -> None:
    settings = get_settings()
    if not settings.openai_configured:
        raise SystemExit("OPENAI_API_KEY missing — set it in backend/.env first")

    model = ChatOpenAI(
        model=settings.chat_model, api_key=settings.openai_api_key, temperature=0
    )

    message = model.invoke("Reply with the single word: ok")
    print(f"model                     : {settings.chat_model}")
    print(f"usage_metadata            : {message.usage_metadata}")
    print(f"response_metadata usage   : {message.response_metadata.get('token_usage')}")

    structured = model.with_structured_output(Verdict, include_raw=True)
    result = structured.invoke("Is one sentence enough evidence? Answer briefly.")
    print(f"structured keys           : {sorted(result)}")
    print(f"parsed type               : {type(result['parsed']).__name__}")
    print(f"usage survives include_raw: {result['raw'].usage_metadata}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Run the probe and save its output for ADR-001**

```bash
cd /Users/nzrsrd/Code/upgrade_pilot/backend
.venv/bin/python probes/probe_llm.py
```

- [ ] **Step 7: Commit**

```bash
cd /Users/nzrsrd/Code/upgrade_pilot
git add backend/probes/probe_llm.py backend/tests/llm/ backend/tests/conftest.py
git commit -m "test(llm): verify usage metadata surface and structured-output usage

Adds the --live marker plumbing and the single real-call test that
guards against fake-LLM tests passing while the token counter reads zero."
```

---

### Task 6: Record findings in ADR-001 and amend the spec

Phase 0's deliverable is not code — it is a verification table filled from real runs, plus any design amendments the probes force. Per development rule 14, a contradicted assumption must be raised and corrected, not worked around.

**Files:**
- Modify: `docs/adr/ADR-001-system-architecture.md` (the "Phase 0 verification record" section)
- Modify: `docs/superpowers/specs/2026-08-24-upgradepilot-agent-core-design.md` (§8.2 node responsibility)
- Modify: `PLANNING.md` (Phase 0 checkboxes)

**Interfaces:**
- Consumes: probe output captured in Tasks 3, 4, and 5.
- Produces: the amended spec §8.2 rule that **Phase 7's `human_review` node must be a thin interrupt-only node**, which the Phase 7 tasks will depend on.

- [ ] **Step 1: Replace ADR-001's verification table with actual results**

Change the section heading from `**Status: pending Phase 0 execution.**` to `**Status: verified 2026-08-24 (or the real date).**` and replace the "what must be recorded" table with a findings table. Fill every cell from captured probe output — do not write "as expected".

```markdown
| Probe | Finding |
|---|---|
| Interpreter | Python 3.14.5, macOS arm64. Only interpreter available; no uv, no pyenv. |
| Pinned versions | fastapi 0.141.1 · uvicorn 0.52.4 · langgraph 1.2.11 · langgraph-checkpoint-sqlite 3.1.1 · langchain-core 1.6.0 · langchain-openai 1.6.0 · openai 3.3.1 · chromadb 1.5.9 · pydantic 2.13.4 · pydantic-settings 2.15.0 · tiktoken 0.14.0. All 108 transitive packages resolved as wheels on cp314; nothing compiles from source. |
| Usage metadata surface | <paste from probe_llm.py> |
| Structured output + usage | <paste from probe_llm.py> |
| Checkpointer round-trip | Interrupt then resume on one thread succeeds; state survives a fresh `AsyncSqliteSaver` over the same file. |
| Thread isolation | Two threads advance independently; no state bleed. |
| ChromaDB | PersistentClient survives restart. Scalar `where` filters work. List-valued metadata rejected. |
| Embeddings | <paste from probe_llm.py or note deferred to Phase 3> |
```

- [ ] **Step 2: Add the new finding that the design did not anticipate**

Append to ADR-001, immediately after the findings table:

```markdown
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
```

- [ ] **Step 3: Amend spec §8.2 to pin node responsibility**

In §8.2, the sentence beginning "Candidate strategies (compatibility layer / staged rollout / direct migration) are enumerated and scored against risk and constraints first" leaves the owning node unspecified. Replace with:

```markdown
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
```

- [ ] **Step 4: Tick the Phase 0 checkboxes in `PLANNING.md`**

Mark every Phase 0 item complete except any probe that genuinely did not run (for example the embedding probe if it was deferred to Phase 3). Leave unticked anything not actually demonstrated — rule 9 forbids marking a requirement complete without demonstrated verification.

- [ ] **Step 5: Verify the whole suite is green before closing the phase**

```bash
cd /Users/nzrsrd/Code/upgrade_pilot/backend
.venv/bin/python -m pytest -v
.venv/bin/ruff check src tests probes
.venv/bin/mypy
```

Expected: all pass; live tests skipped without `--live`.

- [ ] **Step 6: Commit**

```bash
cd /Users/nzrsrd/Code/upgrade_pilot
git add docs/ PLANNING.md
git commit -m "docs: record Phase 0 verification results and amend interrupt design

ADR-001 verification table filled from real probe runs. Adds the finding
that pre-interrupt side effects are billed twice, and amends spec 8.2 so
assess_risk owns strategy enumeration and human_review is interrupt-only."
```

---

# Phase 1 — Domain models and repository access

## File Structure (Phase 1)

| File | Responsibility |
|---|---|
| `models/enums.py` | Shared enumerations: `Severity`, `RiskLevel`, `Confidence`, `SourceType`, `UsageKind`, `DependencyRole`, `VersionConfidence` |
| `models/evidence.py` | Citation types: `SourceRef`, `RepoEvidence`, `DocEvidence`, `EvidenceRef`, `BreakingChange`, `RiskFactor` — where the honesty invariants live |
| `models/inputs.py` | User-supplied inputs: `RepoRef`, `DependencySpec`, `UserConstraints` |
| `models/repo.py` | Analysis outputs: `Manifest`, `DetectedVersion`, `UsageSite`, `AffectedFile`, `SkippedFile`, `SymbolInventory`, `CommitRecord`, `RepoAnalysis` |
| `models/errors.py` | `ErrorCode`, `AppError`, and the `UpgradePilotError` exception hierarchy |
| `services/repo/guards.py` | URL and path validation — the security boundary |
| `services/repo/workspace.py` | `Workspace`: the only view of a repository the analyzer gets |
| `services/repo/local.py` | Local-path resolver |
| `services/repo/clone.py` | Shallow-clone resolver |
| `services/repo/manager.py` | Workspace lifecycle: temp creation, cleanup, stale sweep |
| `tests/fixtures/sample_repo/` | Hand-authored miniature Pydantic v1 project |
| `tests/fixtures/repo_builder.py` | Copies the fixture to `tmp_path` and gives it real git history |

**Note on the fixture repository.** Spec §12 assumption 5 says a real public repository is vendored and pinned. Refining that: unit tests use a **hand-authored miniature project** (Task 13) so expectations stay small, readable, and exactly targeted at each usage pattern; a real public repository is used for the demo and E2E path and is pinned in Phase 12. Vendoring a real repository for unit tests would make assertions large and brittle without testing anything extra. Record this refinement in the spec's assumptions when Task 13 lands.

---

### Task 7: Citation and evidence models — the honesty invariants

**Files:**
- Create: `backend/src/upgradepilot/models/__init__.py`
- Create: `backend/src/upgradepilot/models/enums.py`
- Create: `backend/src/upgradepilot/models/evidence.py`
- Test: `backend/tests/unit/test_evidence_models.py`

**Interfaces:**
- Consumes: `pydantic==2.13.4`.
- Produces, for every later task:
  - `Severity`, `RiskLevel`, `Confidence`, `SourceType`, `RiskCategory` (str enums)
  - `SourceRef(source_id, title, source_type, url_or_reference, chunk_id, relevance)`
  - `RepoEvidence(kind="repo", file, line, snippet=None)`
  - `DocEvidence(kind="doc", source_id, chunk_id, relevance=None)`
  - `EvidenceRef` — discriminated union on `kind`
  - `BreakingChange(id, title, description, old_form, new_form, severity, affected_symbols, source)` — `source` **required**
  - `RiskFactor(id, name, category, level, weight, detail, evidence)` — `evidence` **min_length=1**

- [ ] **Step 1: Write the failing test `backend/tests/unit/test_evidence_models.py`**

```python
import pytest
from pydantic import ValidationError, TypeAdapter

from upgradepilot.models.enums import RiskCategory, RiskLevel, Severity, SourceType
from upgradepilot.models.evidence import (
    BreakingChange,
    DocEvidence,
    EvidenceRef,
    RepoEvidence,
    RiskFactor,
    SourceRef,
)


def a_source() -> SourceRef:
    return SourceRef(
        source_id="pydantic-v2-migration#validator-renamed",
        title="@validator replaced by @field_validator",
        source_type=SourceType.MIGRATION_GUIDE,
        url_or_reference="https://docs.pydantic.dev/latest/migration/",
        chunk_id="chunk-1",
        relevance=0.94,
    )


def test_breaking_change_requires_a_source() -> None:
    """The core invariant: an uncited breaking change is unconstructable."""
    with pytest.raises(ValidationError) as excinfo:
        BreakingChange(
            id="bc-1",
            title="@validator removed",
            description="renamed",
            old_form="@validator",
            new_form="@field_validator",
            severity=Severity.HIGH,
            affected_symbols=["validator"],
        )
    assert "source" in str(excinfo.value)


def test_breaking_change_with_a_source_is_valid() -> None:
    change = BreakingChange(
        id="bc-1",
        title="@validator removed",
        description="renamed to @field_validator",
        old_form="@validator",
        new_form="@field_validator",
        severity=Severity.HIGH,
        affected_symbols=["validator", "root_validator"],
        source=a_source(),
    )
    assert change.source.source_id.startswith("pydantic-v2-migration")
    assert change.severity is Severity.HIGH


def test_breaking_change_requires_at_least_one_symbol() -> None:
    with pytest.raises(ValidationError):
        BreakingChange(
            id="bc-2",
            title="t",
            description="d",
            old_form=None,
            new_form=None,
            severity=Severity.LOW,
            affected_symbols=[],
            source=a_source(),
        )


def test_risk_factor_requires_evidence() -> None:
    """A risk factor citing nothing is unconstructable."""
    with pytest.raises(ValidationError) as excinfo:
        RiskFactor(
            id="rf-1",
            name="breaking_change_exposure",
            category=RiskCategory.BREAKING_CHANGE,
            level=RiskLevel.HIGH,
            weight=0.4,
            detail="three high-confidence sites collide with documented changes",
            evidence=[],
        )
    assert "evidence" in str(excinfo.value)


def test_risk_factor_accepts_mixed_evidence_kinds() -> None:
    factor = RiskFactor(
        id="rf-1",
        name="breaking_change_exposure",
        category=RiskCategory.BREAKING_CHANGE,
        level=RiskLevel.HIGH,
        weight=0.4,
        detail="collides with a documented change",
        evidence=[
            RepoEvidence(file="src/models.py", line=12, snippet="@validator('email')"),
            DocEvidence(source_id="pydantic-v2-migration#validator-renamed", chunk_id="chunk-1"),
        ],
    )
    assert [e.kind for e in factor.evidence] == ["repo", "doc"]


def test_evidence_ref_discriminates_on_kind() -> None:
    adapter = TypeAdapter(EvidenceRef)

    repo = adapter.validate_python({"kind": "repo", "file": "a.py", "line": 3})
    doc = adapter.validate_python({"kind": "doc", "source_id": "s", "chunk_id": "c"})

    assert isinstance(repo, RepoEvidence)
    assert isinstance(doc, DocEvidence)
    with pytest.raises(ValidationError):
        adapter.validate_python({"kind": "guess", "file": "a.py", "line": 3})


def test_repo_evidence_rejects_a_nonpositive_line() -> None:
    with pytest.raises(ValidationError):
        RepoEvidence(file="a.py", line=0)


def test_relevance_is_bounded() -> None:
    with pytest.raises(ValidationError):
        SourceRef(
            source_id="s",
            title="t",
            source_type=SourceType.ADR,
            url_or_reference="ref",
            chunk_id="c",
            relevance=1.4,
        )
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd /Users/nzrsrd/Code/upgrade_pilot/backend
mkdir -p tests/unit && touch tests/unit/__init__.py
.venv/bin/python -m pytest tests/unit/test_evidence_models.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'upgradepilot.models'`.

- [ ] **Step 3: Write `backend/src/upgradepilot/models/enums.py`**

```python
"""Shared enumerations. Str-valued so they serialize readably over the API."""

from enum import StrEnum


class Severity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Confidence(StrEnum):
    """Confidence in a detected usage site or symbol."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class SourceType(StrEnum):
    MIGRATION_GUIDE = "migration_guide"
    CHANGELOG = "changelog"
    ADR = "adr"
    UPGRADE_REPORT = "upgrade_report"
    COMPAT_NOTE = "compat_note"


class RiskCategory(StrEnum):
    BREAKING_CHANGE = "breaking_change"
    BLAST_RADIUS = "blast_radius"
    TEST_COVERAGE = "test_coverage"
    CHURN = "churn"
    ANALYSIS_COVERAGE = "analysis_coverage"
    EVIDENCE_COVERAGE = "evidence_coverage"
    CONSTRAINT_PRESSURE = "constraint_pressure"


class UsageKind(StrEnum):
    IMPORT = "import"
    MODEL_DEFINITION = "model_definition"
    DECORATOR = "decorator"
    NESTED_CONFIG = "nested_config"
    OPTIONAL_FIELD = "optional_field"
    METHOD_CALL = "method_call"


class DependencyRole(StrEnum):
    DIRECT = "direct"
    TRANSITIVE_ONLY = "transitive_only"


class VersionConfidence(StrEnum):
    EXACT = "exact"
    RANGE = "range"
```

- [ ] **Step 4: Write `backend/src/upgradepilot/models/evidence.py`**

```python
"""Citation types.

The invariants here are the product's central promise made structural:
a breaking change without a source, or a risk factor without evidence,
cannot be constructed at all.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from upgradepilot.models.enums import RiskCategory, RiskLevel, Severity, SourceType


class SourceRef(BaseModel):
    """A resolvable pointer into the knowledge base."""

    model_config = ConfigDict(frozen=True)

    source_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    source_type: SourceType
    url_or_reference: str = Field(min_length=1)
    chunk_id: str = Field(min_length=1)
    relevance: float = Field(ge=0.0, le=1.0)


class RepoEvidence(BaseModel):
    """A specific line of the analyzed repository."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["repo"] = "repo"
    file: str = Field(min_length=1)
    line: int = Field(ge=1)
    snippet: str | None = None


class DocEvidence(BaseModel):
    """A specific chunk of a corpus document."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["doc"] = "doc"
    source_id: str = Field(min_length=1)
    chunk_id: str = Field(min_length=1)
    relevance: float | None = Field(default=None, ge=0.0, le=1.0)


EvidenceRef = Annotated[RepoEvidence | DocEvidence, Field(discriminator="kind")]


class BreakingChange(BaseModel):
    """A documented change. `source` is required: no citation, no change."""

    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    old_form: str | None = None
    new_form: str | None = None
    severity: Severity
    affected_symbols: list[str] = Field(min_length=1)
    source: SourceRef


class RiskFactor(BaseModel):
    """One dimension of risk. `evidence` must be non-empty."""

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    category: RiskCategory
    level: RiskLevel
    weight: float = Field(ge=0.0, le=1.0)
    detail: str = Field(min_length=1)
    evidence: list[EvidenceRef] = Field(min_length=1)
```

Also create an empty `backend/src/upgradepilot/models/__init__.py`.

- [ ] **Step 5: Run the test to verify it passes**

```bash
cd /Users/nzrsrd/Code/upgrade_pilot/backend
.venv/bin/python -m pytest tests/unit/test_evidence_models.py -v
```

Expected: 8 passed.

- [ ] **Step 6: Lint, typecheck, commit**

```bash
cd /Users/nzrsrd/Code/upgrade_pilot/backend
.venv/bin/ruff check src tests && .venv/bin/mypy
cd /Users/nzrsrd/Code/upgrade_pilot
git add backend/src/upgradepilot/models backend/tests/unit
git commit -m "feat(models): add citation and evidence models with honesty invariants

BreakingChange.source is required and RiskFactor.evidence has min_length=1,
so an uncited claim raises ValidationError rather than reaching a report."
```

---

### Task 8: Input and repository-analysis models

**Files:**
- Create: `backend/src/upgradepilot/models/inputs.py`
- Create: `backend/src/upgradepilot/models/repo.py`
- Modify: `backend/src/upgradepilot/models/enums.py` (add `ManifestKind`)
- Test: `backend/tests/unit/test_repo_models.py`

**Interfaces:**
- Consumes: `models.enums` and `models.evidence` from Task 7.
- Produces, for every later task:
  - `RemoteRepoRef(kind="remote", url)`, `LocalRepoRef(kind="local", path)`, `RepoRef` (discriminated union on `kind`)
  - `DependencySpec(name, current_version, target_version)`
  - `UserConstraints(zero_downtime, deadline, minimize_effort, risk_tolerance)`
  - `Manifest(path, kind, declared_specifier)`
  - `DetectedVersion(value, specifier, source_manifest, confidence, role)`
  - `UsageSite(file, line, column, symbol, kind, confidence, snippet)`
  - `AffectedFile(path, usage_sites, symbols, is_test, commit_count, last_modified)`
  - `SkippedFile(path, reason)`
  - `SymbolStat(symbol, count, files, confidence)`
  - `SymbolInventory(entries)` plus **`SymbolInventory.from_sites(sites) -> SymbolInventory`** and **`SymbolInventory.high_confidence_symbols() -> tuple[str, ...]`** — Phase 5's retrieval planner and Phase 6's `evidence_coverage` factor both call these
  - `CommitRecord(sha, timestamp, files)`
  - `RepoAnalysis(...)` as defined below

- [ ] **Step 1: Write the failing test `backend/tests/unit/test_repo_models.py`**

Note the symbol-confidence tests: spec §7.1 defines a *symbol* as high-confidence when at least one of its sites is high-confidence. That rule is easy to get subtly wrong, so it is tested directly.

```python
from datetime import date, datetime, timezone

import pytest
from pydantic import TypeAdapter, ValidationError

from upgradepilot.models.enums import (
    Confidence,
    DependencyRole,
    ManifestKind,
    RiskLevel,
    UsageKind,
    VersionConfidence,
)
from upgradepilot.models.inputs import (
    DependencySpec,
    LocalRepoRef,
    RemoteRepoRef,
    RepoRef,
    UserConstraints,
)
from upgradepilot.models.repo import (
    AffectedFile,
    CommitRecord,
    DetectedVersion,
    Manifest,
    RepoAnalysis,
    SkippedFile,
    SymbolInventory,
    UsageSite,
)


def site(symbol: str, confidence: Confidence, file: str = "src/app/models.py", line: int = 1):
    return UsageSite(
        file=file,
        line=line,
        column=0,
        symbol=symbol,
        kind=UsageKind.METHOD_CALL,
        confidence=confidence,
        snippet=f"{symbol}()",
    )


def test_repo_ref_discriminates_remote_and_local() -> None:
    adapter = TypeAdapter(RepoRef)

    remote = adapter.validate_python(
        {"kind": "remote", "url": "https://github.com/acme/payment-service"}
    )
    local = adapter.validate_python({"kind": "local", "path": "/Users/nzrsrd/Code/demo"})

    assert isinstance(remote, RemoteRepoRef)
    assert isinstance(local, LocalRepoRef)
    with pytest.raises(ValidationError):
        adapter.validate_python({"kind": "ftp", "url": "ftp://x"})


def test_dependency_spec_rejects_blank_versions() -> None:
    with pytest.raises(ValidationError):
        DependencySpec(name="pydantic", current_version="", target_version="2.13.4")


def test_dependency_spec_rejects_an_unchanged_version() -> None:
    """Analyzing 1.10.13 -> 1.10.13 is a user error worth catching at the boundary."""
    with pytest.raises(ValidationError) as excinfo:
        DependencySpec(name="pydantic", current_version="1.10.13", target_version="1.10.13")
    assert "differ" in str(excinfo.value)


def test_user_constraints_defaults_are_permissive() -> None:
    constraints = UserConstraints()
    assert constraints.zero_downtime is False
    assert constraints.minimize_effort is False
    assert constraints.deadline is None
    assert constraints.risk_tolerance is RiskLevel.MEDIUM


def test_user_constraints_accepts_a_deadline() -> None:
    constraints = UserConstraints(zero_downtime=True, deadline=date(2026, 9, 1))
    assert constraints.deadline == date(2026, 9, 1)


def test_symbol_inventory_counts_sites_per_symbol() -> None:
    inventory = SymbolInventory.from_sites(
        [
            site("validator", Confidence.HIGH, "src/app/models.py", 10),
            site("validator", Confidence.HIGH, "src/app/other.py", 4),
            site("dict", Confidence.MEDIUM, "src/app/service.py", 20),
        ]
    )

    assert inventory.entries["validator"].count == 2
    assert set(inventory.entries["validator"].files) == {"src/app/models.py", "src/app/other.py"}
    assert inventory.entries["dict"].count == 1


def test_symbol_confidence_is_the_best_of_its_sites() -> None:
    """Spec 7.1: a symbol is high-confidence if ANY site is high-confidence."""
    inventory = SymbolInventory.from_sites(
        [
            site("dict", Confidence.LOW, "src/util.py", 3),
            site("dict", Confidence.HIGH, "src/app/models.py", 7),
            site("dict", Confidence.MEDIUM, "src/app/service.py", 9),
        ]
    )
    assert inventory.entries["dict"].confidence is Confidence.HIGH


def test_symbol_confidence_medium_when_no_high_site_exists() -> None:
    inventory = SymbolInventory.from_sites(
        [
            site("dict", Confidence.LOW, "src/util.py", 3),
            site("dict", Confidence.MEDIUM, "src/app/service.py", 9),
        ]
    )
    assert inventory.entries["dict"].confidence is Confidence.MEDIUM


def test_high_confidence_symbols_are_sorted_and_filtered() -> None:
    inventory = SymbolInventory.from_sites(
        [
            site("validator", Confidence.HIGH),
            site("Config", Confidence.HIGH),
            site("dict", Confidence.MEDIUM),
        ]
    )
    assert inventory.high_confidence_symbols() == ("Config", "validator")


def test_empty_inventory_is_valid() -> None:
    inventory = SymbolInventory.from_sites([])
    assert inventory.entries == {}
    assert inventory.high_confidence_symbols() == ()


def test_affected_file_requires_at_least_one_usage_site() -> None:
    with pytest.raises(ValidationError):
        AffectedFile(path="src/app/models.py", usage_sites=[], symbols=[], is_test=False)


def test_affected_file_derives_symbols_from_sites() -> None:
    affected = AffectedFile.from_sites(
        path="src/app/models.py",
        sites=[site("validator", Confidence.HIGH), site("Config", Confidence.HIGH)],
        is_test=False,
        commit_count=3,
        last_modified=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )
    assert affected.symbols == ("Config", "validator")
    assert affected.commit_count == 3


def test_detected_version_records_provenance() -> None:
    detected = DetectedVersion(
        value="1.10.13",
        specifier="==1.10.13",
        source_manifest=Manifest(
            path="requirements.txt", kind=ManifestKind.REQUIREMENTS, declared_specifier="==1.10.13"
        ),
        confidence=VersionConfidence.EXACT,
        role=DependencyRole.DIRECT,
    )
    assert detected.confidence is VersionConfidence.EXACT
    assert detected.source_manifest.kind is ManifestKind.REQUIREMENTS


def test_repo_analysis_reports_discrepancy_against_the_stated_version() -> None:
    analysis = RepoAnalysis(
        commit_sha="a" * 40,
        languages={"Python": 0.92, "TypeScript": 0.08},
        manifests=[
            Manifest(path="pyproject.toml", kind=ManifestKind.PYPROJECT, declared_specifier="^1.10")
        ],
        detected_version=DetectedVersion(
            value="1.10.13",
            specifier="^1.10",
            source_manifest=Manifest(
                path="pyproject.toml", kind=ManifestKind.PYPROJECT, declared_specifier="^1.10"
            ),
            confidence=VersionConfidence.RANGE,
            role=DependencyRole.DIRECT,
        ),
        total_python_files=40,
        analyzed_files=38,
        skipped_files=[SkippedFile(path="src/broken.py", reason="SyntaxError at line 3")],
        affected_files=[],
        symbol_inventory=SymbolInventory.from_sites([]),
        commit_records=[
            CommitRecord(
                sha="b" * 40,
                timestamp=datetime(2026, 8, 20, tzinfo=timezone.utc),
                files=("src/app/models.py",),
            )
        ],
        test_paths=("tests/test_models.py",),
    )

    assert analysis.version_discrepancy(stated="1.9.0") == ("1.9.0", "1.10.13")
    assert analysis.version_discrepancy(stated="1.10.13") is None
    assert analysis.skipped_ratio == pytest.approx(1 / 40)


def test_repo_analysis_skipped_ratio_is_zero_for_an_empty_repo() -> None:
    analysis = RepoAnalysis(
        commit_sha=None,
        languages={},
        manifests=[],
        detected_version=None,
        total_python_files=0,
        analyzed_files=0,
        skipped_files=[],
        affected_files=[],
        symbol_inventory=SymbolInventory.from_sites([]),
        commit_records=[],
        test_paths=(),
    )
    assert analysis.skipped_ratio == 0.0
```

- [ ] **Step 2: Run the test to verify it fails**

Run from `backend/`: `.venv/bin/python -m pytest tests/unit/test_repo_models.py -v`
Expected: FAIL — `ImportError: cannot import name 'ManifestKind'`.

- [ ] **Step 3: Append `ManifestKind` to `backend/src/upgradepilot/models/enums.py`**

```python
class ManifestKind(StrEnum):
    PYPROJECT = "pyproject"
    REQUIREMENTS = "requirements"
    POETRY_LOCK = "poetry_lock"
    UV_LOCK = "uv_lock"
    PIPFILE_LOCK = "pipfile_lock"
```

- [ ] **Step 4: Write `backend/src/upgradepilot/models/inputs.py`**

```python
"""User-supplied inputs. Validated at the boundary so no node re-checks them."""

from datetime import date
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from upgradepilot.models.enums import RiskLevel


class RemoteRepoRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal["remote"] = "remote"
    url: str = Field(min_length=1)


class LocalRepoRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal["local"] = "local"
    path: str = Field(min_length=1)


RepoRef = Annotated[RemoteRepoRef | LocalRepoRef, Field(discriminator="kind")]


class DependencySpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    current_version: str = Field(min_length=1)
    target_version: str = Field(min_length=1)

    @model_validator(mode="after")
    def _versions_must_differ(self) -> Self:
        if self.current_version.strip() == self.target_version.strip():
            raise ValueError("current_version and target_version must differ")
        return self


class UserConstraints(BaseModel):
    """Migration constraints. Defaults are permissive so an omitted
    constraint never silently tightens the recommendation."""

    model_config = ConfigDict(frozen=True)

    zero_downtime: bool = False
    minimize_effort: bool = False
    deadline: date | None = None
    risk_tolerance: RiskLevel = RiskLevel.MEDIUM
```

- [ ] **Step 5: Write `backend/src/upgradepilot/models/repo.py`**

```python
"""Repository analysis outputs. Pure data; no I/O."""

from datetime import datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, Field

from upgradepilot.models.enums import (
    Confidence,
    DependencyRole,
    ManifestKind,
    UsageKind,
    VersionConfidence,
)

_CONFIDENCE_ORDER: dict[Confidence, int] = {
    Confidence.LOW: 0,
    Confidence.MEDIUM: 1,
    Confidence.HIGH: 2,
}


class Manifest(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str = Field(min_length=1)
    kind: ManifestKind
    declared_specifier: str | None = None


class DetectedVersion(BaseModel):
    model_config = ConfigDict(frozen=True)

    value: str = Field(min_length=1)
    specifier: str | None
    source_manifest: Manifest
    confidence: VersionConfidence
    role: DependencyRole


class UsageSite(BaseModel):
    model_config = ConfigDict(frozen=True)

    file: str = Field(min_length=1)
    line: int = Field(ge=1)
    column: int = Field(ge=0)
    symbol: str = Field(min_length=1)
    kind: UsageKind
    confidence: Confidence
    snippet: str | None = None


class SkippedFile(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class SymbolStat(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str = Field(min_length=1)
    count: int = Field(ge=1)
    files: tuple[str, ...] = Field(min_length=1)
    confidence: Confidence


class SymbolInventory(BaseModel):
    model_config = ConfigDict(frozen=True)

    entries: dict[str, SymbolStat] = Field(default_factory=dict)

    @classmethod
    def from_sites(cls, sites: list[UsageSite]) -> Self:
        """Aggregate sites per symbol.

        A symbol's confidence is the best of its sites (spec 7.1): one
        high-confidence site makes the symbol high-confidence, because the
        retrieval sufficiency gate and evidence_coverage are both defined
        over high-confidence symbols.
        """
        grouped: dict[str, list[UsageSite]] = {}
        for site in sites:
            grouped.setdefault(site.symbol, []).append(site)

        entries: dict[str, SymbolStat] = {}
        for symbol, symbol_sites in grouped.items():
            best = max(symbol_sites, key=lambda s: _CONFIDENCE_ORDER[s.confidence]).confidence
            entries[symbol] = SymbolStat(
                symbol=symbol,
                count=len(symbol_sites),
                files=tuple(sorted({s.file for s in symbol_sites})),
                confidence=best,
            )
        return cls(entries=entries)

    def high_confidence_symbols(self) -> tuple[str, ...]:
        return tuple(
            sorted(s for s, stat in self.entries.items() if stat.confidence is Confidence.HIGH)
        )


class AffectedFile(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str = Field(min_length=1)
    usage_sites: list[UsageSite] = Field(min_length=1)
    symbols: tuple[str, ...] = ()
    is_test: bool = False
    commit_count: int = Field(default=0, ge=0)
    last_modified: datetime | None = None

    @classmethod
    def from_sites(
        cls,
        path: str,
        sites: list[UsageSite],
        *,
        is_test: bool = False,
        commit_count: int = 0,
        last_modified: datetime | None = None,
    ) -> Self:
        return cls(
            path=path,
            usage_sites=sites,
            symbols=tuple(sorted({s.symbol for s in sites})),
            is_test=is_test,
            commit_count=commit_count,
            last_modified=last_modified,
        )


class CommitRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    sha: str = Field(min_length=7)
    timestamp: datetime
    files: tuple[str, ...] = ()


class RepoAnalysis(BaseModel):
    model_config = ConfigDict(frozen=True)

    commit_sha: str | None
    languages: dict[str, float] = Field(default_factory=dict)
    manifests: list[Manifest] = Field(default_factory=list)
    detected_version: DetectedVersion | None
    total_python_files: int = Field(ge=0)
    analyzed_files: int = Field(ge=0)
    skipped_files: list[SkippedFile] = Field(default_factory=list)
    affected_files: list[AffectedFile] = Field(default_factory=list)
    symbol_inventory: SymbolInventory
    commit_records: list[CommitRecord] = Field(default_factory=list)
    test_paths: tuple[str, ...] = ()

    @property
    def skipped_ratio(self) -> float:
        """Share of Python files that could not be parsed. Feeds the
        analysis_coverage risk factor and the confidence ceiling."""
        if self.total_python_files == 0:
            return 0.0
        return len(self.skipped_files) / self.total_python_files

    def version_discrepancy(self, stated: str) -> tuple[str, str] | None:
        """Return (stated, detected) when they disagree, else None.

        Surfaced in the report rather than silently overridden in either
        direction (spec 7.1).
        """
        if self.detected_version is None:
            return None
        if stated.strip() == self.detected_version.value.strip():
            return None
        return (stated.strip(), self.detected_version.value)
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_repo_models.py -v`
Expected: 15 passed.

- [ ] **Step 7: Lint, typecheck, commit**

```bash
cd /Users/nzrsrd/Code/upgrade_pilot/backend
.venv/bin/ruff check src tests && .venv/bin/mypy
cd /Users/nzrsrd/Code/upgrade_pilot
git add backend/src/upgradepilot/models backend/tests/unit/test_repo_models.py
git commit -m "feat(models): add input and repository-analysis models

Includes SymbolInventory.from_sites implementing the spec 7.1 rule that a
symbol's confidence is the best of its sites, and version_discrepancy so a
stated-vs-detected mismatch surfaces instead of being overridden."
```

---

### Task 9: Error taxonomy and repository access guards

Accepting a URL *or* a filesystem path is an arbitrary-read surface. This task is the security boundary, so every guard gets a failing-case test.

**Files:**
- Create: `backend/src/upgradepilot/models/errors.py`
- Create: `backend/src/upgradepilot/services/__init__.py`
- Create: `backend/src/upgradepilot/services/repo/__init__.py`
- Create: `backend/src/upgradepilot/services/repo/guards.py`
- Test: `backend/tests/unit/test_repo_guards.py`

**Interfaces:**
- Consumes: `models.enums`.
- Produces:
  - `ErrorCode` (str enum, the full taxonomy from spec §9.3)
  - `AppError(code, message, detail, node, retryable)` — the state-carried record
  - `UpgradePilotError` base exception with class-level `code` and `http_status`, plus `.to_app_error()`; subclasses `InvalidRepoUrlError`, `LocalPathForbiddenError`, `RepoUnavailableError`, `RepoTooLargeError`, `DependencyNotFoundError`, `VersionInvalidError`, `KnowledgeBaseUnavailableError`, `ThreadNotFoundError`, `ThreadNotAwaitingInputError`, `InvalidDecisionError`. **Phase 9's exception handler maps `http_status` directly.**
  - `validate_clone_url(raw, allowed_schemes) -> str`
  - `resolve_local_path(raw, allowed_roots) -> Path`

- [ ] **Step 1: Write the failing test `backend/tests/unit/test_repo_guards.py`**

```python
from pathlib import Path

import pytest

from upgradepilot.models.errors import (
    ErrorCode,
    InvalidRepoUrlError,
    LocalPathForbiddenError,
    UpgradePilotError,
)
from upgradepilot.services.repo.guards import resolve_local_path, validate_clone_url

DEFAULT_SCHEMES = frozenset({"https", "git"})


# --- URL validation -------------------------------------------------------

def test_accepts_an_https_github_url() -> None:
    url = validate_clone_url("https://github.com/acme/payment-service", DEFAULT_SCHEMES)
    assert url == "https://github.com/acme/payment-service"


def test_accepts_a_git_scheme_url() -> None:
    assert validate_clone_url("git://example.com/repo.git", DEFAULT_SCHEMES)


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ssh://git@github.com/acme/repo.git",
        "ftp://example.com/repo",
        "http://github.com/acme/repo",
        "/Users/nzrsrd/Code/demo",
        "github.com/acme/repo",
    ],
)
def test_rejects_disallowed_schemes(url: str) -> None:
    with pytest.raises(InvalidRepoUrlError) as excinfo:
        validate_clone_url(url, DEFAULT_SCHEMES)
    assert excinfo.value.code is ErrorCode.INVALID_REPO_URL


def test_rejects_credentials_embedded_in_the_url() -> None:
    """Never accept a token pasted into a URL — Sub-project 2 handles auth properly."""
    with pytest.raises(InvalidRepoUrlError) as excinfo:
        validate_clone_url("https://user:token@github.com/acme/repo", DEFAULT_SCHEMES)
    assert "credential" in str(excinfo.value).lower()


def test_rejects_a_url_with_no_host() -> None:
    with pytest.raises(InvalidRepoUrlError):
        validate_clone_url("https:///acme/repo", DEFAULT_SCHEMES)


def test_rejects_blank_input() -> None:
    with pytest.raises(InvalidRepoUrlError):
        validate_clone_url("   ", DEFAULT_SCHEMES)


def test_allowlist_is_injectable_so_tests_can_permit_file_urls() -> None:
    """Clone-resolver tests need file:// without weakening production defaults."""
    assert validate_clone_url("file:///tmp/repo", frozenset({"file"}))


# --- Local path resolution ------------------------------------------------

def test_accepts_a_path_inside_an_allowed_root(tmp_path: Path) -> None:
    project = tmp_path / "demo"
    project.mkdir()
    assert resolve_local_path(str(project), [tmp_path]) == project.resolve()


def test_accepts_the_allowed_root_itself(tmp_path: Path) -> None:
    assert resolve_local_path(str(tmp_path), [tmp_path]) == tmp_path.resolve()


def test_rejects_a_path_outside_every_allowed_root(tmp_path: Path) -> None:
    outside = tmp_path.parent / "elsewhere"
    outside.mkdir(exist_ok=True)
    with pytest.raises(LocalPathForbiddenError) as excinfo:
        resolve_local_path(str(outside), [tmp_path / "allowed"])
    assert excinfo.value.code is ErrorCode.LOCAL_PATH_FORBIDDEN


def test_rejects_traversal_escaping_an_allowed_root(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    with pytest.raises(LocalPathForbiddenError):
        resolve_local_path(str(allowed / ".." / ".."), [allowed])


def test_rejects_a_symlink_pointing_outside_an_allowed_root(tmp_path: Path) -> None:
    """resolve() follows symlinks, so containment is checked on the real path."""
    allowed = tmp_path / "allowed"
    secret = tmp_path / "secret"
    allowed.mkdir()
    secret.mkdir()
    link = allowed / "escape"
    link.symlink_to(secret, target_is_directory=True)

    with pytest.raises(LocalPathForbiddenError):
        resolve_local_path(str(link), [allowed])


def test_rejects_a_missing_directory(tmp_path: Path) -> None:
    with pytest.raises(LocalPathForbiddenError) as excinfo:
        resolve_local_path(str(tmp_path / "nope"), [tmp_path])
    assert "does not exist" in str(excinfo.value).lower()


def test_rejects_a_file_where_a_directory_is_required(tmp_path: Path) -> None:
    target = tmp_path / "a.py"
    target.write_text("x = 1\n")
    with pytest.raises(LocalPathForbiddenError):
        resolve_local_path(str(target), [tmp_path])


def test_rejects_when_no_roots_are_configured(tmp_path: Path) -> None:
    """An empty allowlist denies everything rather than allowing everything."""
    with pytest.raises(LocalPathForbiddenError):
        resolve_local_path(str(tmp_path), [])


# --- Error contract -------------------------------------------------------

def test_errors_expose_a_code_and_an_http_status() -> None:
    error = InvalidRepoUrlError("bad url", detail="scheme was ftp")
    assert error.http_status == 422
    assert LocalPathForbiddenError("no").http_status == 403


def test_error_converts_to_an_app_error_preserving_technical_detail() -> None:
    error = InvalidRepoUrlError("Repository URL is not valid.", detail="scheme=ftp host=x")
    app_error = error.to_app_error(node="analyze_repo")

    assert app_error.code is ErrorCode.INVALID_REPO_URL
    assert app_error.message == "Repository URL is not valid."
    assert app_error.detail == "scheme=ftp host=x"
    assert app_error.node == "analyze_repo"
    assert app_error.retryable is False


def test_base_error_is_catchable_as_one_type() -> None:
    with pytest.raises(UpgradePilotError):
        raise LocalPathForbiddenError("denied")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_repo_guards.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'upgradepilot.models.errors'`.

- [ ] **Step 3: Write `backend/src/upgradepilot/models/errors.py`**

```python
"""Error taxonomy.

`message` is user-facing and comprehensible. `detail` is technical and is
logged, correlated by thread_id. Nothing is ever swallowed: a caught
exception becomes an AppError in state plus a trace event.
"""

from enum import StrEnum
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field


class ErrorCode(StrEnum):
    INVALID_REPO_URL = "invalid_repo_url"
    LOCAL_PATH_FORBIDDEN = "local_path_forbidden"
    REPO_UNAVAILABLE = "repo_unavailable"
    REPO_TOO_LARGE = "repo_too_large"
    DEPENDENCY_NOT_FOUND = "dependency_not_found"
    VERSION_INVALID = "version_invalid"
    KB_UNAVAILABLE = "kb_unavailable"
    LLM_UNAVAILABLE = "llm_unavailable"
    LLM_RATE_LIMITED = "llm_rate_limited"
    THREAD_NOT_FOUND = "thread_not_found"
    THREAD_NOT_AWAITING_INPUT = "thread_not_awaiting_input"
    INVALID_DECISION = "invalid_decision"
    INTERNAL = "internal"


class AppError(BaseModel):
    """An error recorded in graph state and surfaced to the client."""

    model_config = ConfigDict(frozen=True)

    code: ErrorCode
    message: str = Field(min_length=1)
    detail: str | None = None
    node: str | None = None
    retryable: bool = False


class UpgradePilotError(Exception):
    """Base for all domain errors. Subclasses set code and http_status."""

    code: ClassVar[ErrorCode] = ErrorCode.INTERNAL
    http_status: ClassVar[int] = 500
    retryable: ClassVar[bool] = False

    def __init__(self, message: str, *, detail: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail

    def to_app_error(self, node: str | None = None) -> AppError:
        return AppError(
            code=self.code,
            message=self.message,
            detail=self.detail,
            node=node,
            retryable=self.retryable,
        )


class InvalidRepoUrlError(UpgradePilotError):
    code = ErrorCode.INVALID_REPO_URL
    http_status = 422


class LocalPathForbiddenError(UpgradePilotError):
    code = ErrorCode.LOCAL_PATH_FORBIDDEN
    http_status = 403


class RepoUnavailableError(UpgradePilotError):
    code = ErrorCode.REPO_UNAVAILABLE
    http_status = 502
    retryable = True


class RepoTooLargeError(UpgradePilotError):
    code = ErrorCode.REPO_TOO_LARGE
    http_status = 413


class DependencyNotFoundError(UpgradePilotError):
    code = ErrorCode.DEPENDENCY_NOT_FOUND
    http_status = 422


class VersionInvalidError(UpgradePilotError):
    code = ErrorCode.VERSION_INVALID
    http_status = 422


class KnowledgeBaseUnavailableError(UpgradePilotError):
    code = ErrorCode.KB_UNAVAILABLE
    http_status = 503
    retryable = True


class ThreadNotFoundError(UpgradePilotError):
    code = ErrorCode.THREAD_NOT_FOUND
    http_status = 404


class ThreadNotAwaitingInputError(UpgradePilotError):
    code = ErrorCode.THREAD_NOT_AWAITING_INPUT
    http_status = 409


class InvalidDecisionError(UpgradePilotError):
    code = ErrorCode.INVALID_DECISION
    http_status = 422
```

- [ ] **Step 4: Write `backend/src/upgradepilot/services/repo/guards.py`**

```python
"""Security boundary for repository access.

Accepting a URL or a filesystem path is an arbitrary-read surface, so both
are validated here and nowhere else. The scheme allowlist and root list are
parameters rather than globals so tests can permit file:// without
weakening production defaults.
"""

from collections.abc import Sequence
from pathlib import Path
from urllib.parse import urlsplit

from upgradepilot.models.errors import InvalidRepoUrlError, LocalPathForbiddenError


def validate_clone_url(raw: str, allowed_schemes: frozenset[str]) -> str:
    """Return the URL unchanged if safe to hand to `git clone`."""
    candidate = raw.strip()
    if not candidate:
        raise InvalidRepoUrlError("A repository URL is required.")

    parts = urlsplit(candidate)

    if parts.scheme not in allowed_schemes:
        raise InvalidRepoUrlError(
            f"Repository URL scheme must be one of: {', '.join(sorted(allowed_schemes))}.",
            detail=f"scheme={parts.scheme!r} url={candidate!r}",
        )

    if parts.username or parts.password:
        raise InvalidRepoUrlError(
            "Remove the credentials from the repository URL. "
            "Private repositories are not supported yet.",
            detail="credentials present in netloc",
        )

    # file:// legitimately has an empty host; every network scheme needs one.
    if parts.scheme != "file" and not parts.hostname:
        raise InvalidRepoUrlError(
            "Repository URL is missing a host.",
            detail=f"url={candidate!r}",
        )

    if parts.scheme == "file" and not parts.path:
        raise InvalidRepoUrlError("file:// URL is missing a path.", detail=candidate)

    return candidate


def resolve_local_path(raw: str, allowed_roots: Sequence[Path]) -> Path:
    """Resolve a local repository path, confined to the configured roots.

    `Path.resolve()` follows symlinks, so containment is checked against the
    real path — a symlink pointing outside an allowed root is rejected.
    An empty root list denies everything.
    """
    candidate = Path(raw.strip()).expanduser()

    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise LocalPathForbiddenError(
            "That repository path does not exist.",
            detail=f"path={raw!r} error={exc!r}",
        ) from exc

    if not resolved.is_dir():
        raise LocalPathForbiddenError(
            "The repository path must be a directory.",
            detail=f"path={resolved}",
        )

    if not allowed_roots:
        raise LocalPathForbiddenError(
            "Local repository analysis is not enabled on this server.",
            detail="UP_ALLOWED_LOCAL_ROOTS is empty",
        )

    for root in allowed_roots:
        try:
            root_resolved = root.expanduser().resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        if resolved == root_resolved or root_resolved in resolved.parents:
            return resolved

    raise LocalPathForbiddenError(
        "That repository path is outside the allowed directories.",
        detail=f"path={resolved} roots={[str(r) for r in allowed_roots]}",
    )
```

Also create empty `backend/src/upgradepilot/services/__init__.py` and `backend/src/upgradepilot/services/repo/__init__.py`.

- [ ] **Step 5: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_repo_guards.py -v`
Expected: 22 passed.

- [ ] **Step 6: Lint, typecheck, commit**

```bash
cd /Users/nzrsrd/Code/upgrade_pilot/backend
.venv/bin/ruff check src tests && .venv/bin/mypy
cd /Users/nzrsrd/Code/upgrade_pilot
git add backend/src/upgradepilot backend/tests/unit/test_repo_guards.py
git commit -m "feat(repo): add error taxonomy and repository access guards

Scheme allowlist, credential rejection, root confinement, traversal and
symlink-escape rejection. Empty allowlist denies rather than permits.
Errors carry both a user-facing message and technical detail."
```

---

### Task 10: Workspace and the local-path resolver

**Files:**
- Create: `backend/src/upgradepilot/services/repo/workspace.py`
- Create: `backend/src/upgradepilot/services/repo/local.py`
- Test: `backend/tests/unit/test_workspace.py`

**Interfaces:**
- Consumes: `guards.resolve_local_path`, `models.repo.CommitRecord`, `models.errors.RepoTooLargeError`.
- Produces:
  - `Workspace(root, commit_sha=None, cleanup_dir=None)` with `iter_files(suffix=".py") -> Iterator[Path]` (repo-relative), `read_text(relative) -> str`, `enforce_caps(max_files, max_bytes) -> None`, `git_log(limit=100) -> list[CommitRecord]`, `cleanup() -> None`, and context-manager support. **Phase 2's analyzer consumes only this interface.**
  - `open_local_repository(path, *, allowed_roots) -> Workspace`

- [ ] **Step 1: Write the failing test `backend/tests/unit/test_workspace.py`**

```python
import subprocess
from pathlib import Path

import pytest

from upgradepilot.models.errors import RepoTooLargeError
from upgradepilot.services.repo.local import open_local_repository
from upgradepilot.services.repo.workspace import Workspace


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "demo"
    (root / "src" / "app").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "src" / "app" / "models.py").write_text("from pydantic import BaseModel\n")
    (root / "src" / "app" / "service.py").write_text("x = 1\n")
    (root / "tests" / "test_models.py").write_text("def test_x(): pass\n")
    (root / "README.md").write_text("# demo\n")
    return root


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
        env={
            "GIT_AUTHOR_NAME": "T",
            "GIT_AUTHOR_EMAIL": "t@e.com",
            "GIT_COMMITTER_NAME": "T",
            "GIT_COMMITTER_EMAIL": "t@e.com",
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "HOME": str(root),
        },
    )
    return result.stdout.strip()


@pytest.fixture
def git_repo(repo: Path) -> Path:
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "initial")
    (repo / "src" / "app" / "models.py").write_text(
        "from pydantic import BaseModel, validator\n"
    )
    _git(repo, "add", "src/app/models.py")
    _git(repo, "commit", "-q", "-m", "add validator import")
    return repo


# --- iteration ------------------------------------------------------------

def test_iter_files_returns_relative_python_paths(repo: Path) -> None:
    workspace = Workspace(root=repo)
    found = sorted(str(p) for p in workspace.iter_files(".py"))
    assert found == ["src/app/models.py", "src/app/service.py", "tests/test_models.py"]


def test_iter_files_skips_the_git_directory(git_repo: Path) -> None:
    workspace = Workspace(root=git_repo)
    assert not any(".git" in str(p) for p in workspace.iter_files(".py"))


def test_iter_files_skips_vendor_directories(repo: Path) -> None:
    (repo / "node_modules" / "pkg").mkdir(parents=True)
    (repo / "node_modules" / "pkg" / "x.py").write_text("y = 2\n")
    (repo / ".venv" / "lib").mkdir(parents=True)
    (repo / ".venv" / "lib" / "z.py").write_text("z = 3\n")

    workspace = Workspace(root=repo)
    found = {str(p) for p in workspace.iter_files(".py")}
    assert not any(p.startswith(("node_modules", ".venv")) for p in found)


def test_iter_files_skips_symlinks_escaping_the_root(repo: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.py").write_text("token = 'leak'\n")
    (repo / "linked").symlink_to(outside, target_is_directory=True)

    workspace = Workspace(root=repo)
    found = {str(p) for p in workspace.iter_files(".py")}
    assert "linked/secret.py" not in found


# --- reading --------------------------------------------------------------

def test_read_text_returns_file_contents(repo: Path) -> None:
    workspace = Workspace(root=repo)
    assert "BaseModel" in workspace.read_text(Path("src/app/models.py"))


def test_read_text_rejects_escaping_the_root(repo: Path) -> None:
    workspace = Workspace(root=repo)
    with pytest.raises(ValueError, match="outside the workspace"):
        workspace.read_text(Path("../outside.py"))


# --- caps -----------------------------------------------------------------

def test_enforce_caps_passes_within_limits(repo: Path) -> None:
    Workspace(root=repo).enforce_caps(max_files=100, max_bytes=1_000_000)


def test_enforce_caps_rejects_too_many_files(repo: Path) -> None:
    with pytest.raises(RepoTooLargeError, match="files"):
        Workspace(root=repo).enforce_caps(max_files=2, max_bytes=1_000_000)


def test_enforce_caps_rejects_too_many_bytes(repo: Path) -> None:
    with pytest.raises(RepoTooLargeError, match="large"):
        Workspace(root=repo).enforce_caps(max_files=100, max_bytes=10)


# --- git ------------------------------------------------------------------

def test_git_log_returns_commits_newest_first_with_touched_files(git_repo: Path) -> None:
    workspace = Workspace(root=git_repo)
    commits = workspace.git_log(limit=10)

    assert len(commits) == 2
    assert commits[0].files == ("src/app/models.py",)
    assert commits[0].timestamp >= commits[1].timestamp
    assert len(commits[0].sha) == 40


def test_git_log_is_empty_when_there_is_no_git_history(repo: Path) -> None:
    assert Workspace(root=repo).git_log() == []


def test_commit_sha_is_read_for_a_git_repository(git_repo: Path) -> None:
    workspace = open_local_repository(str(git_repo), allowed_roots=[git_repo.parent])
    assert workspace.commit_sha is not None
    assert len(workspace.commit_sha) == 40


def test_commit_sha_is_none_without_git(repo: Path) -> None:
    workspace = open_local_repository(str(repo), allowed_roots=[repo.parent])
    assert workspace.commit_sha is None


# --- lifecycle ------------------------------------------------------------

def test_local_workspace_is_never_deleted_on_cleanup(repo: Path) -> None:
    """A user's own checkout is used in place and must survive cleanup."""
    workspace = open_local_repository(str(repo), allowed_roots=[repo.parent])
    workspace.cleanup()
    assert repo.exists()


def test_cleanup_removes_an_owned_temp_directory(tmp_path: Path) -> None:
    owned = tmp_path / "cloned"
    (owned / "src").mkdir(parents=True)
    (owned / "src" / "a.py").write_text("a = 1\n")

    workspace = Workspace(root=owned, cleanup_dir=owned)
    workspace.cleanup()
    assert not owned.exists()


def test_context_manager_cleans_up_owned_directories(tmp_path: Path) -> None:
    owned = tmp_path / "cloned"
    owned.mkdir()
    with Workspace(root=owned, cleanup_dir=owned) as workspace:
        assert workspace.root.exists()
    assert not owned.exists()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_workspace.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'upgradepilot.services.repo.workspace'`.

- [ ] **Step 3: Write `backend/src/upgradepilot/services/repo/workspace.py`**

```python
"""The only view of a repository the analyzer ever gets.

Local checkouts and shallow clones both resolve to a Workspace, so nothing
downstream needs to know where the code came from.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType

from upgradepilot.models.errors import RepoTooLargeError
from upgradepilot.models.repo import CommitRecord

SKIP_DIRECTORIES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "dist",
        "build",
        ".tox",
        "site-packages",
    }
)

_GIT_TIMEOUT_SECONDS = 30


class Workspace:
    """A readable repository rooted at `root`.

    `cleanup_dir` is set only when this process created the directory (a
    clone). A user's own checkout is used in place and is never deleted.
    """

    def __init__(
        self,
        root: Path,
        commit_sha: str | None = None,
        cleanup_dir: Path | None = None,
    ) -> None:
        self._root = root.resolve()
        self._commit_sha = commit_sha
        self._cleanup_dir = cleanup_dir.resolve() if cleanup_dir else None

    @property
    def root(self) -> Path:
        return self._root

    @property
    def commit_sha(self) -> str | None:
        return self._commit_sha

    def iter_files(self, suffix: str = ".py") -> Iterator[Path]:
        """Yield repo-relative paths, skipping vendor dirs and escaping symlinks."""
        for path in sorted(self._root.rglob(f"*{suffix}")):
            relative = path.relative_to(self._root)
            if any(part in SKIP_DIRECTORIES for part in relative.parts):
                continue
            try:
                real = path.resolve(strict=True)
            except (OSError, RuntimeError):
                continue
            if real != self._root and self._root not in real.parents:
                continue  # symlink escaping the workspace
            if not real.is_file():
                continue
            yield relative

    def read_text(self, relative: Path) -> str:
        """Read a repo-relative file as UTF-8.

        A UnicodeDecodeError is allowed to propagate: Phase 2 records the
        file as skipped rather than silently mangling its contents.
        """
        target = (self._root / relative).resolve()
        if target != self._root and self._root not in target.parents:
            raise ValueError(f"{relative} resolves outside the workspace")
        return target.read_text(encoding="utf-8")

    def enforce_caps(self, max_files: int, max_bytes: int) -> None:
        """Reject oversized repositories before any analysis begins."""
        count = 0
        total = 0
        for relative in self.iter_files(".py"):
            count += 1
            total += (self._root / relative).stat().st_size
            if count > max_files:
                raise RepoTooLargeError(
                    f"This repository has more than {max_files} Python files.",
                    detail=f"root={self._root}",
                )
            if total > max_bytes:
                raise RepoTooLargeError(
                    "This repository's Python sources are too large to analyze.",
                    detail=f"bytes>{max_bytes} root={self._root}",
                )

    def git_log(self, limit: int = 100) -> list[CommitRecord]:
        """Recent commits with the files each touched. Empty when not a git repo.

        One subprocess call, not one per file.
        """
        if not (self._root / ".git").exists():
            return []

        completed = subprocess.run(
            [
                "git",
                "log",
                f"-n{limit}",
                "--name-only",
                "--no-merges",
                "--format=__commit__%H|%ct",
            ],
            cwd=self._root,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
        )
        if completed.returncode != 0:
            return []

        records: list[CommitRecord] = []
        sha: str | None = None
        timestamp: datetime | None = None
        files: list[str] = []

        def flush() -> None:
            if sha and timestamp:
                records.append(
                    CommitRecord(sha=sha, timestamp=timestamp, files=tuple(sorted(set(files))))
                )

        for line in completed.stdout.splitlines():
            if line.startswith("__commit__"):
                flush()
                raw_sha, _, raw_ts = line.removeprefix("__commit__").partition("|")
                sha = raw_sha
                timestamp = datetime.fromtimestamp(int(raw_ts), tz=UTC)
                files = []
            elif line.strip():
                files.append(line.strip())
        flush()
        return records

    def cleanup(self) -> None:
        if self._cleanup_dir and self._cleanup_dir.exists():
            shutil.rmtree(self._cleanup_dir, ignore_errors=True)

    def __enter__(self) -> Workspace:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.cleanup()
```

**Known limitation, accepted for Phase 1:** `iter_files` uses `rglob`, which walks the whole tree before `SKIP_DIRECTORIES` filters it, so a repository with a large committed `.venv` costs a full directory walk. The file and byte caps bound the damage, and correctness is unaffected. Revisit with an `os.walk` that prunes in place only if profiling in Phase 2 shows it matters.

- [ ] **Step 4: Write `backend/src/upgradepilot/services/repo/local.py`**

```python
"""Local-path resolver: use a checkout in place, read-only."""

import subprocess
from collections.abc import Sequence
from pathlib import Path

from upgradepilot.services.repo.guards import resolve_local_path
from upgradepilot.services.repo.workspace import Workspace

_GIT_TIMEOUT_SECONDS = 15


def read_commit_sha(root: Path) -> str | None:
    """Current HEAD sha, or None when the directory is not a git repository."""
    if not (root / ".git").exists():
        return None
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_SECONDS,
        check=False,
    )
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


def open_local_repository(path: str, *, allowed_roots: Sequence[Path]) -> Workspace:
    """Open a local checkout as a Workspace. Never deletes the directory."""
    root = resolve_local_path(path, allowed_roots)
    return Workspace(root=root, commit_sha=read_commit_sha(root), cleanup_dir=None)
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_workspace.py -v`
Expected: 17 passed.

- [ ] **Step 6: Lint, typecheck, commit**

```bash
cd /Users/nzrsrd/Code/upgrade_pilot/backend
.venv/bin/ruff check src tests && .venv/bin/mypy
cd /Users/nzrsrd/Code/upgrade_pilot
git add backend/src/upgradepilot/services backend/tests/unit/test_workspace.py
git commit -m "feat(repo): add Workspace abstraction and local-path resolver

Workspace is the analyzer's only view of a repository: relative-path
iteration that skips vendor dirs and escaping symlinks, capped scanning,
and git history from a single subprocess call. Local checkouts are used
in place and never deleted."
```

---

### Task 11: Shallow-clone resolver

**Files:**
- Create: `backend/src/upgradepilot/services/repo/clone.py`
- Test: `backend/tests/unit/test_clone.py`

**Interfaces:**
- Consumes: `guards.validate_clone_url`, `workspace.Workspace`, `local.read_commit_sha`.
- Produces: `clone_repository(url, dest_parent, *, depth, allowed_schemes, allowed_local_roots, timeout=180) -> Workspace` returning a Workspace whose `cleanup_dir` is the created directory.
- `allowed_local_roots` was added after this plan was written and is **required**, not defaulted: `ALLOWED_LOCAL_ROOTS` now confines `file://` clone URLs as well as local-path refs, since a `file://` URL is a local-disk read that git resolves ignoring the URL's host. Requiring the argument forces every call site to state its filesystem policy; a default would let a new one silently inherit one. It is consulted only for a `file://` URL.

Tests clone from a **local bare repository over `file://`**, injecting `allowed_schemes={"file"}`. No network, so tests stay hermetic and fast.

- [ ] **Step 1: Write the failing test `backend/tests/unit/test_clone.py`**

```python
import subprocess
from pathlib import Path

import pytest

from upgradepilot.models.errors import InvalidRepoUrlError, RepoUnavailableError
from upgradepilot.services.repo.clone import clone_repository

FILE_SCHEME = frozenset({"file"})


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
        env={
            "GIT_AUTHOR_NAME": "T",
            "GIT_AUTHOR_EMAIL": "t@e.com",
            "GIT_COMMITTER_NAME": "T",
            "GIT_COMMITTER_EMAIL": "t@e.com",
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "HOME": str(cwd),
        },
    )


@pytest.fixture
def origin(tmp_path: Path) -> Path:
    """A real git repository with three commits, served over file://."""
    source = tmp_path / "origin"
    (source / "src").mkdir(parents=True)
    _git(source, "init", "-q", "-b", "main")
    for index in range(3):
        (source / "src" / f"mod{index}.py").write_text(f"value = {index}\n")
        _git(source, "add", ".")
        _git(source, "commit", "-q", "-m", f"commit {index}")
    return source


def test_clone_produces_a_readable_workspace(origin: Path, tmp_path: Path) -> None:
    workspace = clone_repository(
        f"file://{origin}",
        tmp_path / "workspaces",
        depth=10,
        allowed_schemes=FILE_SCHEME,
    )
    try:
        files = sorted(str(p) for p in workspace.iter_files(".py"))
        assert files == ["src/mod0.py", "src/mod1.py", "src/mod2.py"]
        assert workspace.commit_sha is not None
        assert len(workspace.commit_sha) == 40
    finally:
        workspace.cleanup()


def test_clone_retains_history_for_churn_signals(origin: Path, tmp_path: Path) -> None:
    """Depth must exceed 1 or git_log yields nothing useful."""
    workspace = clone_repository(
        f"file://{origin}", tmp_path / "workspaces", depth=10, allowed_schemes=FILE_SCHEME
    )
    try:
        assert len(workspace.git_log(limit=10)) == 3
    finally:
        workspace.cleanup()


def test_clone_respects_the_requested_depth(origin: Path, tmp_path: Path) -> None:
    workspace = clone_repository(
        f"file://{origin}", tmp_path / "workspaces", depth=1, allowed_schemes=FILE_SCHEME
    )
    try:
        assert len(workspace.git_log(limit=10)) == 1
    finally:
        workspace.cleanup()


def test_cleanup_removes_the_cloned_directory(origin: Path, tmp_path: Path) -> None:
    workspace = clone_repository(
        f"file://{origin}", tmp_path / "workspaces", depth=5, allowed_schemes=FILE_SCHEME
    )
    root = workspace.root
    assert root.exists()
    workspace.cleanup()
    assert not root.exists()


def test_context_manager_cleans_up(origin: Path, tmp_path: Path) -> None:
    with clone_repository(
        f"file://{origin}", tmp_path / "workspaces", depth=5, allowed_schemes=FILE_SCHEME
    ) as workspace:
        root = workspace.root
        assert root.exists()
    assert not root.exists()


def test_missing_repository_raises_repo_unavailable(tmp_path: Path) -> None:
    with pytest.raises(RepoUnavailableError) as excinfo:
        clone_repository(
            f"file://{tmp_path / 'nonexistent'}",
            tmp_path / "workspaces",
            depth=5,
            allowed_schemes=FILE_SCHEME,
        )
    assert excinfo.value.detail is not None, "git stderr must be preserved for logs"


def test_disallowed_scheme_is_rejected_before_any_subprocess(tmp_path: Path) -> None:
    with pytest.raises(InvalidRepoUrlError):
        clone_repository(
            "ssh://git@github.com/acme/repo.git",
            tmp_path / "workspaces",
            depth=5,
            allowed_schemes=frozenset({"https"}),
        )


def test_failed_clone_leaves_no_partial_directory(tmp_path: Path) -> None:
    workspaces = tmp_path / "workspaces"
    with pytest.raises(RepoUnavailableError):
        clone_repository(
            f"file://{tmp_path / 'nonexistent'}",
            workspaces,
            depth=5,
            allowed_schemes=FILE_SCHEME,
        )
    leftovers = list(workspaces.iterdir()) if workspaces.exists() else []
    assert leftovers == []


def test_each_clone_gets_its_own_directory(origin: Path, tmp_path: Path) -> None:
    first = clone_repository(
        f"file://{origin}", tmp_path / "workspaces", depth=2, allowed_schemes=FILE_SCHEME
    )
    second = clone_repository(
        f"file://{origin}", tmp_path / "workspaces", depth=2, allowed_schemes=FILE_SCHEME
    )
    try:
        assert first.root != second.root
    finally:
        first.cleanup()
        second.cleanup()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_clone.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'upgradepilot.services.repo.clone'`.

- [ ] **Step 3: Write `backend/src/upgradepilot/services/repo/clone.py`**

```python
"""Shallow-clone resolver for public repositories.

Depth defaults above 1 because churn signals need history. Credential
prompting is disabled so a private repository fails fast rather than
hanging the run waiting on stdin.
"""

import shutil
import subprocess
import uuid
from pathlib import Path

from upgradepilot.models.errors import RepoUnavailableError
from upgradepilot.services.repo.guards import validate_clone_url
from upgradepilot.services.repo.local import read_commit_sha
from upgradepilot.services.repo.workspace import Workspace

_NON_INTERACTIVE_GIT_ENV = {
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_ASKPASS": "/usr/bin/true",
    "GIT_CONFIG_NOSYSTEM": "1",
    "PATH": "/usr/bin:/bin:/usr/local/bin",
}


def clone_repository(
    url: str,
    dest_parent: Path,
    *,
    depth: int,
    allowed_schemes: frozenset[str],
    timeout: int = 180,
) -> Workspace:
    """Shallow-clone `url` into a fresh directory under `dest_parent`."""
    safe_url = validate_clone_url(url, allowed_schemes)

    dest_parent.mkdir(parents=True, exist_ok=True)
    destination = dest_parent / f"repo-{uuid.uuid4().hex[:12]}"

    command = [
        "git",
        "clone",
        "--depth",
        str(max(1, depth)),
        "--single-branch",
        "--quiet",
        safe_url,
        str(destination),
    ]

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=_NON_INTERACTIVE_GIT_ENV,
        )
    except subprocess.TimeoutExpired as exc:
        shutil.rmtree(destination, ignore_errors=True)
        raise RepoUnavailableError(
            "Cloning the repository timed out.",
            detail=f"url={safe_url} timeout={timeout}s",
        ) from exc

    if completed.returncode != 0:
        shutil.rmtree(destination, ignore_errors=True)
        raise RepoUnavailableError(
            "The repository could not be cloned. Check that the URL is correct "
            "and the repository is public.",
            detail=f"url={safe_url} exit={completed.returncode} stderr={completed.stderr.strip()}",
        )

    return Workspace(
        root=destination,
        commit_sha=read_commit_sha(destination),
        cleanup_dir=destination,
    )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_clone.py -v`
Expected: 9 passed.

- [ ] **Step 5: Lint, typecheck, commit**

```bash
cd /Users/nzrsrd/Code/upgrade_pilot/backend
.venv/bin/ruff check src tests && .venv/bin/mypy
cd /Users/nzrsrd/Code/upgrade_pilot
git add backend/src/upgradepilot/services/repo/clone.py backend/tests/unit/test_clone.py
git commit -m "feat(repo): add shallow-clone resolver

Validates the URL before spawning git, disables credential prompting so
private repos fail fast, removes partial directories on failure, and keeps
enough history for churn signals. Tested hermetically over file://."
```

---

### Task 12: Workspace lifecycle and the unified entry point

**Files:**
- Create: `backend/src/upgradepilot/services/repo/manager.py`
- Test: `backend/tests/unit/test_workspace_manager.py`

**Interfaces:**
- Consumes: `clone_repository`, `open_local_repository`, `Settings`, `RepoRef`.
- Produces: `WorkspaceManager(settings)` with `open(ref) -> Workspace` and `sweep_stale(max_age_seconds) -> list[Path]`. **This is the single entry point Phase 2's analyzer and Phase 9's API both call** — neither imports `clone.py` or `local.py` directly.

- [ ] **Step 1: Write the failing test `backend/tests/unit/test_workspace_manager.py`**

```python
import os
import subprocess
import time
from pathlib import Path

import pytest

from upgradepilot.config import Settings
from upgradepilot.models.errors import LocalPathForbiddenError, RepoTooLargeError
from upgradepilot.models.inputs import LocalRepoRef, RemoteRepoRef
from upgradepilot.services.repo.manager import WorkspaceManager


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        check=True,
        env={
            "GIT_AUTHOR_NAME": "T",
            "GIT_AUTHOR_EMAIL": "t@e.com",
            "GIT_COMMITTER_NAME": "T",
            "GIT_COMMITTER_EMAIL": "t@e.com",
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "HOME": str(cwd),
        },
    )


@pytest.fixture
def origin(tmp_path: Path) -> Path:
    source = tmp_path / "origin"
    (source / "src").mkdir(parents=True)
    (source / "src" / "a.py").write_text("a = 1\n")
    _git(source, "init", "-q", "-b", "main")
    _git(source, "add", ".")
    _git(source, "commit", "-q", "-m", "initial")
    return source


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        allowed_local_roots=(tmp_path,),
        allowed_url_schemes=frozenset({"file"}),
        workspace_dir=tmp_path / "workspaces",
        max_repo_files=100,
        max_repo_bytes=1_000_000,
        clone_depth=5,
    )


def test_open_dispatches_on_a_local_ref(origin: Path, settings: Settings) -> None:
    with WorkspaceManager(settings).open(LocalRepoRef(path=str(origin))) as workspace:
        assert workspace.root == origin.resolve()
        assert sorted(str(p) for p in workspace.iter_files(".py")) == ["src/a.py"]


def test_open_dispatches_on_a_remote_ref(origin: Path, settings: Settings) -> None:
    with WorkspaceManager(settings).open(RemoteRepoRef(url=f"file://{origin}")) as workspace:
        assert workspace.root != origin.resolve(), "a clone must not alias the origin"
        assert sorted(str(p) for p in workspace.iter_files(".py")) == ["src/a.py"]


def test_open_enforces_caps_before_returning(origin: Path, tmp_path: Path) -> None:
    strict = Settings(
        allowed_local_roots=(tmp_path,),
        allowed_url_schemes=frozenset({"file"}),
        workspace_dir=tmp_path / "workspaces",
        max_repo_files=0,
        max_repo_bytes=1_000_000,
    )
    with pytest.raises(RepoTooLargeError):
        WorkspaceManager(strict).open(LocalRepoRef(path=str(origin)))


def test_open_propagates_guard_failures(tmp_path: Path, settings: Settings) -> None:
    outside = tmp_path.parent / "not-allowed"
    outside.mkdir(exist_ok=True)
    with pytest.raises(LocalPathForbiddenError):
        WorkspaceManager(settings).open(LocalRepoRef(path=str(outside)))


def test_a_failed_clone_does_not_leak_a_workspace(tmp_path: Path, settings: Settings) -> None:
    from upgradepilot.models.errors import RepoUnavailableError

    with pytest.raises(RepoUnavailableError):
        WorkspaceManager(settings).open(RemoteRepoRef(url=f"file://{tmp_path / 'missing'}"))
    workspaces = settings.workspace_dir
    assert not workspaces.exists() or list(workspaces.iterdir()) == []


def test_sweep_stale_removes_only_old_directories(tmp_path: Path, settings: Settings) -> None:
    settings.workspace_dir.mkdir(parents=True)
    old = settings.workspace_dir / "repo-old"
    fresh = settings.workspace_dir / "repo-fresh"
    old.mkdir()
    fresh.mkdir()
    stale_time = time.time() - 7200
    os.utime(old, (stale_time, stale_time))

    removed = WorkspaceManager(settings).sweep_stale(max_age_seconds=3600)

    assert removed == [old]
    assert not old.exists()
    assert fresh.exists()


def test_sweep_stale_is_safe_when_nothing_exists(settings: Settings) -> None:
    assert WorkspaceManager(settings).sweep_stale(max_age_seconds=60) == []


def test_sweep_stale_ignores_unrelated_entries(tmp_path: Path, settings: Settings) -> None:
    """Only directories this service created (repo-*) are ever deleted."""
    settings.workspace_dir.mkdir(parents=True)
    unrelated = settings.workspace_dir / "important.txt"
    unrelated.write_text("keep me\n")
    stale_time = time.time() - 7200
    os.utime(unrelated, (stale_time, stale_time))

    assert WorkspaceManager(settings).sweep_stale(max_age_seconds=3600) == []
    assert unrelated.exists()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_workspace_manager.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'upgradepilot.services.repo.manager'`.

- [ ] **Step 3: Write `backend/src/upgradepilot/services/repo/manager.py`**

```python
"""Single entry point for repository access.

Callers pass a RepoRef and receive a Workspace. Nothing outside this module
needs to know whether the code was cloned or read in place.
"""

import shutil
import time
from pathlib import Path

from upgradepilot.config import Settings
from upgradepilot.models.inputs import LocalRepoRef, RemoteRepoRef, RepoRef
from upgradepilot.services.repo.clone import clone_repository
from upgradepilot.services.repo.local import open_local_repository
from upgradepilot.services.repo.workspace import Workspace

_OWNED_PREFIX = "repo-"


class WorkspaceManager:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def open(self, ref: RepoRef) -> Workspace:
        """Resolve a RepoRef to a capped, ready-to-analyze Workspace."""
        if isinstance(ref, LocalRepoRef):
            workspace = open_local_repository(
                ref.path, allowed_roots=list(self._settings.allowed_local_roots)
            )
        elif isinstance(ref, RemoteRepoRef):
            workspace = clone_repository(
                ref.url,
                self._settings.workspace_dir,
                depth=self._settings.clone_depth,
                allowed_schemes=self._settings.allowed_url_schemes,
            )
        else:  # pragma: no cover - the union is closed
            raise TypeError(f"unsupported repository reference: {type(ref).__name__}")

        try:
            workspace.enforce_caps(
                max_files=self._settings.max_repo_files,
                max_bytes=self._settings.max_repo_bytes,
            )
        except Exception:
            # Never leave a clone behind when the caps reject it.
            workspace.cleanup()
            raise
        return workspace

    def sweep_stale(self, max_age_seconds: int) -> list[Path]:
        """Remove workspaces this service created and then abandoned.

        Run at startup: a crash mid-run leaves clones on disk. Only
        directories matching the owned prefix are ever removed.
        """
        root = self._settings.workspace_dir
        if not root.exists():
            return []

        cutoff = time.time() - max_age_seconds
        removed: list[Path] = []
        for entry in sorted(root.iterdir()):
            if not entry.is_dir() or not entry.name.startswith(_OWNED_PREFIX):
                continue
            if entry.stat().st_mtime < cutoff:
                shutil.rmtree(entry, ignore_errors=True)
                removed.append(entry)
        return removed
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_workspace_manager.py -v`
Expected: 8 passed.

- [ ] **Step 5: Lint, typecheck, commit**

```bash
cd /Users/nzrsrd/Code/upgrade_pilot/backend
.venv/bin/ruff check src tests && .venv/bin/mypy
cd /Users/nzrsrd/Code/upgrade_pilot
git add backend/src/upgradepilot/services/repo/manager.py backend/tests/unit/test_workspace_manager.py
git commit -m "feat(repo): add WorkspaceManager as the single repository entry point

Dispatches on RepoRef, enforces size caps before returning, cleans up a
clone whose caps reject it, and sweeps abandoned workspaces left by a
crashed run. Only directories it created are ever deleted."
```

---

### Task 13: Fixture repository and git-history builder

Phase 2's analyzer needs a repository whose every usage pattern is known exactly. This task authors it.

**Deviation from spec §12 assumption 5, to be recorded:** the spec says a real public repository is vendored and pinned for analyzer tests. Instead, unit tests use a hand-authored miniature project so assertions stay small, readable, and precisely targeted at each usage pattern; a real public repository is pinned in Phase 12 for the demo and E2E path. Vendoring a real repository into unit tests would make assertions large and brittle without testing anything additional.

**Files:**
- Create: `backend/tests/fixtures/__init__.py`
- Create: `backend/tests/fixtures/repo_builder.py`
- Create: `backend/tests/fixtures/sample_repo/pyproject.toml`
- Create: `backend/tests/fixtures/sample_repo/requirements.txt`
- Create: `backend/tests/fixtures/sample_repo/src/app/__init__.py`
- Create: `backend/tests/fixtures/sample_repo/src/app/models.py`
- Create: `backend/tests/fixtures/sample_repo/src/app/service.py`
- Create: `backend/tests/fixtures/sample_repo/src/app/util.py`
- Create: `backend/tests/fixtures/sample_repo/src/app/broken.py.txt`
- Create: `backend/tests/fixtures/sample_repo/tests/test_models.py`
- Modify: `backend/pyproject.toml` (exclude the fixture tree from ruff and pytest collection)
- Test: `backend/tests/unit/test_fixture_repo.py`

**Interfaces:**
- Produces: `build_sample_repo(tmp_path) -> Path` — copies the fixture tree, renames `broken.py.txt` to `broken.py`, and creates two real commits so churn is testable. **Phase 2's analyzer tests all use this.** Also `SAMPLE_REPO_DIR: Path` and the documented expectation constants below.

**Why `broken.py.txt`:** the fixture deliberately contains a file with a syntax error, to prove the analyzer records a `SkippedFile` instead of crashing. Stored with a `.txt` suffix so ruff and pytest never try to parse it in this repository; `build_sample_repo` renames it during the copy.

- [ ] **Step 1: Write the fixture source files**

`backend/tests/fixtures/sample_repo/pyproject.toml`:

```toml
[project]
name = "sample-app"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "pydantic>=1.10,<2",
    "requests>=2.31",
]
```

`backend/tests/fixtures/sample_repo/requirements.txt`:

```
pydantic==1.10.13
requests==2.31.0
```

`backend/tests/fixtures/sample_repo/src/app/models.py` — high-confidence patterns:

```python
"""Pydantic v1 models exercising the high-confidence usage patterns."""

from typing import Optional

from pydantic import BaseModel, validator


class Customer(BaseModel):
    id: int
    email: str
    nickname: Optional[str]          # v1: implicitly optional; v2: required

    class Config:                     # v2: model_config = ConfigDict(...)
        orm_mode = True
        allow_mutation = False

    @validator("email")               # v2: @field_validator
    def email_must_contain_at(cls, value: str) -> str:
        if "@" not in value:
            raise ValueError("invalid email")
        return value


class Invoice(BaseModel):
    number: str
    customer: Customer
    note: Optional[str] = None        # explicit default: unaffected

    class Config:
        orm_mode = True
```

`backend/tests/fixtures/sample_repo/src/app/service.py` — medium-confidence method calls:

```python
"""Method calls on models: medium confidence (generic names, pydantic in scope)."""

import json

from app.models import Customer, Invoice


def serialize(invoice: Invoice) -> str:
    return json.dumps(invoice.dict())          # v2: model_dump()


def load(raw: dict) -> Customer:
    return Customer.parse_obj(raw)             # v2: model_validate()


def schema_of() -> dict:
    return Invoice.schema()                    # v2: model_json_schema()


def duplicate(invoice: Invoice) -> Invoice:
    return invoice.copy()                      # v2: model_copy()
```

`backend/tests/fixtures/sample_repo/src/app/util.py` — the low-confidence trap:

```python
"""No pydantic import. `.dict()` here is a false positive if graded highly."""


class Bag:
    def __init__(self, items: dict) -> None:
        self._items = items

    def dict(self) -> dict:
        return dict(self._items)


def flatten(bag: Bag) -> dict:
    return bag.dict()          # NOT a pydantic call
```

`backend/tests/fixtures/sample_repo/src/app/broken.py.txt` — deliberately unparseable:

```python
def broken(
    this file is intentionally not valid Python
```

`backend/tests/fixtures/sample_repo/tests/test_models.py`:

```python
from app.models import Customer


def test_customer_validates_email() -> None:
    customer = Customer(id=1, email="a@b.com", nickname=None)
    assert customer.email == "a@b.com"
```

`backend/tests/fixtures/sample_repo/src/app/__init__.py`: empty file.

- [ ] **Step 2: Write `backend/tests/fixtures/repo_builder.py`**

```python
"""Builds the sample repository into a temp directory with real git history.

The fixture tree is copied rather than used in place so tests never mutate
a checked-in directory, and so `broken.py.txt` can be renamed into a real
`.py` file that ruff and pytest in *this* repository never see.
"""

import shutil
import subprocess
from pathlib import Path

SAMPLE_REPO_DIR = Path(__file__).parent / "sample_repo"

# Documented expectations, asserted in test_fixture_repo.py and reused by
# Phase 2's analyzer tests.
EXPECTED_PYTHON_FILES = 6          # 4 in src/app (incl. __init__), broken.py, 1 test
EXPECTED_HIGH_CONFIDENCE_SYMBOLS = ("Config", "validator")
EXPECTED_MEDIUM_CONFIDENCE_SYMBOLS = ("copy", "dict", "parse_obj", "schema")
EXPECTED_UNPARSEABLE = "src/app/broken.py"
EXPECTED_DECLARED_SPECIFIER = ">=1.10,<2"
EXPECTED_PINNED_VERSION = "1.10.13"

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "Fixture",
    "GIT_AUTHOR_EMAIL": "fixture@example.com",
    "GIT_COMMITTER_NAME": "Fixture",
    "GIT_COMMITTER_EMAIL": "fixture@example.com",
    "GIT_CONFIG_NOSYSTEM": "1",
    "PATH": "/usr/bin:/bin:/usr/local/bin",
}


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
        env={**_GIT_ENV, "HOME": str(root)},
    )


def build_sample_repo(tmp_path: Path) -> Path:
    """Copy the fixture tree to `tmp_path` and give it two real commits.

    Two commits, not one, so churn signals are testable: the second touches
    only `models.py`, which is the file the analyzer should flag as both
    affected and recently changed.
    """
    root = tmp_path / "sample_repo"
    shutil.copytree(SAMPLE_REPO_DIR, root)

    broken_source = root / "src" / "app" / "broken.py.txt"
    broken_source.rename(root / "src" / "app" / "broken.py")

    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "initial import")

    models = root / "src" / "app" / "models.py"
    models.write_text(models.read_text() + "\n\nMAX_INVOICES = 100\n")
    _git(root, "add", "src/app/models.py")
    _git(root, "commit", "-q", "-m", "add invoice cap")

    return root
```

Also create an empty `backend/tests/fixtures/__init__.py`.

- [ ] **Step 3: Exclude the fixture tree in `backend/pyproject.toml`**

Add to `[tool.pytest.ini_options]`:

```toml
norecursedirs = ["tests/fixtures/sample_repo"]
```

Add to `[tool.ruff]`:

```toml
extend-exclude = ["tests/fixtures/sample_repo"]
```

- [ ] **Step 4: Write the test `backend/tests/unit/test_fixture_repo.py`**

```python
"""Guards the fixture repository's shape.

Phase 2's analyzer tests assert exact counts against this tree. If someone
edits a fixture file without updating the expectations, this fails here
rather than producing a confusing analyzer failure later.
"""

import ast
from pathlib import Path

from tests.fixtures.repo_builder import (
    EXPECTED_PINNED_VERSION,
    EXPECTED_PYTHON_FILES,
    EXPECTED_UNPARSEABLE,
    build_sample_repo,
)
from upgradepilot.services.repo.workspace import Workspace


def test_build_produces_the_expected_python_files(tmp_path: Path) -> None:
    root = build_sample_repo(tmp_path)
    files = sorted(str(p) for p in Workspace(root=root).iter_files(".py"))

    assert len(files) == EXPECTED_PYTHON_FILES
    assert "src/app/models.py" in files
    assert "src/app/util.py" in files
    assert EXPECTED_UNPARSEABLE in files
    assert not any(f.endswith(".py.txt") for f in files)


def test_the_broken_file_is_genuinely_unparseable(tmp_path: Path) -> None:
    root = build_sample_repo(tmp_path)
    source = Workspace(root=root).read_text(Path(EXPECTED_UNPARSEABLE))

    try:
        ast.parse(source)
    except SyntaxError:
        return
    raise AssertionError("fixture broken.py must not parse")


def test_every_other_file_parses(tmp_path: Path) -> None:
    root = build_sample_repo(tmp_path)
    workspace = Workspace(root=root)

    for relative in workspace.iter_files(".py"):
        if str(relative) == EXPECTED_UNPARSEABLE:
            continue
        ast.parse(workspace.read_text(relative))


def test_manifests_declare_a_pydantic_v1_dependency(tmp_path: Path) -> None:
    root = build_sample_repo(tmp_path)

    assert "pydantic" in (root / "pyproject.toml").read_text()
    assert EXPECTED_PINNED_VERSION in (root / "requirements.txt").read_text()


def test_history_has_two_commits_and_recent_churn_on_models(tmp_path: Path) -> None:
    root = build_sample_repo(tmp_path)
    commits = Workspace(root=root).git_log(limit=10)

    assert len(commits) == 2
    assert commits[0].files == ("src/app/models.py",)


def test_util_module_does_not_import_pydantic(tmp_path: Path) -> None:
    """The low-confidence trap: a .dict() call with no pydantic in scope."""
    root = build_sample_repo(tmp_path)
    source = Workspace(root=root).read_text(Path("src/app/util.py"))

    assert "pydantic" not in source
    assert ".dict()" in source


def test_builds_are_independent(tmp_path: Path) -> None:
    first = build_sample_repo(tmp_path / "a")
    second = build_sample_repo(tmp_path / "b")

    (first / "src" / "app" / "models.py").write_text("mutated = True\n")
    assert "mutated" not in (second / "src" / "app" / "models.py").read_text()
```

- [ ] **Step 5: Run the test to verify it fails, then passes**

```bash
cd /Users/nzrsrd/Code/upgrade_pilot/backend
mkdir -p tests/fixtures
.venv/bin/python -m pytest tests/unit/test_fixture_repo.py -v
```

Expected first run: FAIL — `ModuleNotFoundError: No module named 'tests.fixtures.repo_builder'` before the files exist. After Steps 1–3: 7 passed.

If `test_build_produces_the_expected_python_files` fails on the count, fix `EXPECTED_PYTHON_FILES` in `repo_builder.py` to the real number and note it — the constant exists to be accurate, not aspirational.

- [ ] **Step 6: Confirm the whole suite is green and the fixture is not linted**

```bash
cd /Users/nzrsrd/Code/upgrade_pilot/backend
.venv/bin/python -m pytest -v
.venv/bin/ruff check src tests
.venv/bin/mypy
```

Expected: everything passes; ruff reports no errors from `tests/fixtures/sample_repo` (it is excluded); live tests skipped.

- [ ] **Step 7: Record the fixture deviation in the spec**

In spec §12, replace assumption 5 with:

```markdown
5. Analyzer unit tests run against a hand-authored miniature project
   (`backend/tests/fixtures/sample_repo/`) built into a temp directory with
   real git history by `build_sample_repo()`. This keeps assertions small,
   readable, and precisely targeted at each usage pattern. A real public
   repository is pinned by commit in Phase 12 for the demo and end-to-end
   path.
```

- [ ] **Step 8: Update `PLANNING.md` and commit**

Tick the completed Phase 1 items. Leave unticked anything not demonstrated.

```bash
cd /Users/nzrsrd/Code/upgrade_pilot
git add backend/tests/fixtures backend/tests/unit/test_fixture_repo.py backend/pyproject.toml docs/ PLANNING.md
git commit -m "test(fixtures): add sample repository with real git history

Hand-authored miniature Pydantic v1 project covering high, medium, and
low confidence usage patterns plus a deliberately unparseable file. Stored
as broken.py.txt so this repo's linters never parse it; renamed on build.
Records the deviation from spec assumption 5 and why."
```

---

## Plan self-review

**1. Spec coverage for Phases 0–1.** Every Phase 0 and Phase 1 checklist item in `PLANNING.md` maps to a task: repository structure, backend, frontend, Tailwind, Lucide, pins, env config, `.env.example`, `.gitignore` (already committed), FastAPI app, health endpoint, both servers starting, toolchain verification, and all six probes → Tasks 1–6. Domain models, `RepoRef`→`Workspace`, both resolvers, all guards, cleanup and startup sweep, and the fixture repository → Tasks 7–13.

Two Phase 0 items are intentionally satisfied differently from their wording: the OpenAI *connectivity* probe and the *embedding* probe are folded into Task 5, and the embedding-cost probe may legitimately defer to Phase 3 (Task 6 Step 1 records which). Phase 1's "detect repository languages", "detect dependency manifests", "identify the requested dependency", "read installed version", "identify direct imports", and "calculate change indicators" are Phase **2** work in this plan's structure — the models that carry those results are built in Task 8, and `PLANNING.md` Phase 1 should be re-scoped to "domain models and repository access" when Task 13 updates it. Flag this to the user rather than silently reinterpreting the phase.

**2. Placeholder scan.** No `TBD`/`TODO`/"implement later"/"similar to Task N" remains. Two intentional fill-in points exist and are explicitly instructed rather than vague: ADR-001's findings table takes pasted probe output (Task 6 Step 1), and `EXPECTED_PYTHON_FILES` is to be corrected to the real count if it differs (Task 13 Step 5). Both name exactly what to do.

**3. Type consistency.** Checked across tasks:
- `Confidence` (not `ConfidenceLevel`) is used in `UsageSite`, `SymbolStat`, and every test.
- `SymbolInventory.from_sites(sites)` and `.high_confidence_symbols()` — same names in Task 8's implementation, its tests, and the Task 8 Interfaces block.
- `AffectedFile.from_sites(path, sites, *, is_test, commit_count, last_modified)` — keyword-only tail matches the test call.
- `Workspace(root, commit_sha=None, cleanup_dir=None)` — identical in Tasks 10, 11, 12, and 13's tests.
- `validate_clone_url(raw, allowed_schemes)` and `resolve_local_path(raw, allowed_roots)` — same arity in Task 9 and their callers in Tasks 10 and 11.
- `read_commit_sha(root)` is defined in `local.py` (Task 10) and imported by `clone.py` (Task 11) — the import direction is stated in Task 11's Interfaces block.
- `UpgradePilotError.to_app_error(node=None)` returns `AppError` with `code`/`message`/`detail`/`node`/`retryable` — matching `AppError`'s fields exactly.
- `Settings` field names used in `WorkspaceManager` (`allowed_local_roots`, `allowed_url_schemes`, `workspace_dir`, `max_repo_files`, `max_repo_bytes`, `clone_depth`) all exist in Task 1's `Settings`.

**4. Ordering.** No task depends on a later one. `models/errors.py` lands in Task 9, before its first consumer in Task 10. `Settings` lands in Task 1, before Task 12 consumes it.
