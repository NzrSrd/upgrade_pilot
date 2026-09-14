# ADR-002: Deployment — Cloud Run backend, Vercel frontend

- **Status:** Proposed
- **Date:** 2026-09-09
- **Deciders:** Project owner
- **Related:** `docs/adr/ADR-001-system-architecture.md`, `PLANNING.md` Phase 13

## Context

Everything in ADR-001 was decided for a system running on one developer's
laptop, and it shows: three of the four stores are local directories, the run
registry is a dictionary in one process, and the frontend reaches the backend
through a Vite dev-server proxy that exists only under `npm run dev`. None of
that is a defect — Sub-project 1 is graded on agent behaviour, not on
operations — but it means a hosted deployment is not a packaging exercise. It
is a set of architectural decisions that ADR-001 did not have to make.

The target is a **private/internal** deployment. Not a public demo: the API has
no authentication of any kind today, and `POST /api/agent/start` accepts a
repository URL that the server then clones and analyses at real token cost. A
URL on the open internet is an open wallet.

**Sequencing, recorded because it changes how this document should be read.**
Deployment was prioritised ahead of Phases 11 and 12 on 2026-09-09, to unblock
GitHub sign-in and users pasting repository links. So the decisions here are
being made *before* the end-to-end audit that would ordinarily precede them.
Two of them — D4 and D6 — exist only because of what comes after deployment
rather than what comes before it, and D4 replaced a smaller decision for that
reason. The first deploy is therefore not a claim that the product's journey
holds together; it is the environment in which that claim gets tested, since
Phase 10's pending exit and Phase 11's headline item both need a browser
pointed at a running deployment.

The platform is **Google Cloud Run** for the backend and **Vercel** for the
React frontend. Cloud Run was chosen by the project owner; §D1 records what it
costs us and what was rejected alongside it, because two of Cloud Run's
properties are actively hostile to the architecture ADR-001 describes and the
decisions below are mostly consequences of that friction.

Three platform facts drive almost everything here. All three were read from the
current vendor documentation rather than recalled, because a wrong assumption
about any one of them would invalidate the design:

1. Cloud Run's in-container filesystem is **in-memory**: *"It is an in-memory
   file system, so writing to it uses the instance's memory."* Writes count
   against the instance memory limit and do not survive the instance.
2. Cloud Run's shutdown grace period is **10 seconds and not configurable**:
   *"a 10 second period before the actual shutdown occurs, at which point Cloud
   Run sends a `SIGKILL` signal."*
3. Under the default request-based billing, **CPU is allocated only while a
   request is in flight**. Instance-based billing is what keeps the CPU
   allocated for the life of the instance.

## Decisions

### D1. Cloud Run, pinned to exactly one instance

`--max-instances=1`, `--min-instances=0`, one uvicorn worker, no `--reload`,
listening on `0.0.0.0:$PORT`, started with `--factory` because `api/app.py`
exports `create_app` rather than a module-level `app`.

The instance cap is not tuning, it is a correctness requirement, and it is worth
being precise about which component imposes it. It is **not** the checkpointer,
which D2 makes durable and shared. It is the run registry
(`api/registry.py`), which is a dictionary in one process. Under two instances
there are two registries and half the status lookups are blind: a run started by
instance A is invisible to instance B, which reports it as `ORPHANED` and offers
to restart work that is currently in progress. ADR-001 recorded this as an
accepted cost of Sub-project 1 and parked the fix in Sub-project 3. This ADR
does not lift it; it pins the deployment so the constraint holds.

Stated plainly, because the combination is easy to misread as scalable: this is
a **single-instance deployment with a shared database**, not a horizontally
scalable service.

### D2. The LangGraph checkpointer moves to PostgreSQL, ahead of Sub-project 3

`AsyncPostgresSaver` against a managed Postgres, selected by a new
`UP_CHECKPOINT_URL` setting. Its absence keeps the existing `AsyncSqliteSaver`
path, so local development and the hermetic test suite are unchanged.

This is the one decision here that contradicts ADR-001, whose D3 table lists
PostgreSQL as *"absent — arrives in Sub-project 3"*. The forcing fact is
platform fact 1. The checkpointer is what holds a paused human-in-the-loop run,
and on Cloud Run a SQLite file holds it **in RAM**. Every instance recycle —
a redeploy, a scale-to-zero, a memory eviction — destroys every paused run. The
interrupt/resume cycle is the product's headline capability and the thing
Phase 7 was built around; a deployment that loses it on redeploy is a
deployment that does not run this product.

Platform fact 2 makes the same point about runs that are merely *in flight*.
`RunRegistry.drain()` awaits every running task with no timeout, against a
non-configurable 10-second kill. In-flight runs will therefore always be
truncated by a revision replacement. That does not change under Postgres —
what changes is the aftermath. With state in RAM the run was gone. With state
in Postgres the checkpoint survives and the run is resumable through the
`ORPHANED` branch that `resume_run` already has.

**Amended after implementation: the provider is Neon, not Cloud SQL.** This
document was drafted around Cloud SQL and priced at roughly $10-12/month for the
smallest always-on instance. The project owner declined that cost, and Neon's
free tier carries the workload: 0.5 GB of storage against a checkpoint table
holding a handful of paused runs, and no card. Recorded here rather than left to
diverge, because the ADR naming a service the deployment does not use is worse
than no ADR.

The switch cost **no code**, which is the part worth keeping. `Settings`
validates `UP_CHECKPOINT_URL` for scheme and nothing else -- it refuses anything
that is not a `postgres(ql)://` DSN and inspects no host -- so changing
providers was changing one environment variable. That is the seam ADR-001
claimed and this is the second time it has held.

Two things Neon requires that Cloud SQL would not have:

- **The direct endpoint, not the `-pooler` one.** `AsyncPostgresSaver` opens its
  connections with `prepare_threshold=0`, so every statement it issues is a
  named server-side prepared statement. Transaction-pooled connections do not
  carry those across checkouts. The pooled host is the one Neon's dialog offers
  first, and the failure would appear as intermittent `prepared statement
  already exists` errors under load rather than as a clear refusal at startup.
- **Connection checking on checkout**, which is a correctness requirement and
  is described in its own subsection below.

**A failure this provider introduces, found by looking for it.** Neon suspends
its compute after five minutes with no queries. Cloud Run keeps an idle instance
alive after its last request. So the ordinary overnight state of this deployment
is a live process holding a connection to a database that has hung up -- and
`AsyncPostgresSaver.from_conn_string`, the obvious spelling and the one
originally written here, holds exactly **one** connection for the whole
application lifespan. The next resume raised `OperationalError: server closed
the connection unexpectedly`: a 500 on the resume of a run the user had been
told was safely paused, which is the single promise this decision exists to
keep. Durability of the *state* had been established; durability of the
*connection to it* had not, and the two are not the same guarantee.

`probes/probe_postgres_checkpointer.py` could not have caught it and is not
deficient for that. It proved state survives a **process** restart, against a
local server that never suspends. A connection dying underneath a process that
keeps running is the opposite arrangement.

The fix is a `psycopg_pool.AsyncConnectionPool` with
`check=AsyncConnectionPool.check_connection`, and the pool is **not** the fix on
its own -- measured, with `check=` removed the reproduction still fails and logs
`discarding closed connection`, because the pool notices a dead connection when
it comes *back*, having already handed it out. The pool is sized at 1-2
connections rather than by concurrency: `AsyncPostgresSaver._cursor` holds a lock
around every operation, so the saver serialises itself and a second connection
can never be busy. It is there to be a replacement, not a second worker.

Reproduced with `pg_terminate_backend` from a second connection, which is what
an idle timeout does to a session, so the regression test is deterministic,
local, and needs neither a Neon account nor a five-minute wait
(`tests/graph/test_checkpointer_survives_an_idle_disconnect.py`, plus a hermetic
assertion in `test_checkpointer_backend.py` that pins `check=` in CI where no
database exists).

**What this decision does not claim.** It moves the *checkpointer* only.
Accounts, users and saved analysis history stay in Sub-project 3, and ADR-001's
D3 row is amended to that narrower scope rather than deleted. Nor does it lift
the single-worker constraint — see D1.

### D3. The corpus is baked into the container image at build time

`python -m upgradepilot.services.knowledge.ingest` runs during the image build,
behind a BuildKit secret for the provider key, and the image ships a populated
`.chroma`.

Two reasons, one of them a near-miss worth recording. The first is ordinary:
ingest is a full rebuild by design, the corpus is 20 static Markdown files, and
baking it makes deploys deterministic and costs no embedding calls on a cold
start.

The second is the one that matters. `/api/health` reports `chroma_dir` by
stat-ing a directory; it deliberately never opens the store and never counts
documents. So a deployment that forgets to ingest answers **`status: "ok"`
over an empty collection**, and the product then does the one thing its entire
premise forbids — it retrieves no evidence while reporting itself healthy.
Baking the corpus into the image removes the failure mode instead of adding a
warning about it. The `.chroma` directory is read-mostly at runtime; the WAL
writes Chroma makes on open land on the in-memory overlay, which is fine
because nothing needs them to persist.

`UP_CORPUS_DIR` must be set explicitly in the image. `CORPUS_ROOT` resolves
relative to the installed package file, and `corpus/` sits outside `src/` with
no `package-data` entry, so a non-editable install does not ship it.

### D4. Clerk as the identity and access gate

Clerk, with GitHub as the social connection and sign-ups restricted.
`@clerk/react` in the frontend; `clerk-backend-api`'s
`authenticate_request_async` verifying the session token in FastAPI. A plain
`vercel.json` rewrite sends `/api/*` to Cloud Run.

**Two corrections from implementation.** The package is `@clerk/react`;
`@clerk/clerk-react` as first written here does not exist. And the restriction
is an **allowlist over public sign-up**, not invitation-only -- see the
subsection at the end of this decision, because the difference is a security
property rather than a configuration detail.

**This replaces an earlier decision in this document's own draft, and the reason
is worth keeping.** The first version of D4 was a shared secret injected by a
Vercel Routing Middleware — adequate, and about twenty lines. It was replaced on
learning what comes next: GitHub sign-in, so that users can paste repository
links. A shared secret would have been built, deployed, and then deleted weeks
later, and the deployment would have been re-architected around real identity
anyway. Deciding it once is cheaper than deciding it twice, even when the first
decision is small.

Clerk earns the place over a hand-rolled gate on four counts, three of which are
not access control at all:

1. **It is the private gate.** Restricted sign-up is what makes the
   deployment private. Clerk with open registration would be a login page, not
   an access control, and this distinction is the one most easily lost.
2. **It is the GitHub sign-in the prioritisation was for**, rather than a
   prerequisite for it.
3. **It carries Sub-project 2's hardest input.**
   `getUserOauthAccessToken(userId, 'github')` returns the signed-in user's
   GitHub token, which is exactly what an authenticated clone of a private
   repository needs. ADR-001 D5 promised Sub-project 2 would add authenticated
   clones "without touching the analyzer"; this is where the credential to do it
   comes from.
4. **It gives runs an owner** — see D6, which is not optional once real users
   exist.

Two consequences of choosing Clerk, both simplifications:

**The Vercel layer gets smaller, not bigger.** The earlier design needed Routing
Middleware for one reason only: a static rewrite can *match* on headers but
cannot *inject* them, and a shared secret must be added server-side where the
browser cannot read it. Clerk's session token comes *from* the browser, so
nothing needs injecting and a plain `vercel.json` rewrite suffices. The rewrite
still keeps the browser same-origin, so CORS is never exercised and no
`VITE_API_BASE_URL` has to be invented. `UP_CORS_ORIGINS` is set to the Vercel
production domain regardless — it costs nothing, and ADR-001 is explicit that a
wildcard is not a decision anyone would make on purpose.

**Amended after implementation: the sign-up posture is an allowlist, and it
fails open.** This document said invitation-only. What is deployed is
`sign_up_mode: public` with `allowlist_enabled: true` and one address on the
list. It is equally closed today and the distinction still matters, for two
reasons.

The first is that a reader of the Clerk dashboard sees `public` and will
reasonably conclude the deployment is open. It is not -- the allowlist is what
closes it -- but nothing at the point of reading says so.

The second is the failure direction. `restricted` fails **closed**: with no
invitation, nobody gets in. An allowlist fails **open**: clear the list and
sign-up is public registration on a URL that reaches the token budget. The
safer mode is available and was not chosen for a reason worth recording -- the
first attempt set `sign_up_mode: restricted` on an instance with zero users and
no pending invitation, which locked the project owner out of their own
application, and Clerk then refuses `allowlist: true` alongside `restricted`
at all ("isn't allowed to be `true` when sign-up mode is set to restricted").
So the two mechanisms are alternatives, not layers, and the allowlist was the
one that could be applied without first arranging an invitation. Moving to
`restricted` plus a genuine emailed invitation remains the stricter option and
is open.

**The frontend does need a code change**, which the earlier design avoided.
`client.ts` must attach the session token, and the app must read
`VITE_CLERK_PUBLISHABLE_KEY`. That second one ends a property this document
relied on a paragraph ago: the frontend currently reads **zero** environment
variables. The change is contained — `client.ts` is the only module that calls
`fetch`, by Phase 10's design, so this is one edit rather than a sweep — but the
"no code change" claim does not survive Clerk and is withdrawn rather than
quietly reworded.

### D5. Instance-based billing, scaling to zero

`--no-cpu-throttling` with `--min-instances=0`.

The billing mode is not a cost decision, it is a correctness one. Platform fact
3 means the default throttles the CPU as soon as a response is sent — and
`POST /api/agent/start` returns 202 immediately and runs the graph on a
background `asyncio.create_task`. Under request-based billing that task stalls
the moment the 202 is delivered. ADR-001's D8 chose background execution with
status polling; instance-based billing is what makes D8 work on this platform.

Scaling to zero is then available *because* of D2, and would not have been
without it. While paused runs lived in RAM, `--min-instances=1` was the only
thing keeping them alive, and it made the service bill continuously. With the
checkpoint in Postgres an idle instance holds nothing worth keeping, so the
instance can go away and the cost with it. The price is a cold start, dominated
by the `chromadb` import.

**The image size, measured rather than assumed.** This decision was taken with
the image unmeasured, which left the cold-start argument resting on nothing.
The built image is **231,700,255 bytes (~221 MiB)**, corpus included. That is
small enough that pull time is not the dominant term and the `chromadb` import
remains the thing to attack if cold starts become the complaint. Recorded
because "accepted for internal use" is only a defensible position with a number
attached. *Source: `gcloud artifacts docker images list`, tag `f581b31`.*

Neon compounds this, mildly and worth stating: the database also suspends when
idle, so the first request after a quiet night pays a Cloud Run cold start
**and** a Neon resume. Neither is a correctness problem -- see D2 for the one
that was.

### D6. Runs are owned, and ownership is enforced

A `run_owners` table in the same Postgres database D2 provisions, mapping
`thread_id` to a Clerk user id, checked on `status` and `resume`.

This is a direct consequence of D4 and is the one place where adding
authentication could have made the system *less* safe. A shared secret has no
concept of a second user: one trusted proxy, one tenant, every run implicitly
the caller's. Clerk creates real users — and
`GET /api/agent/status/{thread_id}` and `POST /api/agent/resume` check nothing
about who is asking. Shipping identity without ownership would replace "nobody
can get in" with "anyone who is in can read and resume anyone else's run",
including answering someone else's pending decision, which the append-only
`human_decisions` channel would faithfully record.

So this lands with the gate rather than in Sub-project 3, where per-user history
otherwise lives. Two details that are decisions rather than implementation:

- A thread owned by someone else must be **indistinguishable from one that does
  not exist**. A distinct "forbidden" response confirms the thread id is real,
  which is the single fact an enumerating caller wants.
- The table goes in the database D2 already provisions. It needs no second
  store, and it is the natural seed for Sub-project 3's saved analyses — which
  makes this early arrival a down payment rather than a detour.

**Sharing D2's database means sharing D2's connection bug.** `open_ownership`
already used a pool, for concurrency, and a pool alone is not enough — so
`require_owner` raised `psycopg.errors.AdminShutdown` from `owner_of` once the
provider hung up. Worth being precise about the direction: the authorisation
check *failed*, it did not pass. A 500 rather than an accidental allow, so
unavailable and not unsafe — but still the owner locked out of their own paused
run, on the deployment's morning after. Fixed the same way as D2, with
`check=AsyncConnectionPool.check_connection`, and covered by
`test_ownership_still_answers_after_the_database_hangs_up`, which re-asserts
ownership afterwards rather than only checking for a 200: a check that started
failing open would also have satisfied "the request succeeded".

**A coupling this decision creates, added after implementing it.** Ownership
lives in Postgres, so a Clerk key without `UP_CHECKPOINT_URL` describes a
deployment where the gate exists and ownership cannot be recorded — the
downgrade this decision exists to prevent, in the one configuration that looks
correct from the outside. `Settings` therefore **refuses to start** on that
pair, naming both variables. The pairing is one-directional on purpose:
Postgres without Clerk is fine and is what a single-tenant deployment looks
like; it is only the gate that implies ownership.

**Two things the implementation found that this decision had not
anticipated.**

The first was a working oracle. "Indistinguishable" was first written as two
raise sites with two string literals — `"No run with that id."` beside
`"No run with that id exists."` — so the response *text* told a caller whether
a thread id was real, while both returned 404 and every test passed. The
message is now a single constant, `THREAD_NOT_FOUND_MESSAGE`, and the test
compares whole response bodies rather than status codes. Recorded because the
defect was invisible by construction: neither literal was wrong, only their
being two.

The second is that **an unclaimed run must be refused, not shared.** A thread
with no owner row is reachable in practice — one predating the table, or one
whose claim failed to write — and the permissive reading ("no owner, so anyone
may read it") would expose precisely those. `require_owner` treats absence as
refusal, and a test deletes a claim to prove it rather than trusting a comment.

**Verified against the real store rather than a fake.** The ownership tests
carry a `postgres` marker and run against a live database — locally from
`UP_TEST_POSTGRES_URL`, in CI from a service container, with a CI step that
fails if they *skip*, because a silent skip would leave this guarantee
uncovered while CI stayed green. An in-memory dict would have satisfied every
assertion while proving nothing about the SQL, which is the argument rule 24
already makes for keeping one live LLM test. Removing the single
`require_owner` call from the status route turns four of the seven red.

## Alternatives considered

**A1. A small Compute Engine VM instead of Cloud Run** — an `e2-small` with a
persistent disk, running the same container. This fits the architecture better
than the chosen platform does: one process, a real disk, no 10-second kill, and
SQLite stays adequate so D2 becomes unnecessary. Roughly $13/month all in. Not
chosen — Cloud Run is the project owner's platform decision — and it is the
option that would have required the fewest decisions in this document.

**This alternative got weaker after D2's provider changed, and saying so is the
point of keeping it.** It was recorded as the escape hatch if the database line
item stopped earning its place. That line item is now $0, so the comparison is
$13/month for the VM against near-zero for Cloud Run scaled to zero plus Neon's
free tier — and the VM is no longer the cheaper option it was written up as. The
architectural fit argument survives untouched; only the money moved.

**A2. Multiple Cloud Run instances** — rejected outright by D1. The in-memory
run registry makes it a correctness bug, not a scaling trade-off.

**A3. SQLite on a Cloud Storage (GCS FUSE) volume mount** — would have kept the
checkpointer as-is and made it durable. Rejected on the vendor's own words:
Cloud Storage FUSE *"does not provide concurrency control for multiple writes
(file locking) to the same file. When multiple writes try to replace a file, the
last write wins and all previous writes are lost."* It is also *"not a fully
POSIX-compliant file system"*. SQLite's durability depends on exactly the
locking that sentence says is absent.

**A4. SQLite on a Filestore (NFS) volume mount** — Cloud Run does support NFS
mounts, and with a single writer this would work. Rejected on cost: the smallest
Filestore instance runs into the hundreds of dollars a month, which is wildly
disproportionate to a private internal tool and worse value than A1.

**A5. Ingest at container startup rather than at build** — simpler Dockerfile,
no build-time secret. Rejected: it puts embedding calls and provider
reachability on the cold-start path, and makes every cold start a chance to come
up with an empty collection. D3 exists to delete that failure mode.

**A6. A shared secret injected by Vercel Routing Middleware** — this document's
own first answer, and the smallest possible gate: about twenty lines, no vendor,
no new store, and no code change in the React app at all. Rejected by D4 once
the roadmap was clear. It gives no user identity, so D6's ownership check would
have had nothing to key on; it does nothing for GitHub sign-in; and it would
have been deleted within weeks. Recorded rather than dropped because it remains
the right answer for a deployment that genuinely has one tenant forever, and
because the reason it lost is a roadmap fact rather than a technical one.

**A7. Hand-rolled GitHub OAuth with our own sessions** — no vendor dependency,
no per-seat pricing, and full control of the token exchange. Rejected: it is a
session store, a callback handler, token refresh, and a login UI, all of it
security-sensitive code with no relationship to what this product is for, in a
sub-project whose remaining phases are an end-to-end test and a demo. Clerk's
free tier covers an internal deployment comfortably, and this is the clearest
case in the project so far of buying rather than building.

**A8. Google IAP behind an external HTTPS load balancer** — real SSO with no
application code at all. Rejected rather than deferred now: IAP gates access but
would leave the application with no user identity of its own, so D6 would still
need Clerk or an equivalent, and Sub-project 2 would still need a GitHub token
from somewhere. It also costs around $18/month for the load balancer alone. It
solves the smaller half of the problem and leaves the larger half untouched.

**A9. Cloud Run IAM with a service-account ID token minted in Vercel
middleware** — was under consideration as a hardening step on top of the shared
secret. Now moot: D4 removes the middleware entirely, and there is no
server-side hop left to mint a token in.

**A10. A static rewrite with no auth at all** — the zero-code option, and what
this would be if the deployment were public. Rejected by the Context: it leaves
the Cloud Run URL callable by anyone who learns it, with the token budget behind
it.

## What was actually deployed

Recorded because an ADR describing an intended deployment and a deployment that
differs from it is the failure this document exists to prevent. Read back off
the running revision, not off the command that created it.

| | |
|---|---|
| Service | `upgradepilot-backend`, revision `upgradepilot-backend-00002-m8t`, `europe-west1` |
| Image | `backend:049a887`, ~221 MiB, corpus baked |
| Scaling | `maxScale=1`, `minScale=0`, `cpu-throttling=false` — D1 and D5 |
| Resources | 1 CPU, 2 GiB, in-memory volume capped at 512 MiB on `/tmp/workspaces` |
| Secrets | `llm-api-key`, `clerk-secret-key`, `checkpoint-url`, all by reference |
| Database | Neon, PostgreSQL **18.6**, direct (non-pooled) endpoint |

`/api/health` reports `status: ok`, `checkpoint_backend: postgres`,
`auth_required: true`, all three checks true. An unauthenticated
`GET /api/agent/status/{id}` returns 401. The deployed application created
`checkpoints`, `checkpoint_blobs`, `checkpoint_writes`, `checkpoint_migrations`
and `run_owners` in Neon, so both `setup()` calls ran against the real database.

**Four things this document did not anticipate**, each of which cost time and
none of which changes a decision:

- `SHORT_SHA` is empty on a manual `gcloud builds submit`. It is populated only
  for trigger-based builds, and the failure is an unparseable image reference
  rather than anything naming the cause.
- `OPENROUTER_BASE_URL` is baked into the *build* stage for the corpus ingest but
  not into the runtime image, so it must be set at deploy. Unset, the client
  falls back to OpenAI direct while holding an OpenRouter key.
- The runtime service account needs `secretmanager.secretAccessor` granted
  explicitly. Granted per secret rather than at the project level, so the
  service can read these three and nothing else.
- Neon runs PostgreSQL 18.6 while CI's service container and local development
  are both on 17. Nothing has depended on the difference, but the suite that
  guards this deployment does not run on the version the deployment uses.

**The Vercel project was never building the frontend, and nothing said so.**
Its Root Directory was `.` and its framework preset was "Other", so Vercel
served the *repository root* as static files: no build ran, `/` returned 404,
and every deployment reported success in three seconds. Two consequences worth
separating, because only the first is obvious:

- The site was broken in production and had been since the project was created.
- **`frontend/vercel.json` had never been read**, because Vercel looks for it in
  the configured root directory. So the `/api/*` rewrite this document treats as
  the mechanism connecting the two halves was not in effect at any point, and
  the placeholder hostname D4 relies on being replaced was never consulted
  either. The file was correct and inert.

Fixed by setting Root Directory to `frontend` and the preset to Vite. The
evidence that it was actually wrong, rather than merely suspicious, is the build
duration: 3 seconds before, 17 seconds after, with `vite build` and
`dist/index.html` appearing in the log for the first time.

The general lesson is the one this project keeps relearning: a green check mark
reports that a step completed, not that it did anything. The same shape as
`/api/health` answering `ok` over an empty corpus, which is why D3 has the build
assert a document count.

**`UP_ALLOWED_LOCAL_ROOTS` is absent rather than set empty**, which is the
stronger of the two. It defaults to empty; `.gcloudignore` excludes `.env` and
`.env.*`; no `COPY` in the Dockerfile references either. All three checked.

## Consequences

**Accepted costs**

- In-flight runs are still killed mid-execution by any revision replacement.
  Platform fact 2 is not negotiable and `drain()` cannot win against it. What D2
  buys is that the run is resumable afterwards rather than lost. `drain()` is
  given a bounded timeout so the shutdown is deliberate rather than truncated.
- Cold-start latency on the first request after an idle period, dominated by the
  `chromadb` import. Accepted for internal use; it is the direct price of D5.
- Postgres is a new external dependency, in a sub-project that previously had
  no external service other than the model provider. On Neon's free tier it is
  not a new *cost*, but the dependency is real and it brought a failure mode
  with it — see D2's idle-disconnect subsection, which is the one thing in this
  document that was a live defect rather than a trade-off.
- Neon's free tier caps storage at 0.5 GB and compute at 100 CU-hours/month.
  Both are far above a checkpoint table for a handful of paused runs, and
  neither is monitored — so the first sign of outgrowing the tier would be a
  failure, not a warning. Acceptable at this scale; not acceptable if this
  deployment ever stops being internal.
- `psycopg` is a new dependency on an interpreter floor of 3.14, which ADR-001
  says must not be relaxed. Phase 13.0 probes wheel availability before the
  design depends on it.
- The health endpoint's `checkpoint_dir` check becomes meaningless under a
  Postgres DSN. Renaming it to something honest regenerates `openapi.json` and
  `schema.d.ts` and touches the health UI. `_derive_status` iterates the model's
  own fields, so the status derivation absorbs the change; the cost is the
  regeneration, not the logic.
- Deployment now has a build-time secret (the provider key, for D3), which is a
  new class of secret for this project.
- **Clerk is a third-party dependency in the authentication path** — the first
  external service this project depends on other than the model provider, and
  the first whose outage means nobody can sign in. Accepted deliberately in A7
  over hand-rolling sessions.
- The frontend reads its first environment variable
  (`VITE_CLERK_PUBLISHABLE_KEY`), ending a property D4's earlier reasoning
  relied on.
- `httpx` moves from a dev-only dependency to a runtime one, arriving via
  `clerk-backend-api`. `cryptography` is new; it ships `abi3` wheels, which is
  why it is expected to survive the 3.14 floor, but Phase 13.0 probes it rather
  than assuming.
- Two new dependencies land in a sub-project whose remaining phases were an
  end-to-end test and a demo. Rule 12 wants the reason stated for each, and D4
  is that statement.

**Benefits**

- A paused human-in-the-loop run survives a redeploy — the capability Phase 7
  built and the one this platform would otherwise have quietly removed.
- ADR-001 claimed that *"swapping model provider, checkpointer backend, or
  repository source each touch one module."* D2 is the first real test of that
  claim, and `graph/checkpointer.py` is the module it named.
- The frontend ships unmodified, and CORS never enters the picture.
- A deployment cannot come up with an empty knowledge base (D3).
- Sub-project 2's hardest input arrives early and for free: Clerk holds the
  user's GitHub token, so an authenticated clone needs a credential lookup
  rather than an OAuth implementation.
- Runs have owners (D6), which is the seed Sub-project 3's saved analyses grow
  from rather than a throwaway check.

**Obligations created**

- `--max-instances=1` must be asserted by whatever applies the Cloud Run
  configuration, not left to a default. It is the deployment's load-bearing
  correctness setting and the least visible one.
- The provider key needs a hard spend cap set at the provider. D4's gate is the
  only thing between a reachable URL and the token budget, and a cap is the only
  thing that bounds the damage if it fails.
- `UP_ALLOWED_LOCAL_ROOTS` must be empty in production. The committed
  `.env.example` ships a developer-machine path, local-path analysis is
  meaningless on a server, and ADR-001 records the setting as an arbitrary-read
  surface.
- When Sub-project 3 moves the run registry to Postgres, D1 should be revisited
  and the instance cap lifted deliberately rather than forgotten.
- **Clerk sign-ups must stay restricted to invitation.** It is a dashboard
  setting, outside version control and outside CI, and it is the single control
  that makes this deployment private. Everything else in D4 assumes it. If it is
  ever relaxed, D4 and D6 are both void and the token budget is open to whoever
  registers.
- D6's ownership check must be extended to any endpoint added later that takes a
  `thread_id`. The check lives at the route, so a new route is a new hole by
  default.

**Not revisited by this ADR**

ADR-001's A7 ("Zip upload — best for a hosted multi-machine deployment") stays
deferred. This is a single-instance deployment, so the argument that motivated
it does not apply, and the churn signal that rejecting it preserved is still
worth keeping.
