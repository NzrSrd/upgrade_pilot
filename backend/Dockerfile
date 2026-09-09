# UpgradePilot backend, for Cloud Run. See docs/adr/ADR-002-deployment.md.
#
# Two stages. The first resolves dependencies and builds the Chroma corpus,
# both of which need a provider key and network; the second copies the results
# into a clean image that carries neither the key nor the build tooling.

# Declared before the first FROM so a single default serves both stages, then
# re-declared inside each -- an ARG is scoped to its stage, and a runtime-stage
# `ARG EMBEDDING_MODEL` without this would resolve to the empty string.
#
# The two are not independent: the supported providers disagree on model
# identifiers (`openai/text-embedding-3-small` on OpenRouter against
# `text-embedding-3-small` on OpenAI direct), and leaving the base URL unset
# selects OpenAI. Override both together or neither.
ARG LLM_BASE_URL=https://openrouter.ai/api/v1
ARG EMBEDDING_MODEL=openai/text-embedding-3-small

# Debian, not Alpine, for two independent reasons. Alpine is musl, which
# invalidates every `cp314` manylinux wheel this project depends on --
# `probes/probe_wheels.py` confirms all 112 pinned distributions are manylinux
# and none is musllinux-only. And `services/repo/workspace.py` hardcodes
# `PATH=/usr/bin:/bin:/usr/local/bin` for every git subprocess with `env=`
# replacing the whole environment, so git has to be exactly where Debian puts
# it.
FROM python:3.14-slim AS build

# git is absent from `slim` and is not optional: repository analysis shells out
# to `git clone`, `git rev-parse` and `git log`.
RUN apt-get update \
 && apt-get install --no-install-recommends -y git \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# `--require-hashes` is the point of the lockfile: every one of the 112
# distributions is verified against a sha256 recorded on a machine that is not
# this one, and an unpinned transitive cannot slip in. Regenerate with
# `scripts/lock_linux_deps.py` after changing pyproject's dependencies.
COPY requirements.lock ./
RUN python -m pip install --no-cache-dir --require-hashes -r requirements.lock

# The project itself, installed without dependencies -- the lockfile above
# already placed them, and letting pip resolve again here would defeat the
# hash pinning.
# No README: `pyproject.toml` declares no `readme` field, and the project's
# README lives at the repository root, outside this build context.
COPY pyproject.toml ./
COPY src ./src
RUN python -m pip install --no-cache-dir --no-deps .

# Build the knowledge base into the image (ADR-002 D3). This is a mandatory
# step that the README never mentioned, and skipping it produces a server that
# answers `/api/health` with `status: "ok"` while retrieving zero evidence --
# the exact failure this product exists to prevent. Baking it here removes the
# failure mode instead of documenting it.
#
# The key is a mounted build secret, so it is never a layer and never in the
# image history. Ingest is a full rebuild by design, so this is deterministic
# for a given corpus.
COPY corpus ./corpus

# Re-declared from the global ARGs above. The first build attempt omitted the
# base URL entirely and failed as "The embedding provider could not be
# reached" -- which is what an unset base URL produces with an OpenRouter key,
# not a network problem.
ARG LLM_BASE_URL
ARG EMBEDDING_MODEL

ENV UP_CORPUS_DIR=/build/corpus \
    UP_CHROMA_DIR=/build/.chroma
RUN --mount=type=secret,id=llm_api_key \
    OPENROUTER_API_KEY="$(cat /run/secrets/llm_api_key)" \
    OPENROUTER_BASE_URL="${LLM_BASE_URL}" \
    UP_EMBEDDING_MODEL="${EMBEDDING_MODEL}" \
    python -m upgradepilot.services.knowledge.ingest

# --- runtime ---------------------------------------------------------------
FROM python:3.14-slim AS runtime

RUN apt-get update \
 && apt-get install --no-install-recommends -y git \
 && rm -rf /var/lib/apt/lists/*

# A non-root user, and one consequence worth knowing before it becomes an
# issue report: ADR-001 records that the hardened git environment drops the
# global `safe.directory` allowance, so a repository owned by a different uid
# fails closed with a clear error rather than being read. That is the right
# default for user-supplied input, and it means a mounted checkout owned by
# another uid will not work here.
RUN useradd --create-home --uid 10001 upgradepilot

COPY --from=build /usr/local/lib/python3.14/site-packages /usr/local/lib/python3.14/site-packages
COPY --from=build /usr/local/bin /usr/local/bin
COPY --from=build --chown=10001:10001 /build/corpus /app/corpus
COPY --from=build --chown=10001:10001 /build/.chroma /app/.chroma

WORKDIR /app
USER 10001

# Every store path is absolute. All three default to CWD-relative, which makes
# the process working directory load-bearing, and `Settings`' `.env` lookup is
# CWD-relative too -- so an absolute value here is the difference between a
# configuration that is stated and one that depends on how uvicorn was invoked.
#
# `UP_WORKSPACE_DIR` points at a writable path because cloned repositories land
# there. On Cloud Run it should be an in-memory volume with a size cap: the
# container filesystem is memory, so an unbounded clone is a memory leak.
# `UP_ALLOWED_LOCAL_ROOTS` is left unset, which denies all local paths --
# analysing a path on the server is meaningless and it is an arbitrary-read
# surface.
# `UP_EMBEDDING_MODEL` is baked to the value the corpus was **embedded with**,
# and this is a correctness constraint rather than a convenience default.
# Retrieval compares a query vector against stored vectors; produce the two
# with different models and the similarity numbers are meaningless while
# everything still returns 200 and cites documents. Since the corpus ships
# inside this image, the model that built it ships with it. A deploy that
# overrides this without rebuilding has silently broken retrieval, and
# nothing in the product would say so.
ARG EMBEDDING_MODEL
ENV UP_CORPUS_DIR=/app/corpus \
    UP_CHROMA_DIR=/app/.chroma \
    UP_WORKSPACE_DIR=/tmp/workspaces \
    UP_CHECKPOINT_DB=/tmp/checkpoints.db \
    UP_EMBEDDING_MODEL=${EMBEDDING_MODEL} \
    PYTHONUNBUFFERED=1

# `$PORT` is injected by Cloud Run and defaults to 8080; `0.0.0.0` because the
# contract is explicit that the ingress container must not listen on localhost.
# One worker, because the run registry is in-process -- see ADR-002 D1, where
# `--max-instances=1` is a correctness requirement rather than tuning.
# `--factory` is not optional: `api/app.py` exports `create_app`, not `app`.
CMD exec python -m uvicorn upgradepilot.api.app:create_app \
    --factory --host 0.0.0.0 --port "${PORT:-8080}" --workers 1
