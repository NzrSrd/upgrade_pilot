# UpgradePilot

A dependency upgrade risk and migration planning tool. Give it a repository and a version change; it tells you what will break, how risky it is, what evidence supports that assessment, and what to do about it.

The distinguishing constraint: **every claim traces to a real line of code or a real source document.** File and line numbers come from AST analysis, not from a language model. Breaking changes come from retrieved documentation, not from a hardcoded table. Where evidence is missing, the report says so and caps its own confidence.

## Status

In development. See `PLANNING.md` for the current phase.

## How it works

```
repository ──► AST analysis ──┐
                              ├──► risk assessment ──► human decision ──► plan ──► validation
knowledge base ──► agentic RAG ┘        (when a genuine tradeoff exists)
```

Orchestrated with LangGraph; retrieval over ChromaDB; thread state checkpointed so a run can pause for a human decision and resume exactly where it stopped.

## Running it

Every command in this section has been run on this machine. Backend commands
are run from `backend/`, frontend commands from `frontend/`.

**Prerequisites:** Python **3.14 or newer** (the floor is enforced and not
negotiable — see `docs/adr/ADR-001-system-architecture.md` for why 3.12 was a
hang risk), and Node with `npm`. Verified against Python 3.14.5, Node v24.13.1,
npm 11.8.0. `uv` is *not* required and is not installed here; if you prefer it,
you are on your own — every command below uses the standard library's `venv`
and the venv's own interpreter explicitly.

### Backend

```bash
cd backend
python3 -m venv .venv                     # first time only
./.venv/bin/python -m pip install -e '.[dev]'
```

Then, from `backend/`:

```bash
./.venv/bin/python -m uvicorn upgradepilot.api.app:app --port 8000
```

Port 8000 matters: the frontend dev server proxies `/api` there. Check it:

```bash
curl http://127.0.0.1:8000/api/health
# {"status":"degraded","version":"0.1.0",
#  "checks":{"chroma_dir":true,"checkpoint_dir":true,"openai_configured":false}}
```

`degraded` with `openai_configured: false` is the correct answer when no
`OPENAI_API_KEY` is set — `status` is derived from the checks, so the endpoint
cannot report `ok` over a failing one. Copy `backend/.env.example` to
`backend/.env` and fill in the key to get `ok`.

### Frontend

```bash
cd frontend
npm install                               # first time only
npm run dev                               # http://localhost:5173
```

See `frontend/README.md` for the rest, including what is not wired up yet.

### Tests and checks

From `backend/`:

```bash
./.venv/bin/python -m pytest                       # 430 passed, 5 skipped
./.venv/bin/python -m pytest --live                # 433 passed, 2 skipped (no API key here)
./.venv/bin/python -m ruff check src tests
./.venv/bin/python -m ruff format --check src tests
./.venv/bin/python -m mypy                         # strict, over all of src/upgradepilot
```

The default suite is hermetic: no network, no LLM, no API key. The five
skipped tests are marked `@pytest.mark.live` and only run under `--live` —
three clone a small public repository over `https` (these pass here), and two
need a real `OPENAI_API_KEY` (these still skip here, with the reason printed
under `-rs`). They exist because a suite of fakes can pass while the real
path is broken.

Note that the shared skip reason reads "needs `--live` and a real
`OPENAI_API_KEY`", which is only half right for the three clone tests: they
need the network but no key.

From `frontend/`:

```bash
npx tsc -b                                # typecheck
npm run build                             # typecheck plus production build
```

## Documentation

- `PLANNING.md` — what we are building, in what order
- `docs/adr/ADR-001-system-architecture.md` — why the architecture is what it is
- `docs/superpowers/specs/` — detailed designs
- `CLAUDE.md` — working rules

## Stack

Backend: Python, FastAPI, LangGraph, LangChain, ChromaDB, OpenAI.
Frontend: React, Vite, TypeScript, Tailwind, Lucide.
