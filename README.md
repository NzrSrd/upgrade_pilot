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
./.venv/bin/python -m uvicorn upgradepilot.api.app:create_app --factory --port 8000
```

`--factory` is not optional: `app.py` exports `create_app`, not a module-level
`app`, so that the settings and the runtime factory stay injectable. Without
the flag uvicorn reports `Attribute "app" not found`.

Port 8000 matters: the frontend dev server proxies `/api` there. Check it:

```bash
curl http://127.0.0.1:8000/api/health
# {"status":"degraded","version":"0.1.0",
#  "checks":{"chroma_dir":true,"checkpoint_dir":true,"llm_configured":false}}
```

`degraded` with `llm_configured: false` is the correct answer when no model
API key is set — `status` is derived from the checks, so the endpoint cannot
report `ok` over a failing one. Copy `backend/.env.example` to `backend/.env`
and fill in a key to get `ok`.

The key is read from `OPENROUTER_API_KEY`, then `OPENAI_API_KEY`, then
`UP_LLM_API_KEY`, in that order. This project is developed against OpenRouter,
which serves the same OpenAI API behind `OPENROUTER_BASE_URL`; leaving the
base URL unset selects OpenAI direct. Model identifiers differ between the two
(`openai/gpt-4.1-mini` against `gpt-4.1-mini`), so they have to match whichever
block of `.env.example` you uncomment.

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
./.venv/bin/python -m pytest                       # hermetic; live tests skip
./.venv/bin/python -m pytest --live                # also runs the opt-in live tests
./.venv/bin/python -m ruff check src tests
./.venv/bin/python -m ruff format --check src tests
./.venv/bin/python -m mypy                         # strict, over all of src/upgradepilot
```

The default suite is hermetic: no network, no LLM, no API key. The five
skipped tests are marked `@pytest.mark.live` and only run under `--live` —
three clone a small public repository over `https`, and two make one real
model call each to check that token usage is actually reported. They exist
because a suite of fakes can pass while the real path is broken: with a fake
model supplying synthetic usage metadata, every token-tracking test passes
while the extractor reads zero.

With a key in `backend/.env`, `--live` runs all five and nothing skips. The
two model calls cost well under a cent.

Note that the shared skip reason reads "needs `--live` and a real LLM API
key", which is only half right for the three clone tests: they need the
network but no key.

From `frontend/`:

```bash
npx tsc -b                                # typecheck
npm run build                             # typecheck plus production build
```

## Deploying

Backend on Cloud Run, frontend on Vercel. The reasoning is in
`docs/adr/ADR-002-deployment.md`; this is the sequence.

**Two settings are correctness requirements, not tuning.** `--max-instances=1`
because the run registry is in-process, so a second instance makes half of all
status polls report `ORPHANED` and offer to restart work that is currently
running. `--no-cpu-throttling` because `POST /api/agent/start` returns 202 and
the graph runs on a background task, which the default request-based billing
throttles the instant the response is sent.

### Once, per project

```bash
gcloud artifacts repositories create upgradepilot \
  --repository-format=docker --location=europe-west1
```

Three secrets. Use `backend/scripts/set_checkpoint_url.py` for the database
URL rather than assembling it by hand -- it takes the password without echoing
it, strips Neon's `-pooler` suffix, and refuses to write a DSN it could not
connect with.

```bash
printf '%s' "$OPENROUTER_API_KEY" | gcloud secrets create llm-api-key      --data-file=-
printf '%s' "$CLERK_SECRET_KEY"   | gcloud secrets create clerk-secret-key --data-file=-
printf '%s' "$UP_CHECKPOINT_URL"  | gcloud secrets create checkpoint-url   --data-file=-
```

`printf`, not `echo`: a trailing newline in a secret is invisible in every
dashboard and fails as an authentication error that looks like a wrong key.

Grant the runtime service account read access **per secret** rather than at the
project level:

```bash
SA="$(gcloud projects describe "$(gcloud config get-value project)" \
      --format='value(projectNumber)')-compute@developer.gserviceaccount.com"
for s in llm-api-key clerk-secret-key checkpoint-url; do
  gcloud secrets add-iam-policy-binding "$s" \
    --member="serviceAccount:$SA" --role=roles/secretmanager.secretAccessor
done
```

### Every deploy

```bash
cd backend
gcloud builds submit --config=cloudbuild.yaml \
  --substitutions=SHORT_SHA="$(git rev-parse --short HEAD)" .
```

`SHORT_SHA` must be passed explicitly. It is populated automatically only for
trigger-based builds, and a manual submit without it fails on an unparseable
image reference rather than on anything that names the cause.

The build ingests the corpus and then asserts four things, because each has a
silent failure mode: no provider key in the image history, the baked Chroma
collection is non-empty, the image actually serves `/api/health`, and it
reports `degraded` rather than `ok` when it has no key.

```bash
gcloud run deploy upgradepilot-backend \
  --image="europe-west1-docker.pkg.dev/$PROJECT/upgradepilot/backend:$SHA" \
  --region=europe-west1 --allow-unauthenticated \
  --max-instances=1 --min-instances=0 --no-cpu-throttling \
  --cpu=1 --memory=2Gi \
  --add-volume=name=workspaces,type=in-memory,size-limit=512Mi \
  --add-volume-mount=volume=workspaces,mount-path=/tmp/workspaces \
  --set-secrets=OPENROUTER_API_KEY=llm-api-key:latest,CLERK_SECRET_KEY=clerk-secret-key:latest,UP_CHECKPOINT_URL=checkpoint-url:latest \
  --set-env-vars=^|^OPENROUTER_BASE_URL=https://openrouter.ai/api/v1|UP_CORS_ORIGINS=https://your-app.vercel.app|UP_AUTHORIZED_PARTIES=https://your-app.vercel.app,https://*-your-vercel-scope.vercel.app|UP_MAX_CONCURRENT_RUNS=2
```

`^|^` changes gcloud's delimiter from `,` to `|`, which
`UP_AUTHORIZED_PARTIES` needs: its value is itself a comma-separated list, and
with the default delimiter gcloud would read the second origin as a variable
name and fail.

`UP_AUTHORIZED_PARTIES` is the allowlist of origins whose Clerk tokens this API
accepts, and it is required once `CLERK_SECRET_KEY` is set -- the container
refuses to start without it rather than starting and rejecting every caller.
The second entry is a wildcard over the Vercel scope, because every preview
deployment mints its tokens on its own immutable URL; without it only the
project alias can sign in. `UP_CORS_ORIGINS` is a separate setting answering a
separate question and is never exercised in this topology at all.

`--allow-unauthenticated` is deliberate and does not mean the API is open.
Clerk is the gate (ADR-002 D4): the container refuses every request without a
valid session, and `Settings` refuses to boot at all with a Clerk key and no
`UP_CHECKPOINT_URL`. Cloud Run IAM was rejected because Vercel's rewrite is an
unauthenticated proxy and would need a service-account key stored in Vercel.

`OPENROUTER_BASE_URL` is **not** baked into the image and must be set here.
Unset, the client falls back to OpenAI direct while holding an OpenRouter key,
and the first symptom is the corpus ingest failing with "the embedding provider
could not be reached".

`UP_ALLOWED_LOCAL_ROOTS` is deliberately absent. It defaults to empty,
local-path analysis is meaningless on a server, and ADR-001 records the setting
as an arbitrary-read surface. Do not set it in production.

### Frontend

`frontend/vercel.json` rewrites `/api/*` to the Cloud Run URL, which keeps the
browser same-origin so CORS is never exercised. Set
`VITE_CLERK_PUBLISHABLE_KEY` in the Vercel project for both production and
preview -- it is a publishable key and ships in the browser bundle, so it is
not a secret. The Clerk **secret** key belongs only in Secret Manager and must
never appear under `frontend/`.

### Checking it worked

```bash
curl -s "$SERVICE_URL/api/health" | python3 -m json.tool
curl -s -o /dev/null -w '%{http_code}\n' "$SERVICE_URL/api/agent/status/nope"
```

`/api/health` must report `checkpoint_backend: "postgres"` and
`auth_required: true`; the second must be `401`. Note that `status: "ok"` does
**not** prove the corpus is populated -- the check stats a directory and never
opens the store, which is why the build asserts the collection count instead.

## Documentation

- `PLANNING.md` — what we are building, in what order
- `docs/adr/ADR-001-system-architecture.md` — why the architecture is what it is
- `docs/adr/ADR-002-deployment.md` — why the deployment is what it is
- `docs/superpowers/specs/` — detailed designs
- `CLAUDE.md` — working rules

## Stack

Backend: Python, FastAPI, LangGraph, LangChain, ChromaDB, OpenAI.
Frontend: React, Vite, TypeScript, Tailwind, Lucide.
