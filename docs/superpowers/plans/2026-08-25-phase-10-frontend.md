# Phase 10 — Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A single-route React console in which a developer configures a migration run, watches it gather evidence, is asked exactly one meaningful question, answers it, and reads a validated plan whose every claim resolves to a real line of code or a real corpus document.

**Architecture:** One route. All server state flows through one `useRunPolling(threadId)` hook that fetches a *complete* `RunSnapshot` every second while the run is non-terminal — so no component accumulates, merges, or re-derives anything. Three rules that must not be re-implemented per component (which view a status selects, which timeline steps are skipped, how a cost may be worded) live in `src/derive/` as pure, unit-tested functions. Types are generated from the backend's own OpenAPI schema, never hand-mirrored.

**Tech Stack:** React 19, TypeScript, Vite 8, Tailwind 4 (`@tailwindcss/vite`), Lucide icons, Vitest 4 + React Testing Library + MSW, `openapi-typescript`.

**Spec:** `docs/superpowers/specs/2026-08-24-upgradepilot-agent-core-design.md` §10 (frontend) and §9 (API contract). Visual reference: `docs/ui/DESIGN.md`. Component structure: `docs/ui/COMPONENTS.md`. The decisions these two encode were settled in commit `c81891d`; `docs/ui/READINESS.md` records what each one would have cost.

---

## Global Constraints

Copied verbatim from the sources named. Every task's requirements implicitly include this section.

- **`CLAUDE.md` rule 1 — the rule everything else is subordinate to.** Every claim the product makes must trace to a real line of code or a real corpus document. **Uncited output is a bug, not a style issue.** In this phase that means: a number on screen must name the field it came from. If a panel has no field behind it, the panel is wrong.
- **`CLAUDE.md` rule 3.** Do not implement future phases early. Writing to GitHub, run history persistence, and authenticated/private repositories are sub-projects 2 and 3.
- **`CLAUDE.md` rule 9 / 10 / 11.** No requirement is complete without a test or a demonstrated run. Do not claim functionality works unless it has been tested — show the output. A phase is done when its exit criteria are demonstrably met, not when the code exists.
- **`CLAUDE.md` rule 12 / 13.** No new dependency without a stated reason; prefer the standard library. Pin versions, verify them, record resolved versions in ADR-001.
- **`CLAUDE.md` rule 16.** Layer dependencies flow one way. For the frontend the equivalent is: `components/ → hooks/ → api/`, and `derive/` imports only from `api/types`. A component must never call `fetch`; a `derive/` function must never import React.
- **`CLAUDE.md` rule 17.** Prefer typed models over dictionaries. Every server shape comes from the generated schema — no hand-written interface mirroring a Pydantic model.
- **`CLAUDE.md` rule 21.** Derived values are computed, not stored. In React: derive from the snapshot on render; never copy snapshot data into `useState`.
- **`CLAUDE.md` rule 26.** The agent trace shows observable events only — node boundaries, queries issued, sources retrieved and selected, decisions, validation outcomes. It never shows internal prompts or private reasoning.
- **Spec §10 — one route.** The view derives from `RunSnapshot.status`. There is no user-navigable workflow tab bar. `ReportTabs` is the only tab bar in the application.
- **Spec §10 — no form library.** Controlled inputs plus a small validate function mirroring backend rules. The backend stays authoritative and its 422 detail renders inline. Six fields do not earn a form library.
- **Spec §10 — semantic tokens only.** `risk-high`, `risk-medium`, `risk-low`, `pending-input` in the Tailwind theme. Never a raw color at a call site.
- **Spec §10 — `aria-live` on the status region**, so the transition into Human Review is announced rather than merely rendered.
- **Spec §10 — duplicate resume is blocked three ways:** disabled button, local `submitting` flag, and the server's 409 — the last being the only real guarantee.
- **ADR-001 A3 — polling, not SSE.** There is no streaming and no `Streaming` badge. The honest label is *live · 1s poll*. Each snapshot is complete, which is the whole reason React needs no merge logic.
- **`docs/ui/DESIGN.md` — screenshots are normative for layout only.** Never copy a number, a label, or a scenario out of them. They depict a React 17 → 18 JavaScript migration this Python-only analyzer cannot run.
- **Dark interface.** `docs/ui/DESIGN.md` commits to a dark developer-tool interface. This is a single-theme design; there is no light mode to maintain.
- **Backend run commands** (from `README.md`), needed by Tasks 1 and 13:
  - `cd backend && ./.venv/bin/python -m uvicorn upgradepilot.api.app:create_app --factory --port 8000`
  - `cd frontend && npm run dev` → http://localhost:5173, which proxies `/api` to port 8000.
- **Backend gates**, run from `backend/`: `.venv/bin/python -m pytest`, `.venv/bin/python -m mypy`, `.venv/bin/python -m ruff check`, `.venv/bin/python -m ruff format --check`. Baseline at the start of this phase: **1164 passed / 8 skipped**, mypy clean over 145 source files, ruff clean.
- **Frontend gates**, run from `frontend/`: `npm test -- --run`, `npx tsc -b`.

### The eight statuses and five views

This table is the contract between the backend's `RunStatus` and every task below. `idle` is a frontend-only state meaning *no run has been started*; the backend enum has seven members and no `idle`.

| `status` | View | Notes |
|---|---|---|
| `idle` (frontend-only, `threadId === null`) | `ConfigurationForm` | |
| `queued` | `ActivityTimeline` | Beyond the concurrency cap; work has not started |
| `running` | `ActivityTimeline` | |
| `awaiting_human` | `HumanReviewPanel` | Rendered **above a still-incomplete timeline** |
| `completed` | `ReportView` | |
| `completed_with_warnings` | `ReportView` | Warnings banner; failed checks surfaced |
| `failed` | `ErrorView` | Retry |
| `orphaned` | `ErrorView` | Resume-from-checkpoint, no decision needed |

### The eight workflow steps

Node ids are the backend's own, from `RunSnapshot.current_step` / `completed_steps`. `human_review` is the one step a run may legitimately skip.

| Node id | Label |
|---|---|
| `analyze_repo` | Repository Analysis |
| `inspect_dependency` | Dependency Analysis |
| `agentic_rag` | Evidence Retrieval |
| `assess_risk` | Risk Assessment |
| `human_review` | Human Review |
| `generate_plan` | Migration Plan |
| `validate_plan` | Validation |
| `finalize` | Report |

---

## File Structure

Split by responsibility. `derive/` exists so each rule with a right answer is one tested function rather than a habit repeated in six components.

**Created — tooling**

| File | Responsibility |
|---|---|
| `backend/scripts/dump_openapi.py` | Write the OpenAPI schema to `frontend/src/api/openapi.json`. Verified to work with no env and no running server: 4 paths, 66 schemas. |
| `frontend/src/test/setup.ts` | Vitest setup: jest-dom matchers, RTL cleanup. |
| `frontend/src/test/server.ts` | MSW `setupServer` and per-test handler helpers. |
| `frontend/src/test/fixtures.ts` | Typed `RunSnapshot` builders. The only place test data is shaped. |

**Created — source**

| File | Responsibility |
|---|---|
| `frontend/src/api/openapi.json` | Generated. Checked in so CI needs no running backend. |
| `frontend/src/api/schema.d.ts` | Generated by `openapi-typescript`. Never hand-edited. |
| `frontend/src/api/types.ts` | Named aliases into `schema.d.ts`. The only import path components use. |
| `frontend/src/api/client.ts` | `startRun`, `getStatus`, `resumeRun`, `getHealth`, `ApiFailure`. The only module that calls `fetch`. |
| `frontend/src/derive/view.ts` | `viewFor(status)` — status → view. |
| `frontend/src/derive/steps.ts` | `STEPS`, `stepStates(snapshot)` — the eight steps and their six states, including `skipped`. |
| `frontend/src/derive/cost.ts` | `costLabel(usage)` — the only place a cost figure is worded. |
| `frontend/src/hooks/useRunPolling.ts` | The only source of run state. |
| `frontend/src/hooks/useHealth.ts` | One-shot `/api/health` read for the sidebar. |
| `frontend/src/hooks/useSessionRuns.ts` | This tab's runs, in `sessionStorage`. |
| `frontend/src/components/AppShell.tsx` | The three regions plus the drawer. |
| `frontend/src/components/TopBar.tsx` | Product mark, run summary, `StatusPill` (`aria-live`), trace trigger. |
| `frontend/src/components/LeftSidebar.tsx` | New run, session runs, config summary, integration status. |
| `frontend/src/components/WorkflowTimeline.tsx` | Eight steps × six states. Sibling of the view, never a child. |
| `frontend/src/components/ConfigurationForm.tsx` | The `idle` view. Exactly `UserConstraints`. |
| `frontend/src/components/ActivityTimeline.tsx` | The `queued` / `running` view. |
| `frontend/src/components/HumanReviewPanel.tsx` | The `awaiting_human` view. Triple guard. |
| `frontend/src/components/ErrorView.tsx` | The `failed` / `orphaned` view. |
| `frontend/src/components/RunMetrics.tsx` | The right region. Survives view changes. |
| `frontend/src/components/AgentTraceDrawer.tsx` | Observable events only. |
| `frontend/src/components/EvidencePanel.tsx` | Sources, selected vs merely retrieved. Shared by the drawer and the report. |
| `frontend/src/components/report/ReportView.tsx` | Tab host. The only tab bar in the app. |
| `frontend/src/components/report/OverviewTab.tsx` | Risk verdict, confidence with ceilings, version discrepancy, breaking changes. |
| `frontend/src/components/report/RiskFactorsTab.tsx` | The seven-factor table with per-factor evidence. |
| `frontend/src/components/report/EvidenceTab.tsx` | Repo evidence first, then documents. |
| `frontend/src/components/report/PlanTab.tsx` | Steps, mitigations, decisions applied, unaddressed files, all ten checks. |
| `frontend/src/components/report/CodeTab.tsx` | `AffectedFile` → `UsageSite`. Existing code, cited. |
| `frontend/src/components/ui.tsx` | `Card`, `Panel`, `LevelBadge`, `Mono`, `EmptyState`. Shared primitives, no domain knowledge. |

**Modified**

| File | Change |
|---|---|
| `frontend/package.json` | Test scripts, `gen:api`, new dev dependencies. |
| `frontend/vite.config.ts` | Vitest block. |
| `frontend/tsconfig.app.json` | `"strict": true`. |
| `frontend/src/index.css` | Full dark token scale. |
| `frontend/src/App.tsx` | Replace the health-check page with the real shell. |
| `docs/adr/ADR-001-system-architecture.md` | Record resolved frontend dependency versions (rule 13). |
| `PLANNING.md` | Phase 10 checklist and exit evidence. |

---

## Task 1: Toolchain — generated types, a test runner, and strict TypeScript

Nothing here is a feature, and all of it is the ground every later task stands on: generated types (so no shape is hand-mirrored), a runner (so tests can exist), and `strict` (so the exhaustiveness checks Tasks 2–4 depend on actually fire). Folded into one task because none of the three is independently reviewable.

**Files:**
- Create: `backend/scripts/dump_openapi.py`
- Create: `frontend/src/api/openapi.json` (generated)
- Create: `frontend/src/api/schema.d.ts` (generated)
- Create: `frontend/src/api/types.ts`
- Create: `frontend/src/test/setup.ts`
- Create: `frontend/src/api/types.test.ts`
- Modify: `frontend/package.json`
- Modify: `frontend/vite.config.ts`
- Modify: `frontend/tsconfig.app.json`
- Modify: `docs/adr/ADR-001-system-architecture.md`

**Interfaces:**
- Consumes: nothing.
- Produces: `frontend/src/api/types.ts` exporting the type aliases every later task imports — `RunSnapshot`, `RunStatus`, `UsageView`, `TraceEvent`, `InterruptPayload`, `DecisionOption`, `HumanDecision`, `RiskAnalysis`, `RiskFactor`, `ConfidenceCeiling`, `MigrationPlan`, `MigrationStep`, `ValidationReport`, `ValidationOutcome`, `UnaddressedFile`, `FinalReport`, `AffectedFile`, `UsageSite`, `BreakingChange`, `SourceRef`, `RagContext`, `ApiError`, `ErrorResponse`, `StartRunRequest`, `StartResponse`, `ResumeRequest`, `DecisionInput`, `UserConstraints`, `HealthResponse`, `RiskLevel`, `EffortLevel`, `Severity`. Also `npm test`, `npm run gen:api`.

- [ ] **Step 1: Write the schema dump script**

The backend already declares its error shape on every route (`api/routes/agent.py:50` — "an error body that only exists at runtime is one the client renders as `[object Object]` the first time it is hit"), so the generated types carry errors too. Writing the schema to a file rather than fetching from a live server means CI needs no backend process, and the checked-in file is diffable — a contract change shows up in review.

```python
"""Write the OpenAPI schema where `openapi-typescript` can read it.

A file rather than a live fetch: CI generates types without starting a
server, and the checked-in result is diffable, so a change to the HTTP
contract appears in a pull request instead of surfacing as a frontend type
error days later.

`create_app` registers routes synchronously and only opens SQLite and Chroma
inside its lifespan, so this touches no store and needs no environment.
"""

import json
from pathlib import Path

from upgradepilot.api.app import create_app

DESTINATION = (
    Path(__file__).resolve().parents[2] / "frontend" / "src" / "api" / "openapi.json"
)


def main() -> None:
    schema = create_app().openapi()
    DESTINATION.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n")
    print(f"wrote {DESTINATION} ({len(schema['components']['schemas'])} schemas)")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it and check the output**

```bash
cd backend && .venv/bin/python scripts/dump_openapi.py
```

Expected: `wrote .../frontend/src/api/openapi.json (66 schemas)`. Then confirm the enum survived — `RunStatus` reaches the schema only through nested models, and `api/schemas.py:RISK_LEVELS` exists precisely to stop that erasing an enum into `string`:

```bash
cd frontend && node -e "
const s = require('./src/api/openapi.json');
console.log('paths', Object.keys(s.paths));
console.log('RunStatus', s.components.schemas.RunStatus.enum);
console.log('RiskLevel', s.components.schemas.RiskLevel.enum);
"
```

Expected: the four `/api/...` paths, the seven `RunStatus` values, and the three `RiskLevel` values. If `RunStatus.enum` is missing, stop — every later task's exhaustiveness check depends on it.

- [ ] **Step 3: Install the dev dependencies and record what resolved**

Reasons, per rule 12. `openapi-typescript`: spec §10 sanctions it by name — "hand-mirrored interfaces drifting from Pydantic response models is a real bug class, eliminated for one dev-only package". `@testing-library/react`, `@testing-library/user-event`, `msw`, `jsdom`: spec §11 names Vitest, React Testing Library and MSW as the frontend test layer; `jsdom` is the DOM RTL needs and `user-event` is how a click is simulated as a user performs it rather than as a synthetic event. `@testing-library/jest-dom`: assertion vocabulary (`toBeDisabled`) whose absence would mean hand-written DOM assertions in every component test.

```bash
cd frontend
npm install -D openapi-typescript @testing-library/react @testing-library/user-event @testing-library/jest-dom msw jsdom
node -e "
const p = require('./package.json');
for (const k of ['openapi-typescript','@testing-library/react','@testing-library/user-event','@testing-library/jest-dom','msw','jsdom'])
  console.log(k, p.devDependencies[k]);
"
```

Replace each resolved `^x.y.z` with the exact `x.y.z` in `package.json` (rule 13 — pin, do not float), re-run `npm install`, then record the six resolved versions in ADR-001's dependency table beside the backend pins.

- [ ] **Step 4: Add the scripts**

```json
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "preview": "vite preview",
    "gen:api": "openapi-typescript src/api/openapi.json -o src/api/schema.d.ts",
    "test": "vitest",
    "typecheck": "tsc -b"
  },
```

- [ ] **Step 5: Generate the types**

```bash
cd frontend && npm run gen:api && head -20 src/api/schema.d.ts && grep -c "" src/api/schema.d.ts
```

Expected: a generated banner reading `do not make direct changes to the file`, and a non-trivial line count. Commit `schema.d.ts` — a generated file that CI can regenerate and diff is a contract test.

- [ ] **Step 6: Add the Vitest block to `vite.config.ts`**

`globals: false` so `describe`/`it`/`expect` are imported rather than ambient — an ambient global is a type that exists in tests and nowhere else, and the import is one line.

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
  test: {
    environment: "jsdom",
    globals: false,
    setupFiles: ["./src/test/setup.ts"],
    restoreMocks: true,
  },
});
```

- [ ] **Step 7: Write the test setup**

```ts
import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// RTL does not unmount between tests on its own when `globals` is false, and a
// component left mounted keeps its polling timers running into the next test.
afterEach(cleanup);
```

- [ ] **Step 8: Turn on `strict`**

`tsconfig.app.json` has no `strict` key, so strict is off. Tasks 2–4 rely on exhaustive `switch` statements over the status and level unions failing the build when a case is missing; without `strict` (specifically `strictNullChecks`) that check is toothless, and the backend is held to `mypy` strict over all 145 files.

Add to `compilerOptions`, after `"skipLibCheck": true`:

```json
    "strict": true,
```

- [ ] **Step 9: Write the failing test for the type aliases**

A type-only module has nothing to assert at runtime, so this asserts the *values* that must agree with the generated union — which is the part that can silently drift.

```ts
import { describe, expect, it } from "vitest";

import { ALL_STATUSES, TERMINAL_STATUSES } from "./types";

describe("status unions", () => {
  it("lists exactly the seven statuses the backend derives", () => {
    expect([...ALL_STATUSES].sort()).toEqual([
      "awaiting_human",
      "completed",
      "completed_with_warnings",
      "failed",
      "orphaned",
      "queued",
      "running",
    ]);
  });

  it("treats a run that will not change on its own as terminal", () => {
    // `orphaned` is terminal for polling: its process is gone, so no amount of
    // waiting moves it. It is not terminal for the *run*, which a resume
    // continues from the checkpoint.
    expect([...TERMINAL_STATUSES].sort()).toEqual([
      "completed",
      "completed_with_warnings",
      "failed",
      "orphaned",
    ]);
  });

  it("does not treat awaiting_human as terminal", () => {
    // A resume can arrive from another client, and the transition out of the
    // decision panel is exactly what the user is waiting to see.
    expect(TERMINAL_STATUSES.has("awaiting_human")).toBe(false);
  });
});
```

- [ ] **Step 10: Run it to verify it fails**

```bash
cd frontend && npm test -- --run src/api/types.test.ts
```

Expected: FAIL — `Failed to resolve import "./types"`.

- [ ] **Step 11: Write `src/api/types.ts`**

Every alias points into the generated schema. The two exported sets are values, not types, because runtime code iterates them.

```ts
/**
 * Named aliases into the generated schema, and the two status sets that are
 * needed at runtime.
 *
 * Components import from here and never from `./schema` directly, so a rename
 * in the backend's models shows up as one broken line in this file rather than
 * thirty across the tree. Nothing here is hand-written structure: every alias
 * resolves to a Pydantic model, which is what makes drift impossible rather
 * than merely unlikely (spec §10).
 */

import type { components } from "./schema";

type S = components["schemas"];

export type RunSnapshot = S["RunSnapshot"];
export type RunStatus = S["RunStatus"];
export type UsageView = S["UsageView"];
export type TraceEvent = S["TraceEvent"];
export type InterruptPayload = S["InterruptPayload"];
export type DecisionOption = S["DecisionOption"];
export type HumanDecision = S["HumanDecision"];
export type DecisionApplication = S["DecisionApplication"];
export type RiskAnalysis = S["RiskAnalysis"];
export type RiskFactor = S["RiskFactor"];
export type ConfidenceCeiling = S["ConfidenceCeiling"];
export type MigrationPlan = S["MigrationPlan"];
export type MigrationStep = S["MigrationStep"];
export type UnaddressedFile = S["UnaddressedFile"];
export type ValidationReport = S["ValidationReport"];
export type ValidationOutcome = S["ValidationOutcome"];
export type FinalReport = S["FinalReport"];
export type AffectedFile = S["AffectedFile"];
export type UsageSite = S["UsageSite"];
export type BreakingChange = S["BreakingChange"];
export type SourceRef = S["SourceRef"];
export type RagContext = S["RagContext"];
export type ApiError = S["ApiError"];
export type ErrorResponse = S["ErrorResponse"];
export type StartRunRequest = S["StartRunRequest"];
export type StartResponse = S["StartResponse"];
export type ResumeRequest = S["ResumeRequest"];
export type DecisionInput = S["DecisionInput"];
export type UserConstraints = S["UserConstraints"];
export type HealthResponse = S["HealthResponse"];
export type RiskLevel = S["RiskLevel"];
export type EffortLevel = S["EffortLevel"];
export type Severity = S["Severity"];
export type ErrorCode = S["ErrorCode"];

/** Every status the backend derives. `idle` is not among them — see below. */
export const ALL_STATUSES: ReadonlySet<RunStatus> = new Set([
  "queued",
  "running",
  "awaiting_human",
  "completed",
  "completed_with_warnings",
  "failed",
  "orphaned",
]);

/**
 * Statuses where polling stops because nothing further will change on its own.
 *
 * `orphaned` is here and `awaiting_human` is not, and both are deliberate. An
 * orphaned run's process is gone, so continuing to poll is asking a question
 * whose answer cannot change until someone resumes it. A run awaiting a human
 * *can* change without this client doing anything — another client may answer
 * it — and the transition out of the decision panel is the single thing the
 * user is watching for.
 */
export const TERMINAL_STATUSES: ReadonlySet<RunStatus> = new Set([
  "completed",
  "completed_with_warnings",
  "failed",
  "orphaned",
]);

/**
 * The frontend's own extra state: no run has been started, so there is no
 * status to read. The backend enum has seven members and no `idle` — status is
 * derived from a checkpoint, and a run that does not exist has no checkpoint.
 */
export type ViewStatus = RunStatus | "idle";
```

- [ ] **Step 12: Run the test and the typecheck**

```bash
cd frontend && npm test -- --run src/api/types.test.ts && npx tsc -b
```

Expected: 3 passing, and `tsc -b` silent. If `tsc -b` reports errors in `src/App.tsx`, that is `strict` biting the existing health-check page — Task 7 replaces it. Fix the errors minimally now (add the missing null guard) rather than disabling `strict`.

- [ ] **Step 13: Commit**

```bash
cd /Users/nzrsrd/Code/upgrade_pilot
git add backend/scripts/dump_openapi.py frontend/package.json frontend/package-lock.json \
        frontend/vite.config.ts frontend/tsconfig.app.json frontend/src/api frontend/src/test \
        docs/adr/ADR-001-system-architecture.md frontend/src/App.tsx
git commit -m "chore(frontend): types from the backend's own schema, and a runner

Hand-mirrored interfaces drifting from Pydantic response models is a real bug
class; spec 10 buys it out for one dev-only package. The schema is dumped to a
checked-in file rather than fetched from a live server, so CI generates types
without starting a backend and a contract change appears as a diff in review.

\`strict\` was off. Tasks 2-4 rely on exhaustive switches over the status and
level unions failing the build when a case is missing, which without
strictNullChecks they do not -- and the backend is held to mypy strict over
all 145 files.

Resolved versions recorded in ADR-001 per rule 13."
```

---

## Task 2: `viewFor` — one place that decides what the user sees

**Files:**
- Create: `frontend/src/derive/view.ts`
- Test: `frontend/src/derive/view.test.ts`

**Interfaces:**
- Consumes: `ViewStatus`, `RunStatus` from `api/types`.
- Produces: `export type View = "configuration" | "activity" | "human-review" | "report" | "error"` and `export function viewFor(status: ViewStatus): View`.

- [ ] **Step 1: Write the failing test**

```ts
import { describe, expect, it } from "vitest";

import { ALL_STATUSES } from "../api/types";
import type { ViewStatus } from "../api/types";
import { viewFor } from "./view";

describe("viewFor", () => {
  it("shows the configuration form when no run has been started", () => {
    expect(viewFor("idle")).toBe("configuration");
  });

  it("shows activity for a run that is queued as well as one that is running", () => {
    // A queued run has not started work. Reporting it as running would be a
    // lie about work that has not happened, but the user is still watching a
    // run, not configuring one.
    expect(viewFor("queued")).toBe("activity");
    expect(viewFor("running")).toBe("activity");
  });

  it("shows the human review panel while a decision is outstanding", () => {
    expect(viewFor("awaiting_human")).toBe("human-review");
  });

  it("shows the report for both completed statuses", () => {
    // A run with failed validation checks still produced a report, and hiding
    // it would hide the failures with it.
    expect(viewFor("completed")).toBe("report");
    expect(viewFor("completed_with_warnings")).toBe("report");
  });

  it("shows an error view for a failed run and for an orphaned one", () => {
    // `orphaned` is the status this mapping exists for: a checkpoint that
    // outlived its process cannot be represented by a spinner, and giving it
    // no view of its own ships exactly the spinner that never resolves.
    expect(viewFor("failed")).toBe("error");
    expect(viewFor("orphaned")).toBe("error");
  });

  it("maps every status the backend can derive", () => {
    // Guards the case the table cannot: a status added to the backend enum and
    // regenerated into the schema, with no view chosen for it.
    for (const status of ALL_STATUSES) {
      expect(viewFor(status)).toBeTypeOf("string");
    }
  });

  it("routes the frontend's own idle state too", () => {
    const every: ViewStatus[] = [...ALL_STATUSES, "idle"];
    expect(every.map(viewFor).filter(Boolean)).toHaveLength(8);
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd frontend && npm test -- --run src/derive/view.test.ts
```

Expected: FAIL — `Failed to resolve import "./view"`.

- [ ] **Step 3: Write the implementation**

The exhaustive `switch` with no `default` is the point: with `strict` on, adding a status to the backend enum and regenerating makes this file fail to compile, which is a build error rather than a run that silently renders the wrong thing.

```ts
/**
 * Status to view. Spec §10's table, and nothing else decides this.
 *
 * One route, and the view is derived rather than navigated. That is what makes
 * "the workflow can never look finished while waiting" enforceable: there is
 * no code path by which a user reaches the report while a decision is
 * outstanding, because reaching it would require a status that says otherwise.
 */

import type { ViewStatus } from "../api/types";

export type View = "configuration" | "activity" | "human-review" | "report" | "error";

export function viewFor(status: ViewStatus): View {
  switch (status) {
    case "idle":
      return "configuration";
    case "queued":
    case "running":
      return "activity";
    case "awaiting_human":
      return "human-review";
    case "completed":
    case "completed_with_warnings":
      return "report";
    case "failed":
    case "orphaned":
      return "error";
  }
}
```

There is deliberately no `default` clause. A status added to the backend and regenerated into the schema makes this function fail to compile — the failure a reviewer wants, rather than a run that falls through to a blank screen.

- [ ] **Step 4: Run the test and the typecheck**

```bash
cd frontend && npm test -- --run src/derive/view.test.ts && npx tsc -b
```

Expected: 7 passing, `tsc -b` silent.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/derive/view.ts frontend/src/derive/view.test.ts
git commit -m "feat(ui): the status-to-view mapping, exhaustive by construction

Spec 10's table as one function with no default clause, so a status added to
the backend enum fails the build instead of falling through to a blank screen.
\`orphaned\` gets a view of its own, which is the whole reason that status
exists -- a checkpoint that outlived its process cannot be a spinner."
```

---

## Task 3: `stepStates` — eight steps, and the one that is meant to be missing

**Files:**
- Create: `frontend/src/derive/steps.ts`
- Test: `frontend/src/derive/steps.test.ts`

**Interfaces:**
- Consumes: `RunSnapshot`, `RunStatus` from `api/types`.
- Produces:
  - `export const STEPS: readonly { node: string; label: string }[]` — eight entries, in order.
  - `export type StepState = "pending" | "running" | "completed" | "skipped" | "awaiting" | "failed"`
  - `export type Step = { node: string; label: string; state: StepState }`
  - `export function stepStates(snapshot: RunSnapshot | null): Step[]` — always eight entries.

- [ ] **Step 1: Write the failing test**

```ts
import { describe, expect, it } from "vitest";

import { aSnapshot } from "../test/fixtures";
import { STEPS, stepStates } from "./steps";

const stateOf = (steps: ReturnType<typeof stepStates>, node: string) =>
  steps.find((step) => step.node === node)?.state;

describe("stepStates", () => {
  it("always reports all eight steps, in workflow order", () => {
    expect(STEPS.map((step) => step.node)).toEqual([
      "analyze_repo",
      "inspect_dependency",
      "agentic_rag",
      "assess_risk",
      "human_review",
      "generate_plan",
      "validate_plan",
      "finalize",
    ]);
    expect(stepStates(null)).toHaveLength(8);
  });

  it("reports every step pending before a run exists", () => {
    expect(stepStates(null).every((step) => step.state === "pending")).toBe(true);
  });

  it("marks finished steps completed and the current one running", () => {
    const steps = stepStates(
      aSnapshot({
        status: "running",
        completed_steps: ["analyze_repo", "inspect_dependency"],
        current_step: "agentic_rag",
      }),
    );

    expect(stateOf(steps, "analyze_repo")).toBe("completed");
    expect(stateOf(steps, "inspect_dependency")).toBe("completed");
    expect(stateOf(steps, "agentic_rag")).toBe("running");
    expect(stateOf(steps, "assess_risk")).toBe("pending");
  });

  it("marks human review as awaiting, and leaves later steps incomplete", () => {
    // The guarantee this encodes: the workflow can never look finished while
    // it is waiting for an answer.
    const steps = stepStates(
      aSnapshot({
        status: "awaiting_human",
        completed_steps: ["analyze_repo", "inspect_dependency", "agentic_rag", "assess_risk"],
        current_step: "human_review",
      }),
    );

    expect(stateOf(steps, "human_review")).toBe("awaiting");
    expect(stateOf(steps, "generate_plan")).toBe("pending");
    expect(stateOf(steps, "validate_plan")).toBe("pending");
    expect(stateOf(steps, "finalize")).toBe("pending");
  });

  it("marks human review skipped when constraints settled the question", () => {
    // Spec 8.2: when the constraints already decide, no interrupt fires and
    // the trace records "resolved by constraints". Without a skipped state a
    // correct run looks like it lost a step.
    const steps = stepStates(
      aSnapshot({
        status: "running",
        completed_steps: [
          "analyze_repo",
          "inspect_dependency",
          "agentic_rag",
          "assess_risk",
          "generate_plan",
        ],
        current_step: "validate_plan",
      }),
    );

    expect(stateOf(steps, "human_review")).toBe("skipped");
    expect(stateOf(steps, "generate_plan")).toBe("completed");
  });

  it("marks human review skipped on a completed run that was never asked", () => {
    const steps = stepStates(
      aSnapshot({
        status: "completed",
        completed_steps: [
          "analyze_repo",
          "inspect_dependency",
          "agentic_rag",
          "assess_risk",
          "generate_plan",
          "validate_plan",
          "finalize",
        ],
        current_step: null,
      }),
    );

    expect(stateOf(steps, "human_review")).toBe("skipped");
    expect(stateOf(steps, "finalize")).toBe("completed");
  });

  it("prefers awaiting over completed on a second interrupt", () => {
    // `human_decisions` is an append channel so interrupts can fire in
    // sequence, and `completed_steps` records a node once. A second question
    // must not render as a step already behind us.
    const steps = stepStates(
      aSnapshot({
        status: "awaiting_human",
        completed_steps: [
          "analyze_repo",
          "inspect_dependency",
          "agentic_rag",
          "assess_risk",
          "human_review",
        ],
        current_step: "human_review",
      }),
    );

    expect(stateOf(steps, "human_review")).toBe("awaiting");
  });

  it("marks the step a failed run stopped on as failed", () => {
    const steps = stepStates(
      aSnapshot({
        status: "failed",
        completed_steps: ["analyze_repo"],
        current_step: "inspect_dependency",
      }),
    );

    expect(stateOf(steps, "analyze_repo")).toBe("completed");
    expect(stateOf(steps, "inspect_dependency")).toBe("failed");
    expect(stateOf(steps, "agentic_rag")).toBe("pending");
  });

  it("does not mark anything running on an orphaned run", () => {
    // Nothing is running: the process is gone. Showing a spinner on the step
    // it died in is the exact misreport `orphaned` exists to prevent.
    const steps = stepStates(
      aSnapshot({
        status: "orphaned",
        completed_steps: ["analyze_repo"],
        current_step: "inspect_dependency",
      }),
    );

    expect(steps.some((step) => step.state === "running")).toBe(false);
    expect(stateOf(steps, "inspect_dependency")).toBe("pending");
  });

  it("never skips a step that is not human review", () => {
    // Only `human_review` is skippable. A gap anywhere else is a defect, and
    // rendering it as "skipped" would present a broken run as a normal one.
    const steps = stepStates(
      aSnapshot({
        status: "running",
        completed_steps: ["analyze_repo", "agentic_rag"],
        current_step: "assess_risk",
      }),
    );

    expect(stateOf(steps, "inspect_dependency")).toBe("pending");
    expect(steps.filter((step) => step.state === "skipped")).toHaveLength(0);
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd frontend && npm test -- --run src/derive/steps.test.ts
```

Expected: FAIL — `Failed to resolve import "./steps"` (and `../test/fixtures`, which Task 4 Step 3 creates; if executing tasks strictly in order, create `fixtures.ts` here — the builder below is reproduced in Task 4 for readers arriving out of order).

- [ ] **Step 3: Write `test/fixtures.ts`**

One place test data is shaped, typed against the generated schema so a fixture cannot describe a response the API could not send.

```ts
/**
 * Typed builders for server shapes.
 *
 * Typed against the generated schema on purpose: a hand-shaped literal can
 * describe a response the API is incapable of sending, and a test that passes
 * against an impossible fixture is worse than no test.
 */

import type { RunSnapshot, UsageView } from "../api/types";

export function anUsageView(overrides: Partial<UsageView> = {}): UsageView {
  return {
    calls: 0,
    input_tokens: 0,
    output_tokens: 0,
    total_tokens: 0,
    estimated: false,
    pricing_complete: true,
    estimated_cost_usd: 0,
    by_node: [],
    ...overrides,
  };
}

export function aSnapshot(overrides: Partial<RunSnapshot> = {}): RunSnapshot {
  return {
    thread_id: "t-1",
    status: "running",
    current_step: null,
    completed_steps: [],
    trace: [],
    usage: anUsageView(),
    affected_files: [],
    breaking_changes: [],
    retrieved_sources: [],
    rag_context: null,
    risk_analysis: null,
    migration_plan: null,
    validation: null,
    human_decisions: [],
    pending_decision: null,
    final_report: null,
    errors: [],
    ...overrides,
  };
}
```

If `tsc` reports missing properties here, the generated schema is the authority — add them with the value the backend defaults to, and do not widen the type.

- [ ] **Step 4: Write the implementation**

```ts
/**
 * The eight workflow steps and their six states.
 *
 * `human_review` is the only skippable step, and `skipped` exists for it
 * alone. Spec 8.2: when the user's constraints already settle the choice, no
 * interrupt fires. Rendering that as a pending or missing step would make a
 * correct run look like it lost one — while rendering a *genuine* gap
 * elsewhere as "skipped" would make a broken run look normal. So the rule is
 * narrow on purpose.
 */

import type { RunSnapshot } from "../api/types";

export const STEPS = [
  { node: "analyze_repo", label: "Repository Analysis" },
  { node: "inspect_dependency", label: "Dependency Analysis" },
  { node: "agentic_rag", label: "Evidence Retrieval" },
  { node: "assess_risk", label: "Risk Assessment" },
  { node: "human_review", label: "Human Review" },
  { node: "generate_plan", label: "Migration Plan" },
  { node: "validate_plan", label: "Validation" },
  { node: "finalize", label: "Report" },
] as const;

const SKIPPABLE = "human_review";

export type StepState = "pending" | "running" | "completed" | "skipped" | "awaiting" | "failed";

export type Step = { node: string; label: string; state: StepState };

export function stepStates(snapshot: RunSnapshot | null): Step[] {
  if (snapshot === null) {
    return STEPS.map((step) => ({ ...step, state: "pending" as StepState }));
  }

  const completed = new Set(snapshot.completed_steps);
  const laterCompleted = (index: number) =>
    STEPS.slice(index + 1).some((step) => completed.has(step.node));

  return STEPS.map((step, index): Step => {
    // Awaiting outranks completed: `completed_steps` records a node once, so a
    // second interrupt on `human_review` would otherwise render as a step
    // already behind us.
    if (step.node === SKIPPABLE && snapshot.status === "awaiting_human") {
      return { ...step, state: "awaiting" };
    }
    if (completed.has(step.node)) {
      return { ...step, state: "completed" };
    }
    if (snapshot.status === "failed" && step.node === snapshot.current_step) {
      return { ...step, state: "failed" };
    }
    if (step.node === SKIPPABLE && laterCompleted(index)) {
      return { ...step, state: "skipped" };
    }
    // Only a live run has something running. An orphaned run's process is
    // gone, so a spinner on the step it died in is the exact misreport that
    // status exists to prevent.
    const live = snapshot.status === "running" || snapshot.status === "queued";
    if (live && step.node === snapshot.current_step) {
      return { ...step, state: "running" };
    }
    return { ...step, state: "pending" };
  });
}
```

- [ ] **Step 5: Run the test and the typecheck**

```bash
cd frontend && npm test -- --run src/derive/steps.test.ts && npx tsc -b
```

Expected: 10 passing, `tsc -b` silent.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/derive/steps.ts frontend/src/derive/steps.test.ts frontend/src/test/fixtures.ts
git commit -m "feat(ui): eight workflow steps, and the one meant to be missing

\`skipped\` exists for \`human_review\` alone. When constraints already settle
the choice no interrupt fires (spec 8.2), and without this state a correct run
looks like it lost a step -- while treating a gap anywhere else as skipped
would make a broken run look normal, so the rule is deliberately narrow.

Two orderings carry their own reason. Awaiting outranks completed, because
\`completed_steps\` records a node once and sequential interrupts would
otherwise render a live question as history. And nothing is \`running\` on an
orphaned run: its process is gone, and a spinner on the step it died in is
precisely the misreport that status exists to prevent."
```

---

## Task 4: `costLabel` — the only place a cost figure is worded

`UsageView` carries `estimated` and `pricing_complete` because a total without them is misreadable, and spec §9.4's reason for the second is blunt: when it is false the cost is a **lower bound**, and that flag is the only thing that says so. Phase 0 resolved the stack to OpenRouter, so pricing-unknown is the ordinary case rather than the edge one.

**Files:**
- Create: `frontend/src/derive/cost.ts`
- Test: `frontend/src/derive/cost.test.ts`

**Interfaces:**
- Consumes: `UsageView` from `api/types`; `anUsageView` from `test/fixtures`.
- Produces: `export type CostLabel = { text: string; note: string | null; lowerBound: boolean; estimated: boolean }` and `export function costLabel(usage: UsageView): CostLabel`.

- [ ] **Step 1: Write the failing test**

```ts
import { describe, expect, it } from "vitest";

import { anUsageView } from "../test/fixtures";
import { costLabel } from "./cost";

describe("costLabel", () => {
  it("prints a known cost plainly", () => {
    const label = costLabel(anUsageView({ estimated_cost_usd: 0.00056, pricing_complete: true }));

    expect(label.text).toBe("$0.00056");
    expect(label.note).toBeNull();
    expect(label.lowerBound).toBe(false);
  });

  it("says not priced rather than printing zero", () => {
    // `$0.00` for an unpriced run is the single most misleading thing this
    // panel could say: it reads as "this was free" when it means "we do not
    // know". Spec 11 layer 1 asserts the backend returns None here for the
    // same reason.
    const label = costLabel(anUsageView({ estimated_cost_usd: null, calls: 4 }));

    expect(label.text).toBe("not priced");
    expect(label.note).toBe("no price is known for the model used");
    expect(label.lowerBound).toBe(false);
  });

  it("marks an incomplete price as a lower bound", () => {
    const label = costLabel(
      anUsageView({ estimated_cost_usd: 0.00056, pricing_complete: false }),
    );

    expect(label.text).toBe("≥ $0.00056");
    expect(label.note).toBe("lower bound — some calls have no price");
    expect(label.lowerBound).toBe(true);
  });

  it("reports estimated tokens independently of pricing", () => {
    // Two different uncertainties. `estimated` says a token count came from a
    // local tokenizer rather than the provider; `pricing_complete` says a
    // price was missing. Either can be true without the other.
    const label = costLabel(
      anUsageView({ estimated_cost_usd: 0.002, pricing_complete: true, estimated: true }),
    );

    expect(label.estimated).toBe(true);
    expect(label.lowerBound).toBe(false);
    expect(label.text).toBe("$0.00200");
  });

  it("carries both flags at once when both apply", () => {
    const label = costLabel(
      anUsageView({ estimated_cost_usd: 0.002, pricing_complete: false, estimated: true }),
    );

    expect(label.lowerBound).toBe(true);
    expect(label.estimated).toBe(true);
  });

  it("shows five decimals for the small figures this product actually produces", () => {
    // A real run costs $0.00056. Two decimals would render every run this
    // system has ever performed as $0.00.
    expect(costLabel(anUsageView({ estimated_cost_usd: 0.00001 })).text).toBe("$0.00001");
    expect(costLabel(anUsageView({ estimated_cost_usd: 1.5 })).text).toBe("$1.50");
    expect(costLabel(anUsageView({ estimated_cost_usd: 12.3456 })).text).toBe("$12.35");
  });

  it("prints a genuine zero as zero, not as unknown", () => {
    // A priced run with no calls yet cost nothing, and that is a fact rather
    // than an absence. Only `null` means unknown.
    const label = costLabel(anUsageView({ estimated_cost_usd: 0, pricing_complete: true }));

    expect(label.text).toBe("$0.00000");
    expect(label.note).toBeNull();
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd frontend && npm test -- --run src/derive/cost.test.ts
```

Expected: FAIL — `Failed to resolve import "./cost"`.

- [ ] **Step 3: Write the implementation**

```ts
/**
 * How a cost figure is allowed to be worded. One function, because the rule is
 * about honesty rather than formatting and a second copy of it would be a
 * second chance to get it wrong.
 *
 * Three states, and each exists because the alternative misleads:
 *
 *   - `estimated_cost_usd === null` prints "not priced", never `$0.00`. Zero
 *     reads as free; the truth is that no price is known for the model.
 *   - `pricing_complete === false` prints a `≥` prefix. Spec 9.4: when this is
 *     false the cost is a lower bound, and the flag is the only thing that
 *     says so. Phase 0 resolved the stack to OpenRouter, so this is the
 *     ordinary case rather than the edge one.
 *   - `estimated` is separate and orthogonal: it says a token count came from
 *     a local tokenizer rather than the provider. Either flag can be true
 *     without the other.
 */

import type { UsageView } from "../api/types";

export type CostLabel = {
  text: string;
  note: string | null;
  lowerBound: boolean;
  estimated: boolean;
};

/**
 * Five decimals below a cent. A real run of this product costs $0.00056, which
 * two decimals would render as `$0.00` — every run it has ever performed,
 * reported as free.
 */
function money(value: number): string {
  return value < 0.01 ? `$${value.toFixed(5)}` : `$${value.toFixed(2)}`;
}

export function costLabel(usage: UsageView): CostLabel {
  const estimated = usage.estimated;

  if (usage.estimated_cost_usd === null) {
    return {
      text: "not priced",
      note: "no price is known for the model used",
      lowerBound: false,
      estimated,
    };
  }

  const figure = money(usage.estimated_cost_usd);

  if (!usage.pricing_complete) {
    return {
      text: `≥ ${figure}`,
      note: "lower bound — some calls have no price",
      lowerBound: true,
      estimated,
    };
  }

  return { text: figure, note: null, lowerBound: false, estimated };
}
```

- [ ] **Step 4: Run the test and the typecheck**

```bash
cd frontend && npm test -- --run src/derive/cost.test.ts && npx tsc -b
```

Expected: 7 passing, `tsc -b` silent.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/derive/cost.ts frontend/src/derive/cost.test.ts
git commit -m "feat(ui): a cost figure that says what it does not know

Three states, each because the alternative misleads. A null cost prints "not
priced" rather than \$0.00, because zero reads as free when it means unknown.
An incomplete price gets a >= prefix: spec 9.4 makes the cost a lower bound
when \`pricing_complete\` is false, and that flag is the only thing that says
so. \`estimated\` is orthogonal -- it is about token counts, not prices.

Five decimals below a cent, because a real run costs \$0.00056 and two
decimals would report every run this system has performed as free."
```

---

## Task 5: `client.ts` — the only module that calls `fetch`

**Files:**
- Create: `frontend/src/api/client.ts`
- Create: `frontend/src/test/server.ts`
- Test: `frontend/src/api/client.test.ts`
- Modify: `frontend/src/test/setup.ts`

**Interfaces:**
- Consumes: types from `api/types`.
- Produces:
  - `export class ApiFailure extends Error` with `readonly httpStatus: number` and `readonly error: ApiError`.
  - `export function getStatus(threadId: string, signal?: AbortSignal): Promise<RunSnapshot>`
  - `export function startRun(body: StartRunRequest, signal?: AbortSignal): Promise<StartResponse>`
  - `export function resumeRun(body: ResumeRequest, signal?: AbortSignal): Promise<StartResponse>`
  - `export function getHealth(signal?: AbortSignal): Promise<HealthResponse>`
- Also produces `test/server.ts` exporting `server` (an MSW `SetupServerApi`) for later tasks.

- [ ] **Step 1: Write the MSW server module**

```ts
/**
 * One MSW server for the whole suite, started once in `setup.ts`.
 *
 * `onUnhandledRequest: "error"` on purpose: a request nobody stubbed is a test
 * that is quietly exercising the network, and the failure mode is a suite that
 * passes on one machine and hangs on another.
 */

import { setupServer } from "msw/node";

export const server = setupServer();
```

- [ ] **Step 2: Wire it into the setup file**

Replace `src/test/setup.ts` with:

```ts
import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { afterAll, afterEach, beforeAll } from "vitest";

import { server } from "./server";

beforeAll(() => {
  server.listen({ onUnhandledRequest: "error" });
});

// RTL does not unmount between tests when `globals` is false, and a component
// left mounted keeps its polling timers running into the next test.
afterEach(() => {
  cleanup();
  server.resetHandlers();
});

afterAll(() => {
  server.close();
});
```

- [ ] **Step 3: Write the failing test**

```ts
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import { server } from "../test/server";
import { aSnapshot } from "../test/fixtures";
import { ApiFailure, getStatus, resumeRun, startRun } from "./client";

const BASE = "http://localhost";

describe("client", () => {
  it("returns a parsed snapshot on success", async () => {
    const snapshot = aSnapshot({ thread_id: "t-9", status: "awaiting_human" });
    server.use(http.get(`${BASE}/api/agent/status/t-9`, () => HttpResponse.json(snapshot)));

    await expect(getStatus("t-9")).resolves.toMatchObject({
      thread_id: "t-9",
      status: "awaiting_human",
    });
  });

  it("encodes the thread id into the path", async () => {
    // A thread id is a uuid today, but a path built by concatenation is a
    // request-forgery shape waiting for the day it is not.
    server.use(
      http.get(`${BASE}/api/agent/status/:threadId`, ({ params }) =>
        HttpResponse.json(aSnapshot({ thread_id: String(params.threadId) })),
      ),
    );

    const snapshot = await getStatus("a b/c");
    expect(snapshot.thread_id).toBe("a b/c");
  });

  it("throws an ApiFailure carrying the server's own error body", async () => {
    // The backend declares its error shape on every route so the client never
    // has to guess. Rendering `[object Object]` is the failure this prevents.
    server.use(
      http.get(`${BASE}/api/agent/status/nope`, () =>
        HttpResponse.json(
          { error: { code: "thread_not_found", message: "No run with that id exists.", retryable: false, node: null } },
          { status: 404 },
        ),
      ),
    );

    const failure = await getStatus("nope").catch((error: unknown) => error);

    expect(failure).toBeInstanceOf(ApiFailure);
    expect((failure as ApiFailure).httpStatus).toBe(404);
    expect((failure as ApiFailure).error.code).toBe("thread_not_found");
    expect((failure as ApiFailure).message).toBe("No run with that id exists.");
  });

  it("surfaces a 409 as an ApiFailure the caller can branch on", async () => {
    // The third and only real duplicate-submit guard.
    server.use(
      http.post(`${BASE}/api/agent/resume`, () =>
        HttpResponse.json(
          { error: { code: "thread_not_awaiting_input", message: "That run is not waiting for an answer.", retryable: false, node: null } },
          { status: 409 },
        ),
      ),
    );

    const failure = await resumeRun({ thread_id: "t-1", decision: null }).catch(
      (error: unknown) => error,
    );

    expect((failure as ApiFailure).httpStatus).toBe(409);
  });

  it("synthesises an error when the body is not the declared shape", async () => {
    // A proxy or gateway can answer with HTML. Without this the client throws
    // a JSON parse error, which tells the user nothing about what happened.
    server.use(
      http.post(`${BASE}/api/agent/start`, () =>
        HttpResponse.text("<html>502 Bad Gateway</html>", { status: 502 }),
      ),
    );

    const failure = await startRun({
      repo: { url: "https://example.invalid/r.git", path: null },
      dependency: { name: "pydantic", current_version: "1.10.13", target_version: "2.9.2" },
      constraints: { zero_downtime: false, minimize_effort: false, deadline: null, risk_tolerance: "medium" },
    }).catch((error: unknown) => error);

    expect(failure).toBeInstanceOf(ApiFailure);
    expect((failure as ApiFailure).httpStatus).toBe(502);
    expect((failure as ApiFailure).error.code).toBe("internal");
    expect((failure as ApiFailure).error.retryable).toBe(true);
  });

  it("lets an abort propagate rather than dressing it as a server error", async () => {
    // The polling hook aborts on unmount. An abort reported as a failure would
    // paint an error banner every time the user navigates away.
    server.use(http.get(`${BASE}/api/agent/status/t-1`, () => HttpResponse.json(aSnapshot())));
    const controller = new AbortController();
    controller.abort();

    const failure = await getStatus("t-1", controller.signal).catch((error: unknown) => error);

    expect(failure).not.toBeInstanceOf(ApiFailure);
    expect((failure as Error).name).toBe("AbortError");
  });

  it("posts a start request as JSON and returns the poll url", async () => {
    server.use(
      http.post(`${BASE}/api/agent/start`, async ({ request }) => {
        const body = (await request.json()) as { dependency: { name: string } };
        expect(request.headers.get("content-type")).toContain("application/json");
        expect(body.dependency.name).toBe("pydantic");
        return HttpResponse.json(
          { thread_id: "t-2", status: "queued", poll_url: "/api/agent/status/t-2" },
          { status: 202 },
        );
      }),
    );

    const response = await startRun({
      repo: { url: null, path: "/srv/repo" },
      dependency: { name: "pydantic", current_version: "1.10.13", target_version: "2.9.2" },
      constraints: { zero_downtime: true, minimize_effort: false, deadline: "2026-09-30", risk_tolerance: "low" },
    });

    expect(response).toEqual({ thread_id: "t-2", status: "queued", poll_url: "/api/agent/status/t-2" });
  });
});
```

- [ ] **Step 4: Run it to verify it fails**

```bash
cd frontend && npm test -- --run src/api/client.test.ts
```

Expected: FAIL — `Failed to resolve import "./client"`.

- [ ] **Step 5: Write the implementation**

```ts
/**
 * The only module in the frontend that calls `fetch`.
 *
 * Components render and hooks schedule; neither talks to the network. That is
 * the frontend's form of CLAUDE.md rule 16, and it is what makes the whole
 * transport testable from one place.
 */

import type {
  ApiError,
  ErrorResponse,
  HealthResponse,
  ResumeRequest,
  RunSnapshot,
  StartResponse,
  StartRunRequest,
} from "./types";

/**
 * A refused request, carrying the server's own error body.
 *
 * The backend declares `ErrorResponse` on every route precisely so the client
 * never has to guess (`api/routes/agent.py:50`). Keeping `httpStatus` beside
 * the body matters for one caller in particular: the decision panel branches
 * on 409, which is the only real guarantee against a duplicate resume.
 */
export class ApiFailure extends Error {
  constructor(
    readonly httpStatus: number,
    readonly error: ApiError,
  ) {
    super(error.message);
    this.name = "ApiFailure";
  }
}

/**
 * The fallback when a non-2xx body is not the declared shape — a proxy
 * answering with HTML, say. Without it the client throws a JSON parse error,
 * which tells the user nothing about what happened.
 */
function unreadable(httpStatus: number): ApiError {
  return {
    code: "internal",
    message: "The server returned a response this client could not read.",
    retryable: true,
    node: null,
  };
}

async function request<T>(path: string, init: RequestInit, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, { ...init, signal });

  if (!response.ok) {
    let body: ErrorResponse | null = null;
    try {
      body = (await response.json()) as ErrorResponse;
    } catch {
      body = null;
    }
    // Not `except: pass` — the caught parse failure becomes a typed error the
    // caller must handle, which is CLAUDE.md rule 20 in its frontend form.
    throw new ApiFailure(response.status, body?.error ?? unreadable(response.status));
  }

  return (await response.json()) as T;
}

const json = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "content-type": "application/json" },
  body: JSON.stringify(body),
});

export function getStatus(threadId: string, signal?: AbortSignal): Promise<RunSnapshot> {
  // Encoded, not concatenated. The id is a uuid today; a path built by
  // concatenation is a request-forgery shape waiting for the day it is not.
  return request<RunSnapshot>(`/api/agent/status/${encodeURIComponent(threadId)}`, {}, signal);
}

export function startRun(body: StartRunRequest, signal?: AbortSignal): Promise<StartResponse> {
  return request<StartResponse>("/api/agent/start", json(body), signal);
}

export function resumeRun(body: ResumeRequest, signal?: AbortSignal): Promise<StartResponse> {
  return request<StartResponse>("/api/agent/resume", json(body), signal);
}

export function getHealth(signal?: AbortSignal): Promise<HealthResponse> {
  return request<HealthResponse>("/api/health", {}, signal);
}
```

If `tsc` flags `unreadable`'s unused `httpStatus` parameter under `noUnusedParameters`, rename it to `_httpStatus` — do not remove it, the signature documents what the caller has in hand.

- [ ] **Step 6: Run the test and the typecheck**

```bash
cd frontend && npm test -- --run src/api/client.test.ts && npx tsc -b
```

Expected: 7 passing, `tsc -b` silent.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/api/client.ts frontend/src/api/client.test.ts frontend/src/test/server.ts frontend/src/test/setup.ts
git commit -m "feat(ui): one module that calls fetch, and typed refusals

Components render and hooks schedule; neither touches the network. The backend
declares its error shape on every route so the client never guesses, and
\`ApiFailure\` keeps the status beside the body because one caller branches on
it -- 409 is the only real guarantee against a duplicate resume.

Two failures get explicit handling rather than a stack trace. A non-2xx body
that is not the declared shape (a proxy answering HTML) becomes a readable
\`internal\` error instead of a JSON parse throw. And an abort propagates as an
AbortError rather than an ApiFailure, because the polling hook aborts on
unmount and an error banner on every navigation would be the alternative."
```

---

## Task 6: `useRunPolling` — the only source of run state

Spec §10: 1s interval while non-terminal, stops on terminal status, backs off on network error, aborts on unmount. Because each snapshot is *complete* rather than incremental there is no accumulation and no merge logic — the concrete payoff of polling over SSE (ADR-001:68), and the reason this hook can be the whole of the frontend's state management.

**Files:**
- Create: `frontend/src/hooks/useRunPolling.ts`
- Test: `frontend/src/hooks/useRunPolling.test.ts`

**Interfaces:**
- Consumes: `getStatus`, `ApiFailure` from `api/client`; `RunSnapshot`, `ApiError`, `TERMINAL_STATUSES` from `api/types`.
- Produces:
  - `export type PollState = { snapshot: RunSnapshot | null; error: ApiError | null; reconnecting: boolean }`
  - `export function useRunPolling(threadId: string | null): PollState`
  - `export const POLL_MS = 1000` and `export const BACKOFF_MS: readonly number[]` — exported so the test asserts against the same numbers the implementation uses rather than a copy of them.

- [ ] **Step 1: Write the failing test**

Fake timers plus `advanceTimersByTimeAsync`, which flushes microtasks as it advances — without the `Async` variant the awaited fetch never settles and every test times out.

```ts
import { renderHook, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { aSnapshot } from "../test/fixtures";
import { server } from "../test/server";
import { BACKOFF_MS, POLL_MS, useRunPolling } from "./useRunPolling";

const STATUS = "http://localhost/api/agent/status/t-1";

/** Answer each poll from a queue, so a test can script a sequence. */
function scriptSnapshots(...statuses: string[]) {
  let call = 0;
  const seen = () => call;
  server.use(
    http.get(STATUS, () => {
      const status = statuses[Math.min(call, statuses.length - 1)];
      call += 1;
      return HttpResponse.json(aSnapshot({ status: status as never }));
    }),
  );
  return seen;
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("useRunPolling", () => {
  it("issues no request and holds no snapshot without a thread id", async () => {
    // `onUnhandledRequest: "error"` means a stray fetch fails the test, so the
    // absence of a handler here is the assertion.
    const { result } = renderHook(() => useRunPolling(null));

    await vi.advanceTimersByTimeAsync(5 * POLL_MS);

    expect(result.current.snapshot).toBeNull();
    expect(result.current.error).toBeNull();
  });

  it("fetches immediately rather than waiting out the first interval", async () => {
    // A user who just pressed Start should not watch a blank second.
    scriptSnapshots("running");
    const { result } = renderHook(() => useRunPolling("t-1"));

    await waitFor(() => expect(result.current.snapshot).not.toBeNull());
    expect(result.current.snapshot?.status).toBe("running");
  });

  it("polls once per second while the run is not terminal", async () => {
    const calls = scriptSnapshots("running");
    renderHook(() => useRunPolling("t-1"));

    await waitFor(() => expect(calls()).toBe(1));
    await vi.advanceTimersByTimeAsync(POLL_MS);
    expect(calls()).toBe(2);
    await vi.advanceTimersByTimeAsync(POLL_MS);
    expect(calls()).toBe(3);
  });

  it("keeps polling while a decision is outstanding", async () => {
    // `awaiting_human` is deliberately not terminal: a resume can arrive from
    // another client, and the transition out of the decision panel is exactly
    // what the user is watching for.
    const calls = scriptSnapshots("awaiting_human");
    renderHook(() => useRunPolling("t-1"));

    await waitFor(() => expect(calls()).toBe(1));
    await vi.advanceTimersByTimeAsync(3 * POLL_MS);
    expect(calls()).toBe(4);
  });

  it("stops when the run completes", async () => {
    const calls = scriptSnapshots("running", "completed");
    const { result } = renderHook(() => useRunPolling("t-1"));

    await waitFor(() => expect(calls()).toBe(1));
    await vi.advanceTimersByTimeAsync(POLL_MS);
    await waitFor(() => expect(result.current.snapshot?.status).toBe("completed"));

    await vi.advanceTimersByTimeAsync(10 * POLL_MS);
    expect(calls()).toBe(2);
  });

  it("stops on an orphaned run, whose process is gone", async () => {
    const calls = scriptSnapshots("orphaned");
    renderHook(() => useRunPolling("t-1"));

    await waitFor(() => expect(calls()).toBe(1));
    await vi.advanceTimersByTimeAsync(10 * POLL_MS);
    expect(calls()).toBe(1);
  });

  it("stops on a failed run", async () => {
    const calls = scriptSnapshots("failed");
    renderHook(() => useRunPolling("t-1"));

    await waitFor(() => expect(calls()).toBe(1));
    await vi.advanceTimersByTimeAsync(10 * POLL_MS);
    expect(calls()).toBe(1);
  });

  it("backs off on a network error and marks itself reconnecting", async () => {
    let calls = 0;
    server.use(
      http.get(STATUS, () => {
        calls += 1;
        return HttpResponse.error();
      }),
    );
    const { result } = renderHook(() => useRunPolling("t-1"));

    await waitFor(() => expect(result.current.reconnecting).toBe(true));
    expect(calls).toBe(1);

    // Still waiting out the first backoff, which is longer than a poll.
    await vi.advanceTimersByTimeAsync(BACKOFF_MS[0] - 1);
    expect(calls).toBe(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(calls).toBe(2);

    // Second failure waits longer than the first.
    await vi.advanceTimersByTimeAsync(BACKOFF_MS[1] - 1);
    expect(calls).toBe(2);
    await vi.advanceTimersByTimeAsync(1);
    expect(calls).toBe(3);
  });

  it("resets the backoff and clears reconnecting once a poll succeeds", async () => {
    let calls = 0;
    server.use(
      http.get(STATUS, () => {
        calls += 1;
        return calls === 1 ? HttpResponse.error() : HttpResponse.json(aSnapshot({ status: "running" }));
      }),
    );
    const { result } = renderHook(() => useRunPolling("t-1"));

    await waitFor(() => expect(result.current.reconnecting).toBe(true));
    await vi.advanceTimersByTimeAsync(BACKOFF_MS[0]);
    await waitFor(() => expect(result.current.reconnecting).toBe(false));

    // Back to the ordinary cadence, not still backed off.
    await vi.advanceTimersByTimeAsync(POLL_MS);
    expect(calls).toBe(3);
  });

  it("does not raise the backoff past its cap", async () => {
    let calls = 0;
    server.use(
      http.get(STATUS, () => {
        calls += 1;
        return HttpResponse.error();
      }),
    );
    renderHook(() => useRunPolling("t-1"));

    await waitFor(() => expect(calls).toBe(1));
    for (const delay of BACKOFF_MS) {
      await vi.advanceTimersByTimeAsync(delay);
    }
    const beforeCap = calls;
    // Two more waits at the capped delay, not an ever-growing one.
    await vi.advanceTimersByTimeAsync(BACKOFF_MS[BACKOFF_MS.length - 1]);
    await vi.advanceTimersByTimeAsync(BACKOFF_MS[BACKOFF_MS.length - 1]);
    expect(calls).toBe(beforeCap + 2);
  });

  it("stops and reports a refusal rather than retrying it", async () => {
    // A 404 is not a network blip. Retrying a thread that does not exist for
    // ever would hide the one message the user needs.
    let calls = 0;
    server.use(
      http.get(STATUS, () => {
        calls += 1;
        return HttpResponse.json(
          { error: { code: "thread_not_found", message: "No run with that id exists.", retryable: false, node: null } },
          { status: 404 },
        );
      }),
    );
    const { result } = renderHook(() => useRunPolling("t-1"));

    await waitFor(() => expect(result.current.error?.code).toBe("thread_not_found"));
    expect(result.current.reconnecting).toBe(false);

    await vi.advanceTimersByTimeAsync(20 * POLL_MS);
    expect(calls).toBe(1);
  });

  it("issues no further request after unmount", async () => {
    const calls = scriptSnapshots("running");
    const { unmount } = renderHook(() => useRunPolling("t-1"));

    await waitFor(() => expect(calls()).toBe(1));
    unmount();
    await vi.advanceTimersByTimeAsync(20 * POLL_MS);

    expect(calls()).toBe(1);
  });

  it("never has two requests in flight at once", async () => {
    // The next timer is scheduled after the previous request settles, not on a
    // fixed interval. A slow backend would otherwise stack requests until one
    // of them answered.
    let inFlight = 0;
    let overlapped = false;
    server.use(
      http.get(STATUS, async () => {
        inFlight += 1;
        overlapped ||= inFlight > 1;
        await new Promise((resolve) => setTimeout(resolve, 3 * POLL_MS));
        inFlight -= 1;
        return HttpResponse.json(aSnapshot({ status: "running" }));
      }),
    );
    renderHook(() => useRunPolling("t-1"));

    await vi.advanceTimersByTimeAsync(12 * POLL_MS);
    expect(overlapped).toBe(false);
  });

  it("drops the previous run's state when the thread id changes", async () => {
    server.use(
      http.get("http://localhost/api/agent/status/:threadId", ({ params }) =>
        HttpResponse.json(aSnapshot({ thread_id: String(params.threadId), status: "running" })),
      ),
    );
    const { result, rerender } = renderHook(({ id }: { id: string }) => useRunPolling(id), {
      initialProps: { id: "t-1" },
    });

    await waitFor(() => expect(result.current.snapshot?.thread_id).toBe("t-1"));
    rerender({ id: "t-2" });
    await waitFor(() => expect(result.current.snapshot?.thread_id).toBe("t-2"));
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd frontend && npm test -- --run src/hooks/useRunPolling.test.ts
```

Expected: FAIL — `Failed to resolve import "./useRunPolling"`.

- [ ] **Step 3: Write the implementation**

```ts
/**
 * The only source of run state.
 *
 * One hook, one in-flight request, one complete snapshot per tick. Because a
 * `RunSnapshot` describes the whole run rather than a delta, nothing here
 * accumulates and nothing downstream merges — which is the concrete payoff
 * ADR-001:68 banked when it chose polling over SSE, and the reason this hook
 * can be the whole of the frontend's state management.
 *
 * Two distinctions do the real work:
 *
 *   - **A refusal is not a network blip.** An `ApiFailure` (404 on an unknown
 *     thread, say) stops the loop and reports itself. Retrying it for ever
 *     would bury the one message the user needs behind a spinner.
 *   - **The next tick is scheduled after the last one settles**, not on a
 *     fixed interval. A slow backend would otherwise stack requests until one
 *     of them answered.
 */

import { useEffect, useState } from "react";

import { ApiFailure, getStatus } from "../api/client";
import type { ApiError, RunSnapshot } from "../api/types";
import { TERMINAL_STATUSES } from "../api/types";

export const POLL_MS = 1000;

/**
 * Exported so the test asserts against these numbers rather than a copy of
 * them — a copy is a second place to change when the cadence changes.
 */
export const BACKOFF_MS: readonly number[] = [1000, 2000, 4000, 8000, 15000];

export type PollState = {
  snapshot: RunSnapshot | null;
  error: ApiError | null;
  reconnecting: boolean;
};

const INITIAL: PollState = { snapshot: null, error: null, reconnecting: false };

export function useRunPolling(threadId: string | null): PollState {
  const [state, setState] = useState<PollState>(INITIAL);

  useEffect(() => {
    if (threadId === null) {
      setState(INITIAL);
      return;
    }

    // A new thread starts from nothing, so the previous run's report cannot
    // linger on screen for the first second of this one.
    setState(INITIAL);

    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let stopped = false;
    let failures = 0;

    function schedule(ms: number): void {
      timer = setTimeout(() => {
        void tick();
      }, ms);
    }

    async function tick(): Promise<void> {
      try {
        const snapshot = await getStatus(threadId, controller.signal);
        if (stopped) return;

        failures = 0;
        setState({ snapshot, error: null, reconnecting: false });

        if (TERMINAL_STATUSES.has(snapshot.status)) return;
        schedule(POLL_MS);
      } catch (error) {
        if (stopped || controller.signal.aborted) return;

        if (error instanceof ApiFailure) {
          // The server answered, and its answer was no. Stop.
          setState((previous) => ({ ...previous, error: error.error, reconnecting: false }));
          return;
        }

        failures += 1;
        setState((previous) => ({ ...previous, reconnecting: true }));
        schedule(BACKOFF_MS[Math.min(failures - 1, BACKOFF_MS.length - 1)]);
      }
    }

    void tick();

    return () => {
      stopped = true;
      controller.abort();
      if (timer !== undefined) clearTimeout(timer);
    };
  }, [threadId]);

  return state;
}
```

`stopped` and `controller.abort()` are both needed, and neither is redundant. The abort cancels a request already on the wire; `stopped` stops a response that had already resolved from writing state into an unmounted component. Under React 19 `StrictMode` the effect runs twice in development, and without both the second run would race the first.

- [ ] **Step 4: Run the test and the typecheck**

```bash
cd frontend && npm test -- --run src/hooks/useRunPolling.test.ts && npx tsc -b
```

Expected: 14 passing, `tsc -b` silent.

If the overlap test hangs, the MSW handler's `setTimeout` is being consumed by the fake clock — that is correct and intended; `advanceTimersByTimeAsync` drives both. If it still hangs, confirm `vi.useFakeTimers()` is in `beforeEach` and not module scope.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/hooks/useRunPolling.ts frontend/src/hooks/useRunPolling.test.ts
git commit -m "feat(ui): one polling hook, and the two distinctions that matter

Spec 10: 1s while non-terminal, stop on terminal, back off on network error,
abort on unmount. Each snapshot is complete, so nothing accumulates and nothing
merges -- the payoff ADR-001:68 banked when it chose polling over SSE, and the
reason this hook is the whole of the frontend's state management.

A refusal is not a network blip: an ApiFailure stops the loop and reports
itself, because retrying an unknown thread for ever buries the one message the
user needs behind a spinner. And the next tick is scheduled after the last one
settles rather than on a fixed interval, so a slow backend cannot stack
requests until one of them answers.

\`awaiting_human\` is deliberately not terminal -- a resume can arrive from
another client, and that transition is what the user is watching for.
\`orphaned\` is terminal: its process is gone, so no amount of waiting moves
it. Fourteen tests, including no-overlap and no-request-after-unmount."
```

---

## Task 7: Tokens, primitives, and the timeline

**Files:**
- Modify: `frontend/src/index.css`
- Create: `frontend/src/components/ui.tsx`
- Create: `frontend/src/components/WorkflowTimeline.tsx`
- Test: `frontend/src/components/WorkflowTimeline.test.tsx`

**Interfaces:**
- Consumes: `STEPS`, `stepStates`, `Step`, `StepState` from `derive/steps`; `RiskLevel` from `api/types`.
- Produces:
  - From `ui.tsx`: `Card`, `Panel`, `LevelBadge`, `Mono`, `EmptyState`, `Field`.
    - `Panel({ title, action, children }: { title: string; action?: ReactNode; children: ReactNode })`
    - `LevelBadge({ level, children }: { level: RiskLevel | "pending"; children?: ReactNode })`
    - `Field({ label, value }: { label: string; value: ReactNode })`
    - `EmptyState({ children }: { children: ReactNode })`
    - `Mono({ children }: { children: ReactNode })`
  - From `WorkflowTimeline.tsx`: `WorkflowTimeline({ snapshot }: { snapshot: RunSnapshot | null })`.

- [ ] **Step 1: Write the token scale**

Replace `frontend/src/index.css`. Single dark theme: `docs/ui/DESIGN.md` commits to a dark developer-tool interface, so there is no second palette to keep correct. The four status tokens are spec §10's, and `pending-input` is separate from `risk-medium` on purpose — "we need your answer" and "this is moderately risky" are different messages and must not share a color.

```css
@import "tailwindcss";

:root {
  color-scheme: dark;
}

@theme {
  /* Spec §10's four semantic tokens. Never a raw color at a call site. */
  --color-risk-high: oklch(0.64 0.20 25);
  --color-risk-medium: oklch(0.79 0.16 75);
  --color-risk-low: oklch(0.73 0.15 155);
  --color-pending-input: oklch(0.82 0.16 92);

  /* Surfaces, sunken to raised. */
  --color-surface: oklch(0.17 0.012 260);
  --color-surface-raised: oklch(0.21 0.013 260);
  --color-surface-sunken: oklch(0.13 0.010 260);

  --color-edge: oklch(0.30 0.012 260);
  --color-edge-strong: oklch(0.43 0.014 260);

  --color-ink: oklch(0.95 0.005 260);
  --color-ink-muted: oklch(0.71 0.008 260);
  --color-ink-faint: oklch(0.57 0.008 260);

  --font-mono: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
}

body {
  background: var(--color-surface);
  color: var(--color-ink);
}

/* Status is never communicated by colour alone (DESIGN.md §Accessibility), so
   focus is never communicated by colour alone either. */
:focus-visible {
  outline: 2px solid var(--color-edge-strong);
  outline-offset: 2px;
}
```

- [ ] **Step 2: Write the shared primitives**

No domain knowledge in this file — it is the reason the report tabs stay short. `LevelBadge` pairs a color with a word, which is `DESIGN.md`'s rule that status is never colour alone.

```tsx
/**
 * Shared primitives. No domain knowledge lives here — that is what keeps the
 * report tabs short enough to read.
 */

import type { ReactNode } from "react";

import type { RiskLevel } from "../api/types";

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <div className={`rounded-lg border border-edge bg-surface-raised ${className}`}>{children}</div>
  );
}

export function Panel({
  title,
  action,
  children,
}: {
  title: string;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <Card>
      <div className="flex items-center justify-between border-b border-edge px-4 py-2.5">
        <h2 className="text-xs font-semibold tracking-wide text-ink-muted uppercase">{title}</h2>
        {action}
      </div>
      <div className="p-4">{children}</div>
    </Card>
  );
}

const LEVEL_CLASS: Record<RiskLevel | "pending", string> = {
  low: "border-risk-low/40 bg-risk-low/10 text-risk-low",
  medium: "border-risk-medium/40 bg-risk-medium/10 text-risk-medium",
  high: "border-risk-high/40 bg-risk-high/10 text-risk-high",
  pending: "border-pending-input/40 bg-pending-input/10 text-pending-input",
};

/**
 * A level, as a colour *and* a word. `DESIGN.md` §Accessibility: status is
 * never communicated by colour alone, so the word is not optional decoration.
 */
export function LevelBadge({
  level,
  children,
}: {
  level: RiskLevel | "pending";
  children?: ReactNode;
}) {
  return (
    <span
      className={`inline-flex items-center rounded border px-1.5 py-0.5 text-[11px] font-semibold uppercase ${LEVEL_CLASS[level]}`}
    >
      {children ?? level}
    </span>
  );
}

export function Mono({ children }: { children: ReactNode }) {
  return <span className="font-mono text-[13px] text-ink-muted">{children}</span>;
}

export function Field({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div>
      <dt className="text-[11px] tracking-wide text-ink-faint uppercase">{label}</dt>
      <dd className="mt-0.5 text-sm text-ink">{value}</dd>
    </div>
  );
}

export function EmptyState({ children }: { children: ReactNode }) {
  return <p className="text-sm text-ink-faint">{children}</p>;
}
```

- [ ] **Step 3: Write the failing timeline test**

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { aSnapshot } from "../test/fixtures";
import { WorkflowTimeline } from "./WorkflowTimeline";

describe("WorkflowTimeline", () => {
  it("shows all eight steps by their user-facing labels", () => {
    render(<WorkflowTimeline snapshot={null} />);

    for (const label of [
      "Repository Analysis",
      "Dependency Analysis",
      "Evidence Retrieval",
      "Risk Assessment",
      "Human Review",
      "Migration Plan",
      "Validation",
      "Report",
    ]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
  });

  it("names each step's state in text, not only in colour", () => {
    // DESIGN.md §Accessibility. A screen reader and a colour-blind user both
    // need the word.
    render(
      <WorkflowTimeline
        snapshot={aSnapshot({
          status: "running",
          completed_steps: ["analyze_repo"],
          current_step: "inspect_dependency",
        })}
      />,
    );

    expect(screen.getByRole("listitem", { name: /Repository Analysis: completed/i })).toBeInTheDocument();
    expect(screen.getByRole("listitem", { name: /Dependency Analysis: running/i })).toBeInTheDocument();
    expect(screen.getByRole("listitem", { name: /Validation: pending/i })).toBeInTheDocument();
  });

  it("shows human review as waiting and later steps as still incomplete", () => {
    // The guarantee: the workflow can never look finished while it waits.
    render(
      <WorkflowTimeline
        snapshot={aSnapshot({
          status: "awaiting_human",
          completed_steps: ["analyze_repo", "inspect_dependency", "agentic_rag", "assess_risk"],
          current_step: "human_review",
        })}
      />,
    );

    expect(screen.getByRole("listitem", { name: /Human Review: waiting for you/i })).toBeInTheDocument();
    expect(screen.getByRole("listitem", { name: /Migration Plan: pending/i })).toBeInTheDocument();
    expect(screen.getByRole("listitem", { name: /Report: pending/i })).toBeInTheDocument();
  });

  it("says why a skipped step was skipped", () => {
    // Spec 8.2. Without the reason, "skipped" reads as an omission rather than
    // a decision the constraints already made.
    render(
      <WorkflowTimeline
        snapshot={aSnapshot({
          status: "completed",
          completed_steps: [
            "analyze_repo",
            "inspect_dependency",
            "agentic_rag",
            "assess_risk",
            "generate_plan",
            "validate_plan",
            "finalize",
          ],
          current_step: null,
        })}
      />,
    );

    expect(
      screen.getByRole("listitem", { name: /Human Review: skipped, resolved by constraints/i }),
    ).toBeInTheDocument();
  });

  it("marks the step a failed run stopped on", () => {
    render(
      <WorkflowTimeline
        snapshot={aSnapshot({
          status: "failed",
          completed_steps: ["analyze_repo"],
          current_step: "inspect_dependency",
        })}
      />,
    );

    expect(screen.getByRole("listitem", { name: /Dependency Analysis: failed/i })).toBeInTheDocument();
  });
});
```

- [ ] **Step 4: Run it to verify it fails**

```bash
cd frontend && npm test -- --run src/components/WorkflowTimeline.test.tsx
```

Expected: FAIL — `Failed to resolve import "./WorkflowTimeline"`.

- [ ] **Step 5: Write the implementation**

```tsx
/**
 * The eight steps, always all of them, always in order.
 *
 * A sibling of the workspace view rather than a child of any one of them, so
 * `HumanReviewPanel` renders *above a still-incomplete timeline*. That is what
 * makes "the workflow can never look finished while waiting" structural rather
 * than a thing each view has to remember.
 */

import { AlertTriangle, Check, Circle, Loader, MinusCircle, UserCheck } from "lucide-react";
import type { ReactNode } from "react";

import type { RunSnapshot } from "../api/types";
import { stepStates } from "../derive/steps";
import type { StepState } from "../derive/steps";

/**
 * The word for each state, and its icon.
 *
 * The word is not decoration: `DESIGN.md` §Accessibility requires that status
 * never be communicated by colour alone, and it is what the accessible name of
 * each row is built from.
 */
const APPEARANCE: Record<StepState, { word: string; icon: ReactNode; className: string }> = {
  pending: {
    word: "pending",
    icon: <Circle className="size-3.5" aria-hidden />,
    className: "text-ink-faint",
  },
  running: {
    word: "running",
    icon: <Loader className="size-3.5 animate-spin" aria-hidden />,
    className: "text-ink",
  },
  completed: {
    word: "completed",
    icon: <Check className="size-3.5" aria-hidden />,
    className: "text-risk-low",
  },
  skipped: {
    // The reason travels with the word. Without it "skipped" reads as an
    // omission rather than a decision the constraints already made (spec 8.2).
    word: "skipped, resolved by constraints",
    icon: <MinusCircle className="size-3.5" aria-hidden />,
    className: "text-ink-faint",
  },
  awaiting: {
    word: "waiting for you",
    icon: <UserCheck className="size-3.5" aria-hidden />,
    className: "text-pending-input",
  },
  failed: {
    word: "failed",
    icon: <AlertTriangle className="size-3.5" aria-hidden />,
    className: "text-risk-high",
  },
};

export function WorkflowTimeline({ snapshot }: { snapshot: RunSnapshot | null }) {
  const steps = stepStates(snapshot);

  return (
    <ol className="flex flex-wrap items-stretch gap-1.5" aria-label="Workflow progress">
      {steps.map((step) => {
        const { word, icon, className } = APPEARANCE[step.state];
        return (
          <li
            key={step.node}
            aria-label={`${step.label}: ${word}`}
            className={`flex min-w-0 flex-1 basis-40 items-center gap-2 rounded-md border border-edge bg-surface-raised px-2.5 py-2 ${className}`}
          >
            {icon}
            <span className="min-w-0">
              <span className="block truncate text-xs font-medium text-ink">{step.label}</span>
              <span className="block truncate text-[11px]">{word}</span>
            </span>
          </li>
        );
      })}
    </ol>
  );
}
```

- [ ] **Step 6: Run the test and the typecheck**

```bash
cd frontend && npm test -- --run src/components/WorkflowTimeline.test.tsx && npx tsc -b
```

Expected: 5 passing, `tsc -b` silent.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/index.css frontend/src/components/ui.tsx \
        frontend/src/components/WorkflowTimeline.tsx frontend/src/components/WorkflowTimeline.test.tsx
git commit -m "feat(ui): the token scale, shared primitives, and the eight-step timeline

Single dark theme, because DESIGN.md commits to one and a second palette is a
second thing to keep correct. \`pending-input\` stays a separate token from
\`risk-medium\`: "we need your answer" and "this is moderately risky" are
different messages, and an earlier draft of the design folded both into one
Warning colour, which is the collision these tokens exist to prevent.

Every step names its state in words as well as colour, and the accessible name
of each row is built from that word. The skipped state carries its reason --
"resolved by constraints" -- because \`skipped\` alone reads as an omission
rather than a decision spec 8.2 already made."
```

---

## Task 8: The shell — three regions, a status pill that announces itself, and the session run list

**Files:**
- Create: `frontend/src/hooks/useHealth.ts`
- Create: `frontend/src/hooks/useSessionRuns.ts`
- Create: `frontend/src/components/TopBar.tsx`
- Create: `frontend/src/components/LeftSidebar.tsx`
- Create: `frontend/src/components/AppShell.tsx`
- Test: `frontend/src/components/TopBar.test.tsx`
- Test: `frontend/src/hooks/useSessionRuns.test.ts`
- Modify: `frontend/src/App.tsx`

**Interfaces:**
- Consumes: `useRunPolling`, `viewFor`, `WorkflowTimeline`, `Panel`/`Field`/`EmptyState` from `ui`.
- Produces:
  - `useHealth(): { health: HealthResponse | null; error: ApiError | null }`
  - `useSessionRuns(): { runs: SessionRun[]; remember(run: SessionRun): void }` where `export type SessionRun = { threadId: string; dependency: string; from: string; to: string }`
  - `TopBar({ status, reconnecting, summary, onOpenTrace }: { status: ViewStatus; reconnecting: boolean; summary: SessionRun | null; onOpenTrace: () => void })`
  - `LeftSidebar({ runs, current, onNewRun, onSelectRun, summary }: {...})`
  - `AppShell({ topBar, sidebar, metrics, drawer, children }: {...})`

- [ ] **Step 1: Write the failing `useSessionRuns` test**

```ts
import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { useSessionRuns } from "./useSessionRuns";

const A = { threadId: "t-1", dependency: "pydantic", from: "1.10.13", to: "2.9.2" };
const B = { threadId: "t-2", dependency: "pydantic", from: "1.9.0", to: "2.9.2" };

beforeEach(() => {
  sessionStorage.clear();
});

describe("useSessionRuns", () => {
  it("starts empty", () => {
    expect(renderHook(() => useSessionRuns()).result.current.runs).toEqual([]);
  });

  it("remembers a run, newest first", () => {
    const { result } = renderHook(() => useSessionRuns());

    act(() => result.current.remember(A));
    act(() => result.current.remember(B));

    expect(result.current.runs.map((run) => run.threadId)).toEqual(["t-2", "t-1"]);
  });

  it("does not list the same thread twice", () => {
    const { result } = renderHook(() => useSessionRuns());

    act(() => result.current.remember(A));
    act(() => result.current.remember(A));

    expect(result.current.runs).toHaveLength(1);
  });

  it("survives a remount, because it is in sessionStorage", () => {
    const first = renderHook(() => useSessionRuns());
    act(() => first.result.current.remember(A));
    first.unmount();

    expect(renderHook(() => useSessionRuns()).result.current.runs[0].threadId).toBe("t-1");
  });

  it("ignores a corrupt stored value rather than throwing on load", () => {
    // sessionStorage is user-writable and survives a deploy that changes this
    // shape. A crash on read would make the whole app unreachable until the
    // user knew to clear site data.
    sessionStorage.setItem("upgradepilot.runs", "{not json");

    expect(renderHook(() => useSessionRuns()).result.current.runs).toEqual([]);
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd frontend && npm test -- --run src/hooks/useSessionRuns.test.ts
```

Expected: FAIL — `Failed to resolve import "./useSessionRuns"`.

- [ ] **Step 3: Write `useSessionRuns`**

Listing *past* runs needs the Postgres run registry, which is sub-project 3 (rule 3). What is honest today is the runs this tab started, labelled as such — and it works because `/api/agent/status/{thread_id}` answers for any thread the SQLite checkpointer still holds.

```ts
/**
 * The runs this browser tab started. Not a history feature.
 *
 * Listing past runs needs the Postgres run registry, which is sub-project 3.
 * What is honest today is what this tab did, held in `sessionStorage` and
 * labelled "this session" in the sidebar — and it is genuinely useful, because
 * `/api/agent/status/{thread_id}` still answers for any thread the SQLite
 * checkpointer holds, so clicking one reopens its report.
 */

import { useCallback, useState } from "react";

const KEY = "upgradepilot.runs";

export type SessionRun = {
  threadId: string;
  dependency: string;
  from: string;
  to: string;
};

function load(): SessionRun[] {
  try {
    const raw = sessionStorage.getItem(KEY);
    const parsed: unknown = raw === null ? [] : JSON.parse(raw);
    return Array.isArray(parsed) ? (parsed as SessionRun[]) : [];
  } catch {
    // `sessionStorage` is user-writable and outlives a deploy that changes
    // this shape. Throwing here would make the whole application unreachable
    // until the user knew to clear site data — so a bad value is discarded,
    // which is the one case where dropping data is the honest move.
    return [];
  }
}

export function useSessionRuns(): {
  runs: SessionRun[];
  remember: (run: SessionRun) => void;
} {
  const [runs, setRuns] = useState<SessionRun[]>(load);

  const remember = useCallback((run: SessionRun) => {
    setRuns((previous) => {
      const next = [run, ...previous.filter((each) => each.threadId !== run.threadId)];
      try {
        sessionStorage.setItem(KEY, JSON.stringify(next));
      } catch {
        // A full or disabled store must not stop the run the user just began.
      }
      return next;
    });
  }, []);

  return { runs, remember };
}
```

- [ ] **Step 4: Write the failing `TopBar` test**

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { TopBar } from "./TopBar";

const summary = { threadId: "t-1", dependency: "pydantic", from: "1.10.13", to: "2.9.2" };

describe("TopBar", () => {
  it("announces the status in a live region", () => {
    // Spec 10: the transition into Human Review must be announced, not merely
    // rendered. `polite` rather than `assertive` so it does not interrupt a
    // user mid-sentence.
    const { container } = render(
      <TopBar status="awaiting_human" reconnecting={false} summary={summary} onOpenTrace={() => {}} />,
    );

    const live = container.querySelector("[aria-live]");
    expect(live).toHaveAttribute("aria-live", "polite");
    expect(live).toHaveTextContent(/waiting for your decision/i);
  });

  it("words each status for a person rather than echoing the enum", () => {
    const { rerender } = render(
      <TopBar status="idle" reconnecting={false} summary={null} onOpenTrace={() => {}} />,
    );
    expect(screen.getByText(/no run started/i)).toBeInTheDocument();

    rerender(<TopBar status="queued" reconnecting={false} summary={summary} onOpenTrace={() => {}} />);
    expect(screen.getByText(/queued/i)).toBeInTheDocument();

    rerender(
      <TopBar status="completed_with_warnings" reconnecting={false} summary={summary} onOpenTrace={() => {}} />,
    );
    expect(screen.getByText(/completed with warnings/i)).toBeInTheDocument();

    rerender(<TopBar status="orphaned" reconnecting={false} summary={summary} onOpenTrace={() => {}} />);
    expect(screen.getByText(/interrupted by a restart/i)).toBeInTheDocument();
  });

  it("says the activity is polled, not streamed", () => {
    // ADR-001 A3 defers SSE. A "streaming" label would describe a transport
    // this system does not have.
    render(<TopBar status="running" reconnecting={false} summary={summary} onOpenTrace={() => {}} />);

    expect(screen.getByText(/1s poll/i)).toBeInTheDocument();
    expect(screen.queryByText(/streaming/i)).not.toBeInTheDocument();
  });

  it("shows a reconnecting notice without discarding the last known status", () => {
    render(<TopBar status="running" reconnecting summary={summary} onOpenTrace={() => {}} />);

    expect(screen.getByText(/reconnecting/i)).toBeInTheDocument();
    expect(screen.getByText(/running/i)).toBeInTheDocument();
  });

  it("names the run being worked on", () => {
    render(<TopBar status="running" reconnecting={false} summary={summary} onOpenTrace={() => {}} />);

    expect(screen.getByText("pydantic")).toBeInTheDocument();
    expect(screen.getByText(/1\.10\.13/)).toBeInTheDocument();
    expect(screen.getByText(/2\.9\.2/)).toBeInTheDocument();
  });

  it("offers the agent trace", () => {
    render(<TopBar status="running" reconnecting={false} summary={summary} onOpenTrace={() => {}} />);

    expect(screen.getByRole("button", { name: /agent trace/i })).toBeInTheDocument();
  });
});
```

- [ ] **Step 5: Run it to verify it fails**

```bash
cd frontend && npm test -- --run src/components/TopBar.test.tsx
```

Expected: FAIL — `Failed to resolve import "./TopBar"`.

- [ ] **Step 6: Write `TopBar`**

```tsx
/**
 * The top bar: what run this is, what it is doing, and the trace trigger.
 *
 * The status pill is the application's `aria-live` region (spec §10), because
 * the transition into Human Review is the one state change a user must not
 * miss and the only one that is otherwise announced by nothing.
 */

import { ScrollText, Wifi, WifiOff } from "lucide-react";

import type { ViewStatus } from "../api/types";
import type { SessionRun } from "../hooks/useSessionRuns";

/**
 * A sentence per status, not the enum echoed back.
 *
 * `orphaned` gets the longest one because it is the status a user has no
 * intuition for: their run's process is gone, the work it did survives, and
 * the thing to do is resume it.
 */
const WORDING: Record<ViewStatus, { text: string; className: string }> = {
  idle: { text: "No run started", className: "text-ink-faint border-edge" },
  queued: { text: "Queued", className: "text-ink-muted border-edge" },
  running: { text: "Running", className: "text-ink border-edge-strong" },
  awaiting_human: {
    text: "Waiting for your decision",
    className: "text-pending-input border-pending-input/50 bg-pending-input/10",
  },
  completed: { text: "Completed", className: "text-risk-low border-risk-low/50" },
  completed_with_warnings: {
    text: "Completed with warnings",
    className: "text-risk-medium border-risk-medium/50",
  },
  failed: { text: "Failed", className: "text-risk-high border-risk-high/50" },
  orphaned: {
    text: "Interrupted by a restart",
    className: "text-risk-medium border-risk-medium/50",
  },
};

const LIVE = new Set<ViewStatus>(["queued", "running", "awaiting_human"]);

export function TopBar({
  status,
  reconnecting,
  summary,
  onOpenTrace,
}: {
  status: ViewStatus;
  reconnecting: boolean;
  summary: SessionRun | null;
  onOpenTrace: () => void;
}) {
  const wording = WORDING[status];

  return (
    <header className="flex items-center gap-4 border-b border-edge bg-surface-sunken px-4 py-2.5">
      <span className="text-sm font-semibold tracking-tight">UpgradePilot</span>

      {summary !== null && (
        <span className="flex items-baseline gap-2 text-sm">
          <span className="font-medium">{summary.dependency}</span>
          <span className="font-mono text-[13px] text-ink-muted">
            {summary.from} → {summary.to}
          </span>
        </span>
      )}

      <div className="ml-auto flex items-center gap-3">
        {LIVE.has(status) &&
          (reconnecting ? (
            <span className="flex items-center gap-1.5 text-[11px] text-risk-medium">
              <WifiOff className="size-3.5" aria-hidden /> Reconnecting…
            </span>
          ) : (
            // ADR-001 A3 defers SSE. This is the honest label for what the
            // client actually does.
            <span className="flex items-center gap-1.5 text-[11px] text-ink-faint">
              <Wifi className="size-3.5" aria-hidden /> Live · 1s poll
            </span>
          ))}

        <span
          aria-live="polite"
          className={`rounded-full border px-2.5 py-1 text-[11px] font-semibold ${wording.className}`}
        >
          {wording.text}
        </span>

        <button
          type="button"
          onClick={onOpenTrace}
          className="flex items-center gap-1.5 rounded-md border border-edge px-2.5 py-1 text-xs text-ink-muted hover:text-ink"
        >
          <ScrollText className="size-3.5" aria-hidden /> Agent trace
        </button>
      </div>
    </header>
  );
}
```

- [ ] **Step 7: Write `useHealth`, `LeftSidebar` and `AppShell`**

`useHealth` is a one-shot read: `/api/health` reports store reachability and whether a model key is configured, which does not change while the page is open.

```ts
/**
 * One read of `/api/health` for the sidebar's integration status.
 *
 * Not polled: it reports store reachability and whether a model key is
 * configured, neither of which changes while the page is open. Phase 9 found
 * this endpoint reporting on the wrong settings object, so what it says is
 * worth showing rather than assuming.
 */

import { useEffect, useState } from "react";

import { ApiFailure, getHealth } from "../api/client";
import type { ApiError, HealthResponse } from "../api/types";

export function useHealth(): { health: HealthResponse | null; error: ApiError | null } {
  const [state, setState] = useState<{ health: HealthResponse | null; error: ApiError | null }>({
    health: null,
    error: null,
  });

  useEffect(() => {
    const controller = new AbortController();
    getHealth(controller.signal)
      .then((health) => setState({ health, error: null }))
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setState({
          health: null,
          error:
            error instanceof ApiFailure
              ? error.error
              : {
                  code: "internal",
                  message: "The backend is unreachable.",
                  retryable: true,
                  node: null,
                },
        });
      });
    return () => controller.abort();
  }, []);

  return state;
}
```

```tsx
/**
 * Left region: start a run, revisit one this tab started, see what was
 * configured and whether the stores are reachable.
 *
 * Two absences are deliberate. There is no historical run list — that needs
 * the Postgres registry, which is sub-project 3 — and there are no model or
 * temperature controls, because configuration is environment variables via
 * `pydantic-settings` (rule 14) and the API exposes no configuration endpoint.
 * The model actually in use is reported in the telemetry region, from calls
 * that happened.
 */

import { CheckCircle, Database, KeyRound, Plus, ShieldAlert } from "lucide-react";

import type { HealthResponse } from "../api/types";
import type { SessionRun } from "../hooks/useSessionRuns";
import { EmptyState, Field } from "./ui";

function Check({ ok, label }: { ok: boolean; label: string }) {
  return (
    <li className="flex items-center gap-2 text-xs">
      {ok ? (
        <CheckCircle className="size-3.5 text-risk-low" aria-hidden />
      ) : (
        <ShieldAlert className="size-3.5 text-risk-high" aria-hidden />
      )}
      <span className={ok ? "text-ink-muted" : "text-risk-high"}>
        {label}: {ok ? "ready" : "unavailable"}
      </span>
    </li>
  );
}

export function LeftSidebar({
  runs,
  current,
  summary,
  health,
  onNewRun,
  onSelectRun,
}: {
  runs: SessionRun[];
  current: string | null;
  summary: SessionRun | null;
  health: HealthResponse | null;
  onNewRun: () => void;
  onSelectRun: (threadId: string) => void;
}) {
  return (
    <nav className="flex w-64 shrink-0 flex-col gap-5 overflow-y-auto border-r border-edge bg-surface-sunken p-3">
      <button
        type="button"
        onClick={onNewRun}
        className="flex items-center justify-center gap-1.5 rounded-md border border-edge-strong bg-surface-raised px-3 py-2 text-sm font-medium hover:border-ink-faint"
      >
        <Plus className="size-4" aria-hidden /> New migration run
      </button>

      <section>
        <h2 className="mb-2 text-[11px] font-semibold tracking-wide text-ink-faint uppercase">
          This session
        </h2>
        {runs.length === 0 ? (
          <EmptyState>No runs yet in this tab.</EmptyState>
        ) : (
          <ul className="space-y-1">
            {runs.map((run) => (
              <li key={run.threadId}>
                <button
                  type="button"
                  onClick={() => onSelectRun(run.threadId)}
                  aria-current={run.threadId === current ? "true" : undefined}
                  className={`w-full rounded-md px-2 py-1.5 text-left text-xs ${
                    run.threadId === current
                      ? "bg-surface-raised text-ink"
                      : "text-ink-muted hover:bg-surface-raised"
                  }`}
                >
                  <span className="block truncate font-medium">{run.dependency}</span>
                  <span className="block truncate font-mono text-[11px] text-ink-faint">
                    {run.from} → {run.to} · {run.threadId.slice(0, 8)}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      {summary !== null && (
        <section>
          <h2 className="mb-2 text-[11px] font-semibold tracking-wide text-ink-faint uppercase">
            Configuration
          </h2>
          <dl className="space-y-2">
            <Field label="Dependency" value={summary.dependency} />
            <Field
              label="Versions"
              value={
                <span className="font-mono text-[13px]">
                  {summary.from} → {summary.to}
                </span>
              }
            />
            <Field
              label="Thread"
              value={<span className="font-mono text-[12px]">{summary.threadId}</span>}
            />
          </dl>
        </section>
      )}

      <section className="mt-auto">
        <h2 className="mb-2 text-[11px] font-semibold tracking-wide text-ink-faint uppercase">
          Integrations
        </h2>
        {health === null ? (
          <EmptyState>Checking…</EmptyState>
        ) : (
          <ul className="space-y-1.5">
            <Check ok={health.checks.chroma_dir} label="Knowledge base" />
            <Check ok={health.checks.checkpoint_dir} label="Checkpoints" />
            <Check ok={health.checks.llm_configured} label="Model key" />
          </ul>
        )}
      </section>
    </nav>
  );
}
```

```tsx
/**
 * Three regions plus a drawer.
 *
 * `metrics` is a sibling of `children` rather than something a view renders,
 * so it stays mounted and updating while the workspace changes underneath it —
 * token and cost tracking is a graded capability, not a panel one view owns.
 */

import type { ReactNode } from "react";

export function AppShell({
  topBar,
  sidebar,
  metrics,
  drawer,
  children,
}: {
  topBar: ReactNode;
  sidebar: ReactNode;
  metrics: ReactNode;
  drawer: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="flex h-screen flex-col bg-surface text-ink">
      {topBar}
      <div className="flex min-h-0 flex-1">
        {sidebar}
        <main className="min-w-0 flex-1 overflow-y-auto p-5">{children}</main>
        <aside className="hidden w-72 shrink-0 overflow-y-auto border-l border-edge bg-surface-sunken p-3 xl:block">
          {metrics}
        </aside>
      </div>
      {drawer}
    </div>
  );
}
```

- [ ] **Step 8: Wire `App.tsx` to the shell**

This replaces the Phase 0 health-check page. The views are stubs until Tasks 9–13 fill them; the point of this step is that the shell, the derived routing and the timeline are real and visible now.

```tsx
import { useState } from "react";

import type { ViewStatus } from "./api/types";
import { AppShell } from "./components/AppShell";
import { LeftSidebar } from "./components/LeftSidebar";
import { TopBar } from "./components/TopBar";
import { WorkflowTimeline } from "./components/WorkflowTimeline";
import { EmptyState, Panel } from "./components/ui";
import { viewFor } from "./derive/view";
import { useHealth } from "./hooks/useHealth";
import { useRunPolling } from "./hooks/useRunPolling";
import { useSessionRuns } from "./hooks/useSessionRuns";

export default function App() {
  const [threadId, setThreadId] = useState<string | null>(null);
  const { snapshot, error, reconnecting } = useRunPolling(threadId);
  const { health } = useHealth();
  const { runs, remember } = useSessionRuns();

  const status: ViewStatus = threadId === null ? "idle" : (snapshot?.status ?? "queued");
  const view = viewFor(status);
  const summary = runs.find((run) => run.threadId === threadId) ?? null;

  return (
    <AppShell
      topBar={
        <TopBar
          status={status}
          reconnecting={reconnecting}
          summary={summary}
          onOpenTrace={() => undefined}
        />
      }
      sidebar={
        <LeftSidebar
          runs={runs}
          current={threadId}
          summary={summary}
          health={health}
          onNewRun={() => setThreadId(null)}
          onSelectRun={setThreadId}
        />
      }
      metrics={<Panel title="Telemetry">{<EmptyState>Task 10.</EmptyState>}</Panel>}
      drawer={null}
    >
      <div className="space-y-5">
        {threadId !== null && <WorkflowTimeline snapshot={snapshot} />}
        {error !== null && (
          <p className="rounded-md border border-risk-high/50 bg-risk-high/10 px-3 py-2 text-sm text-risk-high">
            {error.message}
          </p>
        )}
        <Panel title={view}>
          <EmptyState>This view arrives in a later task.</EmptyState>
        </Panel>
      </div>
    </AppShell>
  );
}
```

Note the `remember` binding is unused until Task 9 submits a form. If `noUnusedLocals` complains, destructure only `runs` here and add `remember` in Task 9 — do not disable the check.

- [ ] **Step 9: Run the tests, the typecheck, and look at it**

```bash
cd frontend && npm test -- --run && npx tsc -b
```

Expected: all suites passing (types 3, view 7, steps 10, cost 7, client 7, polling 14, timeline 5, sessionRuns 5, topBar 6 = 64).

Then start both processes and confirm the shell renders with the sidebar reporting real health:

```bash
cd backend && ./.venv/bin/python -m uvicorn upgradepilot.api.app:create_app --factory --port 8000 &
cd frontend && npm run dev
```

Open http://localhost:5173. Expected: three regions, "No run started" in the pill, and the Integrations list reporting the three real checks from `/api/health`. Record what it says — if the model key reads unavailable, that is the honest report of an unset `LLM_API_KEY`, not a bug.

- [ ] **Step 10: Commit**

```bash
git add frontend/src/hooks/useHealth.ts frontend/src/hooks/useSessionRuns.ts \
        frontend/src/hooks/useSessionRuns.test.ts frontend/src/components/TopBar.tsx \
        frontend/src/components/TopBar.test.tsx frontend/src/components/LeftSidebar.tsx \
        frontend/src/components/AppShell.tsx frontend/src/App.tsx
git commit -m "feat(ui): three regions, and a status pill that announces itself

The pill is the application's aria-live region, because the transition into
Human Review is the one state change a user must not miss and nothing else
announces it. Each status gets a sentence rather than the enum echoed back --
\`orphaned\` gets the longest, since a user has no intuition for "your run's
process is gone, its work survives, resume it".

The label says "Live - 1s poll", never streaming: ADR-001 A3 defers SSE and a
streaming label would describe a transport this system does not have.

\`metrics\` is a sibling of the workspace rather than something a view renders,
so it stays mounted while the view changes underneath it -- token and cost
tracking is a graded capability, not a panel one view happens to own.

The sidebar's run list is this tab's runs in sessionStorage, labelled as such,
because listing past runs needs the Postgres registry from sub-project 3. It is
still useful: /api/agent/status answers for any thread the SQLite checkpointer
holds, so clicking one reopens its report. A corrupt stored value is discarded
rather than thrown on, since sessionStorage outlives a deploy that changes the
shape and a crash on read would make the app unreachable."
```

---

## Task 9: `ConfigurationForm` — exactly `UserConstraints`, and a 422 that lands on the field

Spec §10: no form library. Controlled inputs plus a small validate function mirroring backend rules; the backend stays authoritative and its 422 detail renders inline. The fields are exactly `UserConstraints` — `zero_downtime`, `minimize_effort`, `deadline`, `risk_tolerance`. The two missing ones are not cosmetic: `constraint_pressure` is derived partly from the deadline (spec §8.1), and the `scope_tradeoff` decision kind is unreachable without it.

**Files:**
- Create: `frontend/src/components/ConfigurationForm.tsx`
- Test: `frontend/src/components/ConfigurationForm.test.tsx`
- Modify: `frontend/src/App.tsx`

**Interfaces:**
- Consumes: `startRun`, `ApiFailure` from `api/client`; `StartRunRequest`, `RiskLevel` from `api/types`.
- Produces: `ConfigurationForm({ onStarted }: { onStarted: (run: SessionRun) => void })`, and `export const FIELD_FOR_CODE: Partial<Record<ErrorCode, FormField>>` where `type FormField = "repo" | "dependency" | "versions"`.

- [ ] **Step 1: Write the failing test**

```tsx
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import { server } from "../test/server";
import { ConfigurationForm } from "./ConfigurationForm";

const START = "http://localhost/api/agent/start";

async function fillMinimum(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText(/repository url/i), "https://example.invalid/r.git");
  await user.type(screen.getByLabelText(/dependency/i), "pydantic");
  await user.type(screen.getByLabelText(/current version/i), "1.10.13");
  await user.type(screen.getByLabelText(/target version/i), "2.9.2");
}

describe("ConfigurationForm", () => {
  it("offers the four constraint fields the backend models", () => {
    render(<ConfigurationForm onStarted={() => {}} />);

    expect(screen.getByLabelText(/zero downtime/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/minimize effort/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/deadline/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/risk tolerance/i)).toBeInTheDocument();
  });

  it("offers no field the backend has nowhere to put", () => {
    // READINESS 2.5, 2.10: model and temperature are environment variables and
    // there is no configuration endpoint; "Additional Context" is unbacked
    // prose entering the judgment path.
    render(<ConfigurationForm onStarted={() => {}} />);

    expect(screen.queryByLabelText(/temperature/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/model/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/additional context/i)).not.toBeInTheDocument();
  });

  it("takes the dependency as free text, not a dropdown", () => {
    // READINESS 2.6: `DependencySpec.name` is free text and nothing enumerates
    // a repository's manifest before a run starts.
    render(<ConfigurationForm onStarted={() => {}} />);

    expect(screen.getByLabelText(/dependency/i).tagName).toBe("INPUT");
  });

  it("refuses two version fields that are equal, before asking the server", async () => {
    // `DependencySpec` rejects this with its own validator. Mirroring it here
    // saves a round trip; the backend remains the authority.
    const user = userEvent.setup();
    render(<ConfigurationForm onStarted={() => {}} />);

    await user.type(screen.getByLabelText(/repository url/i), "https://example.invalid/r.git");
    await user.type(screen.getByLabelText(/dependency/i), "pydantic");
    await user.type(screen.getByLabelText(/current version/i), "2.9.2");
    await user.type(screen.getByLabelText(/target version/i), "2.9.2");
    await user.click(screen.getByRole("button", { name: /start/i }));

    expect(screen.getByText(/must differ/i)).toBeInTheDocument();
  });

  it("sends exactly one of url or path", async () => {
    // Spec 9.1: a request naming both is refused rather than resolved by
    // precedence, because quietly preferring one analyses a repository the
    // caller did not name and every citation then points at the wrong tree.
    const user = userEvent.setup();
    let body: { repo: { url: string | null; path: string | null } } | null = null;
    server.use(
      http.post(START, async ({ request }) => {
        body = (await request.json()) as typeof body;
        return HttpResponse.json(
          { thread_id: "t-7", status: "queued", poll_url: "/api/agent/status/t-7" },
          { status: 202 },
        );
      }),
    );
    render(<ConfigurationForm onStarted={() => {}} />);

    await fillMinimum(user);
    await user.click(screen.getByRole("button", { name: /start/i }));

    await waitFor(() => expect(body).not.toBeNull());
    expect(body!.repo.url).toBe("https://example.invalid/r.git");
    expect(body!.repo.path).toBeNull();
  });

  it("sends a local path when the local source is chosen", async () => {
    const user = userEvent.setup();
    let body: { repo: { url: string | null; path: string | null } } | null = null;
    server.use(
      http.post(START, async ({ request }) => {
        body = (await request.json()) as typeof body;
        return HttpResponse.json(
          { thread_id: "t-8", status: "queued", poll_url: "/api/agent/status/t-8" },
          { status: 202 },
        );
      }),
    );
    render(<ConfigurationForm onStarted={() => {}} />);

    await user.click(screen.getByRole("radio", { name: /local/i }));
    await user.type(screen.getByLabelText(/local path/i), "/srv/repo");
    await user.type(screen.getByLabelText(/dependency/i), "pydantic");
    await user.type(screen.getByLabelText(/current version/i), "1.10.13");
    await user.type(screen.getByLabelText(/target version/i), "2.9.2");
    await user.click(screen.getByRole("button", { name: /start/i }));

    await waitFor(() => expect(body).not.toBeNull());
    expect(body!.repo.path).toBe("/srv/repo");
    expect(body!.repo.url).toBeNull();
  });

  it("sends the constraints as the backend models them", async () => {
    const user = userEvent.setup();
    let body: { constraints: Record<string, unknown> } | null = null;
    server.use(
      http.post(START, async ({ request }) => {
        body = (await request.json()) as typeof body;
        return HttpResponse.json(
          { thread_id: "t-9", status: "queued", poll_url: "/api/agent/status/t-9" },
          { status: 202 },
        );
      }),
    );
    render(<ConfigurationForm onStarted={() => {}} />);

    await fillMinimum(user);
    await user.click(screen.getByLabelText(/zero downtime/i));
    await user.type(screen.getByLabelText(/deadline/i), "2026-09-30");
    await user.selectOptions(screen.getByLabelText(/risk tolerance/i), "low");
    await user.click(screen.getByRole("button", { name: /start/i }));

    await waitFor(() => expect(body).not.toBeNull());
    expect(body!.constraints).toEqual({
      zero_downtime: true,
      minimize_effort: false,
      deadline: "2026-09-30",
      risk_tolerance: "low",
    });
  });

  it("sends a null deadline rather than an empty string", async () => {
    // `deadline: date | None`. An empty string is a 422 the user cannot act on.
    const user = userEvent.setup();
    let body: { constraints: { deadline: string | null } } | null = null;
    server.use(
      http.post(START, async ({ request }) => {
        body = (await request.json()) as typeof body;
        return HttpResponse.json(
          { thread_id: "t-10", status: "queued", poll_url: "/api/agent/status/t-10" },
          { status: 202 },
        );
      }),
    );
    render(<ConfigurationForm onStarted={() => {}} />);

    await fillMinimum(user);
    await user.click(screen.getByRole("button", { name: /start/i }));

    await waitFor(() => expect(body).not.toBeNull());
    expect(body!.constraints.deadline).toBeNull();
  });

  it("renders a 422 against the field its code names", async () => {
    const user = userEvent.setup();
    server.use(
      http.post(START, () =>
        HttpResponse.json(
          { error: { code: "invalid_repo_url", message: "Only https and git URLs are accepted.", retryable: false, node: null } },
          { status: 422 },
        ),
      ),
    );
    render(<ConfigurationForm onStarted={() => {}} />);

    await fillMinimum(user);
    await user.click(screen.getByRole("button", { name: /start/i }));

    const field = screen.getByLabelText(/repository url/i);
    await waitFor(() => expect(field).toHaveAccessibleDescription(/only https and git urls/i));
    expect(field).toHaveAttribute("aria-invalid", "true");
  });

  it("falls back to a banner for a code that names no field", async () => {
    const user = userEvent.setup();
    server.use(
      http.post(START, () =>
        HttpResponse.json(
          { error: { code: "kb_unavailable", message: "The knowledge base is unavailable.", retryable: true, node: null } },
          { status: 503 },
        ),
      ),
    );
    render(<ConfigurationForm onStarted={() => {}} />);

    await fillMinimum(user);
    await user.click(screen.getByRole("button", { name: /start/i }));

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(/knowledge base is unavailable/i),
    );
  });

  it("re-enables the button after a refusal so the user can correct and retry", async () => {
    const user = userEvent.setup();
    server.use(
      http.post(START, () =>
        HttpResponse.json(
          { error: { code: "invalid_repo_url", message: "Only https and git URLs are accepted.", retryable: false, node: null } },
          { status: 422 },
        ),
      ),
    );
    render(<ConfigurationForm onStarted={() => {}} />);

    await fillMinimum(user);
    await user.click(screen.getByRole("button", { name: /start/i }));

    await waitFor(() => expect(screen.getByRole("button", { name: /start/i })).toBeEnabled());
  });

  it("hands the started run up with its thread id", async () => {
    const user = userEvent.setup();
    const onStarted = vi.fn();
    server.use(
      http.post(START, () =>
        HttpResponse.json(
          { thread_id: "t-42", status: "queued", poll_url: "/api/agent/status/t-42" },
          { status: 202 },
        ),
      ),
    );
    render(<ConfigurationForm onStarted={onStarted} />);

    await fillMinimum(user);
    await user.click(screen.getByRole("button", { name: /start/i }));

    await waitFor(() =>
      expect(onStarted).toHaveBeenCalledWith({
        threadId: "t-42",
        dependency: "pydantic",
        from: "1.10.13",
        to: "2.9.2",
      }),
    );
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd frontend && npm test -- --run src/components/ConfigurationForm.test.tsx
```

Expected: FAIL — `Failed to resolve import "./ConfigurationForm"`.

- [ ] **Step 3: Write the implementation**

```tsx
/**
 * The `idle` view. Six fields, no form library (spec §10).
 *
 * The client-side checks mirror the backend's own validators to save a round
 * trip, and nothing more: the backend stays authoritative, and its 422 renders
 * against the field its error code names. Duplicating more of the rules here
 * would create a second implementation that can disagree with the first.
 *
 * What is *not* here is the point of READINESS §2. No provider, model or
 * temperature control — configuration is environment variables via
 * `pydantic-settings` (rule 14) and the API exposes no configuration endpoint.
 * No dependency dropdown — nothing enumerates a repository's manifest before a
 * run starts. No "Additional Context" — unbacked prose entering the judgment
 * path. And the four constraint fields are exactly `UserConstraints`: a form
 * without `deadline` silently weakens `constraint_pressure`, which is derived
 * partly from it, and makes the `scope_tradeoff` decision kind unreachable.
 */

import { useState } from "react";
import type { FormEvent } from "react";

import { ApiFailure, startRun } from "../api/client";
import type { ErrorCode, RiskLevel } from "../api/types";
import type { SessionRun } from "../hooks/useSessionRuns";
import { Panel } from "./ui";

type FormField = "repo" | "dependency" | "versions";

/**
 * Which field an error code belongs beside.
 *
 * A code with no entry renders in the banner instead — a `kb_unavailable` is
 * about the system, not about something the user typed, and attaching it to an
 * input would tell them to fix the wrong thing.
 */
export const FIELD_FOR_CODE: Partial<Record<ErrorCode, FormField>> = {
  invalid_repo_url: "repo",
  local_path_forbidden: "repo",
  repo_unavailable: "repo",
  repo_too_large: "repo",
  dependency_not_found: "dependency",
  version_invalid: "versions",
};

const RISK_OPTIONS: RiskLevel[] = ["low", "medium", "high"];

export function ConfigurationForm({ onStarted }: { onStarted: (run: SessionRun) => void }) {
  const [source, setSource] = useState<"remote" | "local">("remote");
  const [url, setUrl] = useState("");
  const [path, setPath] = useState("");
  const [name, setName] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [zeroDowntime, setZeroDowntime] = useState(false);
  const [minimizeEffort, setMinimizeEffort] = useState(false);
  const [deadline, setDeadline] = useState("");
  const [riskTolerance, setRiskTolerance] = useState<RiskLevel>("medium");

  const [submitting, setSubmitting] = useState(false);
  const [fieldError, setFieldError] = useState<{ field: FormField; message: string } | null>(null);
  const [banner, setBanner] = useState<string | null>(null);

  const errorFor = (field: FormField) =>
    fieldError !== null && fieldError.field === field ? fieldError.message : null;

  function localCheck(): { field: FormField; message: string } | null {
    if (source === "remote" && url.trim() === "") {
      return { field: "repo", message: "A repository URL is required." };
    }
    if (source === "local" && path.trim() === "") {
      return { field: "repo", message: "A local path is required." };
    }
    if (name.trim() === "") {
      return { field: "dependency", message: "A dependency name is required." };
    }
    if (from.trim() === "" || to.trim() === "") {
      return { field: "versions", message: "Both versions are required." };
    }
    if (from.trim() === to.trim()) {
      // `DependencySpec` rejects this with its own validator; mirroring it here
      // saves a round trip.
      return { field: "versions", message: "The two versions must differ." };
    }
    return null;
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBanner(null);

    const problem = localCheck();
    if (problem !== null) {
      setFieldError(problem);
      return;
    }
    setFieldError(null);
    setSubmitting(true);

    try {
      const response = await startRun({
        // Exactly one, never both. Spec 9.1 refuses a request naming both
        // rather than resolving it by precedence.
        repo: source === "remote" ? { url: url.trim(), path: null } : { url: null, path: path.trim() },
        dependency: {
          name: name.trim(),
          current_version: from.trim(),
          target_version: to.trim(),
        },
        constraints: {
          zero_downtime: zeroDowntime,
          minimize_effort: minimizeEffort,
          // `date | None`, so an empty input is null rather than "" — an empty
          // string is a 422 the user cannot act on.
          deadline: deadline === "" ? null : deadline,
          risk_tolerance: riskTolerance,
        },
      });
      onStarted({
        threadId: response.thread_id,
        dependency: name.trim(),
        from: from.trim(),
        to: to.trim(),
      });
    } catch (error) {
      if (error instanceof ApiFailure) {
        const field = FIELD_FOR_CODE[error.error.code];
        if (field !== undefined) setFieldError({ field, message: error.error.message });
        else setBanner(error.error.message);
      } else {
        setBanner("The backend is unreachable.");
      }
      // Re-enabled on failure, unlike the decision panel: this request charged
      // nothing and started nothing, so correcting and retrying is the whole
      // point.
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={submit} className="max-w-2xl space-y-4" noValidate>
      <Panel title="Repository">
        <fieldset className="mb-3">
          <legend className="sr-only">Repository source</legend>
          <div className="flex gap-4 text-sm">
            {(["remote", "local"] as const).map((option) => (
              <label key={option} className="flex items-center gap-1.5 capitalize">
                <input
                  type="radio"
                  name="source"
                  value={option}
                  checked={source === option}
                  onChange={() => setSource(option)}
                />
                {option}
              </label>
            ))}
          </div>
        </fieldset>

        {source === "remote" ? (
          <TextField
            id="repo-url"
            label="Repository URL"
            value={url}
            onChange={setUrl}
            placeholder="https://github.com/owner/project.git"
            error={errorFor("repo")}
            mono
          />
        ) : (
          <TextField
            id="repo-path"
            label="Local path"
            value={path}
            onChange={setPath}
            placeholder="/srv/repo"
            error={errorFor("repo")}
            mono
          />
        )}
      </Panel>

      <Panel title="Dependency">
        <div className="space-y-3">
          <TextField
            id="dependency"
            label="Dependency"
            value={name}
            onChange={setName}
            placeholder="pydantic"
            error={errorFor("dependency")}
          />
          <div className="grid grid-cols-2 gap-3">
            <TextField
              id="version-from"
              label="Current version"
              value={from}
              onChange={setFrom}
              placeholder="1.10.13"
              error={errorFor("versions")}
              mono
            />
            <TextField
              id="version-to"
              label="Target version"
              value={to}
              onChange={setTo}
              placeholder="2.9.2"
              mono
            />
          </div>
          <p className="text-[11px] text-ink-faint">
            The version you state is compared against what the manifests declare; the report shows
            both when they disagree.
          </p>
        </div>
      </Panel>

      <Panel title="Constraints">
        <div className="space-y-3">
          <label className="flex items-center gap-2 text-sm" htmlFor="zero-downtime">
            <input
              id="zero-downtime"
              type="checkbox"
              checked={zeroDowntime}
              onChange={(event) => setZeroDowntime(event.target.checked)}
            />
            Zero downtime required
          </label>
          <label className="flex items-center gap-2 text-sm" htmlFor="minimize-effort">
            <input
              id="minimize-effort"
              type="checkbox"
              checked={minimizeEffort}
              onChange={(event) => setMinimizeEffort(event.target.checked)}
            />
            Minimize effort
          </label>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-[11px] tracking-wide text-ink-faint uppercase" htmlFor="deadline">
                Deadline
              </label>
              <input
                id="deadline"
                type="date"
                value={deadline}
                onChange={(event) => setDeadline(event.target.value)}
                className="mt-1 w-full rounded-md border border-edge bg-surface px-2 py-1.5 text-sm"
              />
            </div>
            <div>
              <label className="block text-[11px] tracking-wide text-ink-faint uppercase" htmlFor="risk-tolerance">
                Risk tolerance
              </label>
              <select
                id="risk-tolerance"
                value={riskTolerance}
                onChange={(event) => setRiskTolerance(event.target.value as RiskLevel)}
                className="mt-1 w-full rounded-md border border-edge bg-surface px-2 py-1.5 text-sm capitalize"
              >
                {RISK_OPTIONS.map((level) => (
                  <option key={level} value={level}>
                    {level}
                  </option>
                ))}
              </select>
            </div>
          </div>
        </div>
      </Panel>

      {banner !== null && (
        <p
          role="alert"
          className="rounded-md border border-risk-high/50 bg-risk-high/10 px-3 py-2 text-sm text-risk-high"
        >
          {banner}
        </p>
      )}

      <button
        type="submit"
        disabled={submitting}
        className="rounded-md border border-edge-strong bg-surface-raised px-4 py-2 text-sm font-medium disabled:opacity-50"
      >
        {submitting ? "Starting…" : "Start migration audit"}
      </button>
    </form>
  );
}

function TextField({
  id,
  label,
  value,
  onChange,
  placeholder,
  error,
  mono = false,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  error?: string | null;
  mono?: boolean;
}) {
  const describedBy = error != null ? `${id}-error` : undefined;
  return (
    <div>
      <label className="block text-[11px] tracking-wide text-ink-faint uppercase" htmlFor={id}>
        {label}
      </label>
      <input
        id={id}
        type="text"
        value={value}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
        aria-invalid={error != null ? "true" : undefined}
        aria-describedby={describedBy}
        className={`mt-1 w-full rounded-md border bg-surface px-2 py-1.5 text-sm ${
          mono ? "font-mono text-[13px]" : ""
        } ${error != null ? "border-risk-high" : "border-edge"}`}
      />
      {error != null && (
        <p id={`${id}-error`} className="mt-1 text-xs text-risk-high">
          {error}
        </p>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Wire it into `App.tsx`**

Replace the placeholder `Panel` with the derived view, and use `remember` so a started run reaches the sidebar:

```tsx
        {view === "configuration" && (
          <ConfigurationForm
            onStarted={(run) => {
              remember(run);
              setThreadId(run.threadId);
            }}
          />
        )}
        {view !== "configuration" && (
          <Panel title={view}>
            <EmptyState>This view arrives in a later task.</EmptyState>
          </Panel>
        )}
```

- [ ] **Step 5: Run the tests and the typecheck**

```bash
cd frontend && npm test -- --run && npx tsc -b
```

Expected: the 12 new tests passing, 76 in total, `tsc -b` silent.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/ConfigurationForm.tsx \
        frontend/src/components/ConfigurationForm.test.tsx frontend/src/App.tsx
git commit -m "feat(ui): a configuration form with no field the backend cannot hold

Exactly \`UserConstraints\`, and the two fields the design pack omitted are the
load-bearing ones: \`constraint_pressure\` is derived partly from the deadline
(spec 8.1), so a form without a deadline picker silently weakens a modeled risk
factor, and the scope_tradeoff decision kind is unreachable without it.

Absences with reasons. No provider, model or temperature control -- those are
environment variables and there is no configuration endpoint. No dependency
dropdown -- nothing enumerates a repository's manifest before a run starts. No
"Additional Context" -- unbacked prose entering the judgment path.

Exactly one of url or path is sent, never both: spec 9.1 refuses a request
naming both rather than resolving it by precedence, because quietly preferring
one analyses a repository the caller did not name and every citation then
points at the wrong tree. An empty deadline is null, not "", which would be a
422 the user cannot act on.

A 422 lands beside the field its error code names, via one map; a code that
names no field goes to the banner, because \`kb_unavailable\` is about the
system and attaching it to an input tells the user to fix the wrong thing.
Twelve tests, including one asserting the absent fields stay absent."
```

---

## Task 10: Activity, telemetry, evidence, and the trace drawer

Four surfaces that all read the same snapshot, grouped because none is independently useful: the activity view is thin without telemetry beside it, and the trace drawer and the evidence panel share the source-rendering rules.

**Files:**
- Create: `frontend/src/components/EvidencePanel.tsx`
- Create: `frontend/src/components/RunMetrics.tsx`
- Create: `frontend/src/components/ActivityTimeline.tsx`
- Create: `frontend/src/components/AgentTraceDrawer.tsx`
- Test: `frontend/src/components/RunMetrics.test.tsx`
- Test: `frontend/src/components/AgentTraceDrawer.test.tsx`
- Modify: `frontend/src/App.tsx`

**Interfaces:**
- Consumes: `costLabel` from `derive/cost`; `RunSnapshot`, `SourceRef`, `TraceEvent`, `UsageView`, `RagContext` from `api/types`.
- Produces:
  - `EvidencePanel({ sources, selectedIds }: { sources: SourceRef[]; selectedIds: ReadonlySet<string> })`
  - `RunMetrics({ snapshot }: { snapshot: RunSnapshot | null })`
  - `ActivityTimeline({ snapshot }: { snapshot: RunSnapshot | null })`
  - `AgentTraceDrawer({ trace, open, onClose }: { trace: TraceEvent[]; open: boolean; onClose: () => void })`
  - `export function selectedSourceIds(trace: TraceEvent[]): Set<string>` — exported from `EvidencePanel.tsx` and reused by the report.

- [ ] **Step 1: Write the failing `RunMetrics` test**

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { aSnapshot, anUsageView } from "../test/fixtures";
import { RunMetrics } from "./RunMetrics";

describe("RunMetrics", () => {
  it("shows the three token counts and the call count", () => {
    render(
      <RunMetrics
        snapshot={aSnapshot({
          usage: anUsageView({
            calls: 4,
            input_tokens: 320,
            output_tokens: 40,
            total_tokens: 360,
            estimated_cost_usd: 0.00042,
          }),
        })}
      />,
    );

    expect(screen.getByText("320")).toBeInTheDocument();
    expect(screen.getByText("40")).toBeInTheDocument();
    expect(screen.getByText("360")).toBeInTheDocument();
    expect(screen.getByText("4")).toBeInTheDocument();
  });

  it("prints a lower bound when some calls have no price", () => {
    render(
      <RunMetrics
        snapshot={aSnapshot({
          usage: anUsageView({ estimated_cost_usd: 0.00042, pricing_complete: false }),
        })}
      />,
    );

    expect(screen.getByText("≥ $0.00042")).toBeInTheDocument();
    expect(screen.getByText(/lower bound/i)).toBeInTheDocument();
  });

  it("says not priced rather than showing zero", () => {
    render(
      <RunMetrics snapshot={aSnapshot({ usage: anUsageView({ estimated_cost_usd: null, calls: 3 }) })} />,
    );

    expect(screen.getByText(/not priced/i)).toBeInTheDocument();
    expect(screen.queryByText("$0.00")).not.toBeInTheDocument();
  });

  it("flags estimated token counts", () => {
    render(
      <RunMetrics
        snapshot={aSnapshot({ usage: anUsageView({ estimated: true, estimated_cost_usd: 0.001 }) })}
      />,
    );

    expect(screen.getByText(/partly estimated/i)).toBeInTheDocument();
  });

  it("shows where the tokens went", () => {
    // Spec 9.4: the second question a developer asks.
    render(
      <RunMetrics
        snapshot={aSnapshot({
          usage: anUsageView({ by_node: [["assess_risk", 210], ["generate_plan", 150]] }),
        })}
      />,
    );

    expect(screen.getByText("assess_risk")).toBeInTheDocument();
    expect(screen.getByText("210")).toBeInTheDocument();
  });

  it("shows nothing rather than zeroes before a run exists", () => {
    render(<RunMetrics snapshot={null} />);

    expect(screen.getByText(/no run started/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd frontend && npm test -- --run src/components/RunMetrics.test.tsx
```

Expected: FAIL — `Failed to resolve import "./RunMetrics"`.

- [ ] **Step 3: Write `EvidencePanel` and `selectedSourceIds`**

```tsx
/**
 * Sources, and whether the agent actually used them.
 *
 * `relevance` is labelled "similarity" because that is what it is — a vector
 * distance, not a judgement. `DESIGN.md` is explicit that the UI must never
 * imply a document is relevant merely because search returned it, so the
 * distinction that carries weight here is *selected* versus *retrieved*, and
 * that comes from the `sources_selected` trace event rather than from the
 * score.
 */

import { FileText } from "lucide-react";

import type { SourceRef, TraceEvent } from "../api/types";
import { EmptyState, Mono } from "./ui";

/**
 * Source ids the agent selected, read off the trace.
 *
 * `sources_selected` events carry the ids in their summary text, which is the
 * observable record of a choice the agent made. Everything else in
 * `retrieved_sources` was returned by search and not used — a distinction
 * worth showing, because it is the difference between evidence and noise.
 */
export function selectedSourceIds(trace: TraceEvent[]): Set<string> {
  const selected = new Set<string>();
  for (const event of trace) {
    if (event.kind !== "sources_selected") continue;
    for (const token of event.summary.split(/[\s,]+/)) {
      if (token !== "") selected.add(token);
    }
  }
  return selected;
}

export function EvidencePanel({
  sources,
  selectedIds,
}: {
  sources: SourceRef[];
  selectedIds: ReadonlySet<string>;
}) {
  if (sources.length === 0) {
    return <EmptyState>No documents retrieved yet.</EmptyState>;
  }

  return (
    <ul className="space-y-2">
      {sources.map((source) => {
        const used = selectedIds.has(source.source_id);
        return (
          <li
            key={source.chunk_id}
            className={`rounded-md border px-3 py-2 ${
              used ? "border-edge-strong bg-surface" : "border-edge bg-surface/40"
            }`}
          >
            <div className="flex items-start gap-2">
              <FileText className="mt-0.5 size-3.5 shrink-0 text-ink-faint" aria-hidden />
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium">{source.title}</p>
                <p className="mt-0.5 flex flex-wrap items-center gap-x-2 text-[11px] text-ink-faint">
                  <span>{source.source_type.replace(/_/g, " ")}</span>
                  <span>·</span>
                  <span>similarity {source.relevance.toFixed(2)}</span>
                  <span>·</span>
                  <span className={used ? "text-risk-low" : "text-ink-faint"}>
                    {used ? "selected by the agent" : "retrieved, not used"}
                  </span>
                </p>
                <p className="mt-1 truncate">
                  <Mono>{source.url_or_reference}</Mono>
                </p>
              </div>
            </div>
          </li>
        );
      })}
    </ul>
  );
}
```

- [ ] **Step 4: Write `RunMetrics`**

```tsx
/**
 * The right region. Stays mounted while the workspace changes underneath it,
 * because token and cost tracking is a graded capability rather than a panel
 * one view owns — and it keeps reporting after the run finishes, which the
 * screenshots disagreed about.
 *
 * The model shown is read from `usage.by_model`, that is, from calls that
 * actually happened. There is no configuration endpoint to ask, and a
 * hardcoded model name would describe whatever was true when it was typed.
 */

import type { RunSnapshot } from "../api/types";
import { costLabel } from "../derive/cost";
import { EmptyState, Field, Panel } from "./ui";

const integer = new Intl.NumberFormat("en-US");

export function RunMetrics({ snapshot }: { snapshot: RunSnapshot | null }) {
  if (snapshot === null) {
    return (
      <Panel title="Telemetry">
        <EmptyState>No run started.</EmptyState>
      </Panel>
    );
  }

  const { usage } = snapshot;
  const cost = costLabel(usage);
  const models = usage.by_node.length > 0 ? usage.by_node : [];

  return (
    <div className="space-y-3">
      <Panel title="Usage">
        <dl className="grid grid-cols-2 gap-3">
          <Field label="Input tokens" value={integer.format(usage.input_tokens)} />
          <Field label="Output tokens" value={integer.format(usage.output_tokens)} />
          <Field label="Total tokens" value={integer.format(usage.total_tokens)} />
          <Field label="LLM calls" value={integer.format(usage.calls)} />
        </dl>
      </Panel>

      <Panel title="Estimated cost">
        <p className={`font-mono text-lg ${cost.lowerBound ? "text-risk-medium" : "text-ink"}`}>
          {cost.text}
        </p>
        {cost.note !== null && <p className="mt-1 text-[11px] text-risk-medium">{cost.note}</p>}
        {cost.estimated && (
          <p className="mt-1 text-[11px] text-risk-medium">
            Token counts partly estimated by a local tokenizer.
          </p>
        )}
      </Panel>

      {models.length > 0 && (
        <Panel title="Tokens by node">
          <ul className="space-y-1">
            {models.map(([node, total]) => (
              <li key={node} className="flex items-baseline justify-between gap-2 text-xs">
                <span className="truncate font-mono text-ink-muted">{node}</span>
                <span className="font-mono text-ink">{integer.format(total)}</span>
              </li>
            ))}
          </ul>
        </Panel>
      )}

      <Panel title="Graph execution">
        <dl className="space-y-2">
          <Field label="Current node" value={<span className="font-mono text-[13px]">{snapshot.current_step ?? "—"}</span>} />
          <Field label="Completed" value={`${snapshot.completed_steps.length} of 8`} />
          {snapshot.rag_context !== null && (
            <>
              <Field label="Retrieval rounds" value={String(snapshot.rag_context.iterations)} />
              <Field
                label="Stopped because"
                value={snapshot.rag_context.stop_reason.replace(/_/g, " ")}
              />
            </>
          )}
        </dl>
      </Panel>
    </div>
  );
}
```

- [ ] **Step 5: Write `ActivityTimeline`**

```tsx
/**
 * The `queued` and `running` view: what has been established so far.
 *
 * Progressively populated rather than a spinner, because `RunSnapshot` carries
 * evidence as it accumulates and a developer watching a three-minute run
 * should be able to read what it has found. `queued` says so plainly — a run
 * beyond the concurrency cap has not started, and reporting it as working
 * would be a lie about work that has not happened.
 */

import { Loader } from "lucide-react";

import type { RunSnapshot } from "../api/types";
import { EvidencePanel, selectedSourceIds } from "./EvidencePanel";
import { EmptyState, Field, LevelBadge, Mono, Panel } from "./ui";

export function ActivityTimeline({ snapshot }: { snapshot: RunSnapshot | null }) {
  if (snapshot === null || snapshot.status === "queued") {
    return (
      <Panel title="Queued">
        <p className="flex items-center gap-2 text-sm text-ink-muted">
          <Loader className="size-4 animate-spin" aria-hidden />
          Waiting for a run slot. Nothing has started yet.
        </p>
      </Panel>
    );
  }

  const selected = selectedSourceIds(snapshot.trace);

  return (
    <div className="space-y-4">
      <Panel title="Activity">
        {snapshot.trace.length === 0 ? (
          <EmptyState>No events recorded yet.</EmptyState>
        ) : (
          <ol className="space-y-1.5">
            {snapshot.trace.map((event) => (
              <li key={event.event_id} className="flex gap-3 text-sm">
                <Mono>{new Date(event.at).toLocaleTimeString()}</Mono>
                <span className="shrink-0 font-mono text-[13px] text-ink-faint">{event.node}</span>
                <span className="min-w-0 flex-1 text-ink">{event.summary}</span>
              </li>
            ))}
          </ol>
        )}
      </Panel>

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel title="Affected files">
          {snapshot.affected_files.length === 0 ? (
            <EmptyState>Not analyzed yet.</EmptyState>
          ) : (
            <ul className="space-y-1">
              {snapshot.affected_files.map((file) => (
                <li key={file.path} className="flex items-baseline justify-between gap-2 text-sm">
                  <Mono>{file.path}</Mono>
                  <span className="shrink-0 text-[11px] text-ink-faint">
                    {file.usage_sites.length} site{file.usage_sites.length === 1 ? "" : "s"}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Panel>

        <Panel title="Breaking changes">
          {snapshot.breaking_changes.length === 0 ? (
            <EmptyState>None established yet.</EmptyState>
          ) : (
            <ul className="space-y-2">
              {snapshot.breaking_changes.map((change) => (
                <li key={change.id} className="flex items-start gap-2 text-sm">
                  <LevelBadge level={change.severity} />
                  <span className="min-w-0 flex-1">{change.title}</span>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>

      <Panel title="Retrieved evidence">
        <EvidencePanel sources={snapshot.retrieved_sources} selectedIds={selected} />
      </Panel>

      {snapshot.risk_analysis !== null && (
        <Panel title="Risk so far">
          <dl className="grid grid-cols-2 gap-3">
            <Field
              label="Verdict"
              value={<LevelBadge level={snapshot.risk_analysis.overall_risk} />}
            />
            <Field
              label="Confidence"
              value={`${Math.round(snapshot.risk_analysis.confidence * 100)}%`}
            />
          </dl>
        </Panel>
      )}
    </div>
  );
}
```

- [ ] **Step 6: Write the failing `AgentTraceDrawer` test**

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { TraceEvent } from "../api/types";
import { AgentTraceDrawer } from "./AgentTraceDrawer";

const event = (overrides: Partial<TraceEvent>): TraceEvent => ({
  event_id: "e-1",
  kind: "node_started",
  node: "assess_risk",
  at: "2026-08-25T12:00:00Z",
  summary: "assess_risk started",
  detail: null,
  ...overrides,
});

describe("AgentTraceDrawer", () => {
  it("renders nothing when closed", () => {
    render(<AgentTraceDrawer trace={[event({})]} open={false} onClose={() => {}} />);

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("lists observable events with their node and kind", () => {
    render(
      <AgentTraceDrawer
        trace={[event({ kind: "query_issued", summary: "pydantic validator migration" })]}
        open
        onClose={() => {}}
      />,
    );

    expect(screen.getByRole("dialog", { name: /agent trace/i })).toBeInTheDocument();
    expect(screen.getByText("query issued")).toBeInTheDocument();
    expect(screen.getByText(/pydantic validator migration/)).toBeInTheDocument();
  });

  it("says what it does not show", () => {
    // CLAUDE.md rule 26. Saying so is the difference between a drawer that
    // omits prompts and a drawer a user assumes is complete.
    render(<AgentTraceDrawer trace={[event({})]} open onClose={() => {}} />);

    expect(screen.getByText(/observable events only/i)).toBeInTheDocument();
    expect(screen.getByText(/no prompts/i)).toBeInTheDocument();
  });

  it("closes on the button and on Escape", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(<AgentTraceDrawer trace={[event({})]} open onClose={onClose} />);

    await user.click(screen.getByRole("button", { name: /close/i }));
    expect(onClose).toHaveBeenCalledTimes(1);

    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it("reports an empty trace as empty rather than as nothing", () => {
    render(<AgentTraceDrawer trace={[]} open onClose={() => {}} />);

    expect(screen.getByText(/no events recorded/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 7: Run it to verify it fails, then write `AgentTraceDrawer`**

```bash
cd frontend && npm test -- --run src/components/AgentTraceDrawer.test.tsx
```

Expected: FAIL — `Failed to resolve import "./AgentTraceDrawer"`.

```tsx
/**
 * The observable event log. CLAUDE.md rule 26 defines what belongs here — node
 * boundaries, queries issued, sources retrieved and selected, decisions,
 * validation outcomes — and what does not: internal prompts and private
 * reasoning.
 *
 * The drawer says so on its face. A drawer that silently omits prompts is one a
 * user assumes is complete, and "this is everything the agent did" is a
 * stronger claim than this surface can make.
 *
 * Separate from `Diagnostics` in the telemetry region on purpose: that is
 * latency and internals, this is the event record, and they have different
 * disclosure rules.
 */

import { X } from "lucide-react";
import { useEffect } from "react";

import type { TraceEvent } from "../api/types";
import { EmptyState, Mono } from "./ui";

export function AgentTraceDrawer({
  trace,
  open,
  onClose,
}: {
  trace: TraceEvent[];
  open: boolean;
  onClose: () => void;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Agent trace"
      className="fixed inset-y-0 right-0 z-20 flex w-full max-w-xl flex-col border-l border-edge bg-surface-sunken shadow-2xl"
    >
      <div className="flex items-start justify-between gap-3 border-b border-edge px-4 py-3">
        <div>
          <h2 className="text-sm font-semibold">Agent trace</h2>
          <p className="mt-0.5 text-[11px] text-ink-faint">
            Observable events only — no prompts, no private reasoning.
          </p>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close agent trace"
          className="rounded-md border border-edge p-1 text-ink-muted hover:text-ink"
        >
          <X className="size-4" aria-hidden />
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        {trace.length === 0 ? (
          <EmptyState>No events recorded yet.</EmptyState>
        ) : (
          <ol className="space-y-2">
            {trace.map((event) => (
              <li key={event.event_id} className="rounded-md border border-edge bg-surface px-3 py-2">
                <p className="flex flex-wrap items-baseline gap-x-2 text-[11px] text-ink-faint">
                  <Mono>{new Date(event.at).toLocaleTimeString()}</Mono>
                  <span className="font-medium text-ink-muted">{event.kind.replace(/_/g, " ")}</span>
                  <span>·</span>
                  <span className="font-mono">{event.node}</span>
                </p>
                <p className="mt-1 text-sm">{event.summary}</p>
                {event.detail !== null && (
                  <p className="mt-1 text-xs text-ink-faint">{event.detail}</p>
                )}
              </li>
            ))}
          </ol>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 8: Wire all four into `App.tsx`**

Add `const [traceOpen, setTraceOpen] = useState(false);`, pass `onOpenTrace={() => setTraceOpen(true)}` to `TopBar`, replace the `metrics` prop with `<RunMetrics snapshot={snapshot} />`, set `drawer={<AgentTraceDrawer trace={snapshot?.trace ?? []} open={traceOpen} onClose={() => setTraceOpen(false)} />}`, and render `{view === "activity" && <ActivityTimeline snapshot={snapshot} />}`.

- [ ] **Step 9: Run the tests and the typecheck**

```bash
cd frontend && npm test -- --run && npx tsc -b
```

Expected: 11 new tests passing, 87 in total, `tsc -b` silent.

- [ ] **Step 10: Commit**

```bash
git add frontend/src/components/EvidencePanel.tsx frontend/src/components/RunMetrics.tsx \
        frontend/src/components/ActivityTimeline.tsx frontend/src/components/RunMetrics.test.tsx \
        frontend/src/components/AgentTraceDrawer.tsx frontend/src/components/AgentTraceDrawer.test.tsx \
        frontend/src/App.tsx
git commit -m "feat(ui): telemetry that reports what it does not know, and a trace that says so

The cost card carries both flags because a total without them is misreadable:
\"not priced\" instead of \$0.00, a >= prefix when some calls have no price,
and a separate note when token counts came from a local tokenizer. The model
shown is read from usage.by_model -- calls that actually happened -- because
there is no configuration endpoint to ask and a hardcoded name describes
whatever was true when it was typed.

The evidence panel labels \`relevance\` as similarity, which is what it is, and
distinguishes selected from merely retrieved using the sources_selected trace
event. DESIGN.md forbids implying a document is relevant because search
returned it, and the score cannot carry that distinction.

The trace drawer states on its face that it shows observable events only, no
prompts. A drawer that silently omits them is one a user assumes is complete,
and that is a stronger claim than rule 26 permits this surface to make.

Activity is progressively populated rather than a spinner, and \`queued\` says
plainly that nothing has started -- a run beyond the concurrency cap has not
begun, and showing it as working would misreport work that has not happened."
```

---

## Task 11: `HumanReviewPanel` — the primary interaction, and the guard that actually holds

The HITL state is one of the primary interactions of the application. Spec §10 blocks a duplicate resume three ways — disabled button, local `submitting` flag, and the server's 409 — and names the last as the only real guarantee. This task builds all three and tests that the third works when the first two are bypassed.

**Files:**
- Create: `frontend/src/components/HumanReviewPanel.tsx`
- Test: `frontend/src/components/HumanReviewPanel.test.tsx`
- Modify: `frontend/src/App.tsx`

**Interfaces:**
- Consumes: `resumeRun`, `ApiFailure` from `api/client`; `InterruptPayload`, `DecisionOption` from `api/types`.
- Produces: `HumanReviewPanel({ threadId, decision, answered, onSubmitted }: { threadId: string; decision: InterruptPayload; answered: number; onSubmitted: () => void })`.

- [ ] **Step 1: Add the interrupt fixture**

Append to `frontend/src/test/fixtures.ts`:

```ts
import type { DecisionOption, InterruptPayload } from "../api/types";

export function anOption(overrides: Partial<DecisionOption> = {}): DecisionOption {
  return {
    id: "staged_rollout",
    label: "Staged rollout",
    summary: "Migrate module by module behind a feature flag.",
    risk_level: "medium",
    effort: "high",
    downtime: false,
    consequences: ["Two code paths coexist for several weeks."],
    supporting_evidence: [{ kind: "doc", source_id: "s-1", chunk_id: "s-1#0", relevance: 0.82 }],
    ...overrides,
  };
}

export function anInterrupt(overrides: Partial<InterruptPayload> = {}): InterruptPayload {
  return {
    question_id: "q-1",
    kind: "strategy_choice",
    reason: "Zero-downtime and the deadline pull in opposite directions.",
    question: "Which migration strategy should the plan follow?",
    evidence: [{ kind: "constraint", field: "zero_downtime", value: "true" }],
    options: [
      anOption({}),
      anOption({
        id: "direct_migration",
        label: "Direct migration",
        summary: "Change everything in one release.",
        risk_level: "high",
        effort: "low",
        downtime: true,
        consequences: ["A short outage during deploy."],
      }),
    ],
    recommendation_id: "staged_rollout",
    consequences_if_unanswered: "Without an answer the run stops here and produces no plan.",
    validation_error: null,
    ...overrides,
  };
}
```

If `tsc` objects to the evidence literals, read the generated union for `EvidenceRef` in `schema.d.ts` and match it exactly — the discriminator is `kind`, and the generated type is the authority.

- [ ] **Step 2: Write the failing test**

```tsx
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import { anInterrupt, anOption } from "../test/fixtures";
import { server } from "../test/server";
import { HumanReviewPanel } from "./HumanReviewPanel";

const RESUME = "http://localhost/api/agent/resume";

const accepted = () =>
  HttpResponse.json(
    { thread_id: "t-1", status: "running", poll_url: "/api/agent/status/t-1" },
    { status: 202 },
  );

const conflict = () =>
  HttpResponse.json(
    { error: { code: "thread_not_awaiting_input", message: "That run is not waiting for an answer.", retryable: false, node: null } },
    { status: 409 },
  );

function panel(props: Partial<Parameters<typeof HumanReviewPanel>[0]> = {}) {
  return (
    <HumanReviewPanel
      threadId="t-1"
      decision={anInterrupt()}
      answered={0}
      onSubmitted={() => {}}
      {...props}
    />
  );
}

describe("HumanReviewPanel", () => {
  it("asks the question and says why it is being asked", () => {
    render(panel());

    expect(screen.getByText(/which migration strategy/i)).toBeInTheDocument();
    expect(screen.getByText(/pull in opposite directions/i)).toBeInTheDocument();
  });

  it("shows what happens if the user walks away", () => {
    // `consequences_if_unanswered` is carried on every payload and had no home
    // in the design pack. It is more useful than showing the user their own
    // constraints back to them.
    render(panel());

    expect(screen.getByText(/stops here and produces no plan/i)).toBeInTheDocument();
  });

  it("renders each option's trade-offs from its own fields", () => {
    render(panel());

    expect(screen.getByRole("radio", { name: /staged rollout/i })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /direct migration/i })).toBeInTheDocument();
    expect(screen.getByText(/two code paths coexist/i)).toBeInTheDocument();
    expect(screen.getByText(/short outage during deploy/i)).toBeInTheDocument();
  });

  it("marks the recommendation without preselecting it", () => {
    // A preselected recommendation is a decision the agent made, submitted
    // under a human's name. Spec 8.2 asks a question; it does not answer one.
    render(panel());

    expect(screen.getByText(/recommended/i)).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /staged rollout/i })).not.toBeChecked();
  });

  it("uses a radio group, not clickable divs", () => {
    render(panel());

    expect(screen.getByRole("radiogroup")).toBeInTheDocument();
    expect(screen.getAllByRole("radio")).toHaveLength(2);
  });

  it("keeps submit disabled until an option is chosen — guard one", () => {
    render(panel());

    expect(screen.getByRole("button", { name: /submit/i })).toBeDisabled();
  });

  it("disables submit the moment it is pressed — guard two", async () => {
    const user = userEvent.setup();
    let calls = 0;
    server.use(
      http.post(RESUME, async () => {
        calls += 1;
        await new Promise((resolve) => setTimeout(resolve, 50));
        return accepted();
      }),
    );
    render(panel());

    await user.click(screen.getByRole("radio", { name: /staged rollout/i }));
    const button = screen.getByRole("button", { name: /submit/i });
    await user.click(button);

    expect(button).toBeDisabled();
    await waitFor(() => expect(calls).toBe(1));
  });

  it("stays disabled after a successful submit, so a second answer is impossible", async () => {
    const user = userEvent.setup();
    const onSubmitted = vi.fn();
    server.use(http.post(RESUME, accepted));
    render(panel({ onSubmitted }));

    await user.click(screen.getByRole("radio", { name: /staged rollout/i }));
    await user.click(screen.getByRole("button", { name: /submit/i }));

    await waitFor(() => expect(onSubmitted).toHaveBeenCalledTimes(1));
    expect(screen.getByRole("button", { name: /submit|submitted/i })).toBeDisabled();
  });

  it("renders the server's 409 as already answered — guard three", async () => {
    // The only real guarantee. This is the case where the first two guards were
    // bypassed: two tabs, a replayed request, a resume from elsewhere.
    const user = userEvent.setup();
    server.use(http.post(RESUME, conflict));
    render(panel());

    await user.click(screen.getByRole("radio", { name: /staged rollout/i }));
    await user.click(screen.getByRole("button", { name: /submit/i }));

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(/already been answered/i),
    );
    expect(screen.getByRole("button", { name: /submit|submitted/i })).toBeDisabled();
  });

  it("re-enables after a retryable failure so the answer is not lost", async () => {
    // A 503 is not a duplicate. Leaving the panel dead would strand the user
    // with a question they cannot answer.
    const user = userEvent.setup();
    server.use(
      http.post(RESUME, () =>
        HttpResponse.json(
          { error: { code: "llm_unavailable", message: "The model provider is unavailable.", retryable: true, node: null } },
          { status: 502 },
        ),
      ),
    );
    render(panel());

    await user.click(screen.getByRole("radio", { name: /staged rollout/i }));
    await user.click(screen.getByRole("button", { name: /submit/i }));

    await waitFor(() => expect(screen.getByRole("button", { name: /submit/i })).toBeEnabled());
    expect(screen.getByRole("alert")).toHaveTextContent(/model provider is unavailable/i);
  });

  it("sends the question id and the chosen option", async () => {
    const user = userEvent.setup();
    let body: { thread_id: string; decision: { question_id: string; selected_option_id: string } } | null = null;
    server.use(
      http.post(RESUME, async ({ request }) => {
        body = (await request.json()) as typeof body;
        return accepted();
      }),
    );
    render(panel());

    await user.click(screen.getByRole("radio", { name: /direct migration/i }));
    await user.click(screen.getByRole("button", { name: /submit/i }));

    await waitFor(() => expect(body).not.toBeNull());
    expect(body!.thread_id).toBe("t-1");
    expect(body!.decision.question_id).toBe("q-1");
    expect(body!.decision.selected_option_id).toBe("direct_migration");
  });

  it("shows a rejected previous answer with what the server said", () => {
    // `validation_error` is set when the graph refused an answer, and the
    // question comes back carrying the reason. Without it the user re-submits
    // the same thing.
    render(
      panel({
        decision: anInterrupt({
          validation_error: "Choose one of: compatibility_layer, staged_rollout, direct_migration",
        }),
      }),
    );

    expect(screen.getByRole("alert")).toHaveTextContent(/choose one of/i);
  });

  it("says which question this is when more than one has been asked", () => {
    // `human_decisions` is an append channel so interrupts fire in sequence. A
    // second question must not read as a bug.
    render(panel({ answered: 1 }));

    expect(screen.getByText(/question 2/i)).toBeInTheDocument();
  });

  it("names the decision kind so the framing is not always strategy", () => {
    render(panel({ decision: anInterrupt({ kind: "risk_acceptance" }) }));

    expect(screen.getByText(/risk acceptance/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 3: Run it to verify it fails**

```bash
cd frontend && npm test -- --run src/components/HumanReviewPanel.test.tsx
```

Expected: FAIL — `Failed to resolve import "./HumanReviewPanel"`.

- [ ] **Step 4: Write the implementation**

```tsx
/**
 * The `awaiting_human` view — one of the two interactions this product exists
 * for.
 *
 * Rendered by `App` *above* a still-incomplete `WorkflowTimeline`, which is
 * where the "can never look finished while waiting" guarantee actually lives.
 * This component's job is narrower: ask the question honestly, and make a
 * second answer impossible.
 *
 * **The triple guard, and why the third is the only real one.** The button is
 * disabled until an option is chosen and while a request is in flight; a local
 * `submitting` flag is set before the request and is deliberately *not*
 * cleared on success. Both are defeated by two tabs, a replayed request, or a
 * resume issued from somewhere else entirely — so the server's 409 is the
 * guarantee, and this panel renders it as a settled fact rather than an error
 * to retry.
 *
 * All four `DecisionKind`s share this layout. The kind sets the framing, not
 * the shape: every one of them is a question, some evidence, and options with
 * trade-offs.
 */

import { AlertTriangle, UserCheck } from "lucide-react";
import { useState } from "react";

import { ApiFailure, resumeRun } from "../api/client";
import type { DecisionOption, InterruptPayload } from "../api/types";
import { Card, LevelBadge, Panel } from "./ui";

export function HumanReviewPanel({
  threadId,
  decision,
  answered,
  onSubmitted,
}: {
  threadId: string;
  decision: InterruptPayload;
  answered: number;
  onSubmitted: () => void;
}) {
  const [selected, setSelected] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [settled, setSettled] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  const blocked = selected === null || submitting || settled;

  async function submit() {
    if (blocked || selected === null) return;

    // Guard two: set before the request, so a second click in the same tick
    // finds it already true.
    setSubmitting(true);
    setProblem(null);

    try {
      await resumeRun({
        thread_id: threadId,
        decision: { question_id: decision.question_id, selected_option_id: selected, rationale: null },
      });
      // Deliberately *not* clearing `submitting`. The answer is in; the next
      // poll moves the view on. Re-enabling here would offer a second submit
      // against a question that is no longer open.
      onSubmitted();
    } catch (error) {
      if (error instanceof ApiFailure && error.httpStatus === 409) {
        // Guard three. Not a failure to retry — a settled fact, so the panel
        // stays closed and says so.
        setSettled(true);
        setProblem("This question has already been answered.");
        return;
      }
      setProblem(
        error instanceof ApiFailure ? error.error.message : "The backend is unreachable.",
      );
      // A retryable failure is not a duplicate. Leaving the panel dead would
      // strand the user with a question they cannot answer.
      setSubmitting(false);
    }
  }

  return (
    <div className="space-y-4">
      <Card className="border-pending-input/50 bg-pending-input/5">
        <div className="flex items-start gap-3 p-4">
          <UserCheck className="mt-0.5 size-5 shrink-0 text-pending-input" aria-hidden />
          <div className="min-w-0">
            <p className="flex flex-wrap items-center gap-x-2 text-[11px] font-semibold tracking-wide text-pending-input uppercase">
              <span>The agent is waiting for your decision</span>
              <span>·</span>
              <span>{decision.kind.replace(/_/g, " ")}</span>
              {answered > 0 && (
                <>
                  <span>·</span>
                  <span>Question {answered + 1}</span>
                </>
              )}
            </p>
            <h2 className="mt-1.5 text-lg font-semibold">{decision.question}</h2>
            <p className="mt-1 text-sm text-ink-muted">{decision.reason}</p>
            <p className="mt-2 text-sm text-risk-medium">
              If you do not answer: {decision.consequences_if_unanswered}
            </p>
          </div>
        </div>
      </Card>

      {decision.validation_error !== null && (
        <p
          role="alert"
          className="flex items-start gap-2 rounded-md border border-risk-medium/50 bg-risk-medium/10 px-3 py-2 text-sm text-risk-medium"
        >
          <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
          {decision.validation_error}
        </p>
      )}

      <Panel title="Options">
        <div role="radiogroup" aria-label="Migration strategy options" className="space-y-2">
          {decision.options.map((option) => (
            <OptionCard
              key={option.id}
              option={option}
              recommended={option.id === decision.recommendation_id}
              checked={selected === option.id}
              disabled={submitting || settled}
              onChoose={() => setSelected(option.id)}
            />
          ))}
        </div>
      </Panel>

      {problem !== null && (
        <p
          role="alert"
          className={`rounded-md border px-3 py-2 text-sm ${
            settled
              ? "border-edge-strong bg-surface-raised text-ink-muted"
              : "border-risk-high/50 bg-risk-high/10 text-risk-high"
          }`}
        >
          {problem}
        </p>
      )}

      <button
        type="button"
        onClick={submit}
        disabled={blocked}
        className="rounded-md border border-pending-input/60 bg-pending-input/15 px-4 py-2 text-sm font-semibold text-pending-input disabled:opacity-50"
      >
        {settled ? "Submitted" : submitting ? "Submitting…" : "Submit decision"}
      </button>
    </div>
  );
}

function OptionCard({
  option,
  recommended,
  checked,
  disabled,
  onChoose,
}: {
  option: DecisionOption;
  recommended: boolean;
  checked: boolean;
  disabled: boolean;
  onChoose: () => void;
}) {
  return (
    <label
      className={`flex cursor-pointer gap-3 rounded-md border px-3 py-2.5 ${
        checked ? "border-pending-input bg-pending-input/10" : "border-edge bg-surface hover:border-edge-strong"
      }`}
    >
      <input
        type="radio"
        name="decision-option"
        value={option.id}
        checked={checked}
        disabled={disabled}
        onChange={onChoose}
        className="mt-1"
      />
      <span className="min-w-0 flex-1">
        <span className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-medium">{option.label}</span>
          {recommended && (
            // Marked, never preselected: a preselected recommendation is a
            // decision the agent made and submitted under a human's name.
            <span className="rounded border border-edge-strong px-1.5 py-0.5 text-[10px] tracking-wide text-ink-muted uppercase">
              Recommended
            </span>
          )}
        </span>
        <span className="mt-1 block text-sm text-ink-muted">{option.summary}</span>
        <span className="mt-2 flex flex-wrap items-center gap-2 text-[11px]">
          <LevelBadge level={option.risk_level}>{option.risk_level} risk</LevelBadge>
          <span className="rounded border border-edge px-1.5 py-0.5 text-ink-muted">
            {option.effort} effort
          </span>
          {option.downtime && (
            <span className="rounded border border-risk-medium/50 px-1.5 py-0.5 text-risk-medium">
              requires downtime
            </span>
          )}
        </span>
        <ul className="mt-2 space-y-0.5">
          {option.consequences.map((consequence) => (
            <li key={consequence} className="text-xs text-ink-muted">
              — {consequence}
            </li>
          ))}
        </ul>
      </span>
    </label>
  );
}
```

- [ ] **Step 5: Wire it into `App.tsx`**

Rendered *below* the timeline in DOM order but as the dominant surface, so the incomplete steps stay visible above the question:

```tsx
        {view === "human-review" && snapshot?.pending_decision != null && (
          <HumanReviewPanel
            threadId={snapshot.thread_id}
            decision={snapshot.pending_decision}
            answered={snapshot.human_decisions.length}
            onSubmitted={() => undefined}
          />
        )}
```

`onSubmitted` is intentionally a no-op: the panel does not tell the app what happened, the next poll does. That is what keeps a single source of run state single.

- [ ] **Step 6: Run the tests and the typecheck**

```bash
cd frontend && npm test -- --run && npx tsc -b
```

Expected: 14 new tests passing, 101 in total, `tsc -b` silent.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/HumanReviewPanel.tsx \
        frontend/src/components/HumanReviewPanel.test.tsx frontend/src/test/fixtures.ts \
        frontend/src/App.tsx
git commit -m "feat(ui): the decision panel, and the one guard that actually holds

Three guards, and the tests are written around which of them is real. The
button is disabled until an option is chosen and while a request is in flight;
the local submitting flag is set before the request and deliberately not
cleared on success. Both are defeated by two tabs, a replayed request, or a
resume issued from elsewhere -- so the server's 409 is the guarantee, and it
renders as a settled fact rather than an error to retry. A 502 does re-enable,
because a retryable failure is not a duplicate and leaving the panel dead would
strand the user with a question they cannot answer.

The recommendation is marked and never preselected. A preselected
recommendation is a decision the agent made, submitted under a human's name.

\`consequences_if_unanswered\` gets the prominence the design pack gave to
showing the user their own constraints back to them. \`validation_error\`
renders too, so a rejected answer comes back with the reason instead of
inviting the same submission again. And the panel says which question this is
when more than one has been asked -- \`human_decisions\` is an append channel
precisely so interrupts can fire in sequence, and a second question must not
read as a bug.

All four decision kinds share the layout. The kind sets the framing, not the
shape: each one is a question, some evidence, and options with trade-offs.

\`onSubmitted\` is a no-op on purpose -- the panel does not tell the app what
happened, the next poll does, which is what keeps one source of run state one."
```

---

## Task 12: `ReportView`, Overview, and the factor table

The report is where rule 1 either holds or does not. Two tabs here carry the honesty load: Overview never shows a confidence figure without its ceilings, and Risk Factors is the seven-factor table that replaced the letter grade, the complexity score and the donut.

**Files:**
- Create: `frontend/src/components/report/ReportView.tsx`
- Create: `frontend/src/components/report/OverviewTab.tsx`
- Create: `frontend/src/components/report/RiskFactorsTab.tsx`
- Test: `frontend/src/components/report/ReportView.test.tsx`
- Test: `frontend/src/components/report/OverviewTab.test.tsx`
- Test: `frontend/src/components/report/RiskFactorsTab.test.tsx`

**Interfaces:**
- Consumes: `FinalReport`, `RunSnapshot`, `RiskAnalysis`, `RiskFactor` from `api/types`; `ui` primitives; `EvidencePanel`/`selectedSourceIds`.
- Produces:
  - `ReportView({ snapshot }: { snapshot: RunSnapshot })` — tab host; `export type ReportTab = "overview" | "risk" | "evidence" | "plan" | "code"`.
  - `OverviewTab({ report }: { report: FinalReport })`
  - `RiskFactorsTab({ analysis }: { analysis: RiskAnalysis | null })`
  - `export function EvidenceRefList({ refs }: { refs: FinalReport["risk_analysis"] extends null ? never : RiskFactor["evidence"] })` — from `RiskFactorsTab.tsx`, reused by `PlanTab` in Task 13. Simplify the type to `RiskFactor["evidence"]`.

- [ ] **Step 1: Write the failing `ReportView` test**

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { aReport, aSnapshot } from "../../test/fixtures";
import { ReportView } from "./ReportView";

describe("ReportView", () => {
  it("offers exactly the five tabs the data supports", () => {
    render(<ReportView snapshot={aSnapshot({ status: "completed", final_report: aReport() })} />);

    expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual([
      "Overview",
      "Risk Factors",
      "Evidence",
      "Plan",
      "Code",
    ]);
  });

  it("offers no PR draft tab", () => {
    // Writing to GitHub is sub-project 2. A PR body behind a button that
    // cannot create anything offers a capability the product does not have.
    render(<ReportView snapshot={aSnapshot({ status: "completed", final_report: aReport() })} />);

    expect(screen.queryByRole("tab", { name: /pull request|pr draft/i })).not.toBeInTheDocument();
  });

  it("opens on the overview", () => {
    render(<ReportView snapshot={aSnapshot({ status: "completed", final_report: aReport() })} />);

    expect(screen.getByRole("tab", { name: "Overview" })).toHaveAttribute("aria-selected", "true");
  });

  it("switches tabs on click", async () => {
    const user = userEvent.setup();
    render(<ReportView snapshot={aSnapshot({ status: "completed", final_report: aReport() })} />);

    await user.click(screen.getByRole("tab", { name: "Risk Factors" }));

    expect(screen.getByRole("tab", { name: "Risk Factors" })).toHaveAttribute("aria-selected", "true");
  });

  it("banners a run whose validation did not pass", () => {
    // Spec 8.4 ends "never silently passes", so the report never silently
    // omits it either.
    render(
      <ReportView
        snapshot={aSnapshot({
          status: "completed_with_warnings",
          final_report: aReport({ completed_with_warnings: true }),
        })}
      />,
    );

    expect(screen.getByRole("alert")).toHaveTextContent(/validation did not pass/i);
  });

  it("says so rather than rendering an empty report when there is none", () => {
    render(<ReportView snapshot={aSnapshot({ status: "completed", final_report: null })} />);

    expect(screen.getByText(/no report was produced/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Extend the fixtures with a report builder**

Append to `frontend/src/test/fixtures.ts`:

```ts
import type { FinalReport, RiskAnalysis, RiskFactor } from "../api/types";

export function aFactor(overrides: Partial<RiskFactor> = {}): RiskFactor {
  return {
    id: "breaking_change_exposure",
    name: "Breaking change exposure",
    category: "breaking_change_exposure",
    level: "high",
    weight: 0.25,
    detail: "Four breaking changes touch symbols this repository uses.",
    evidence: [{ kind: "repo", file: "src/app/models.py", line: 12, snippet: null }],
    ...overrides,
  };
}

export function aRiskAnalysis(overrides: Partial<RiskAnalysis> = {}): RiskAnalysis {
  return {
    overall_risk: "high",
    aggregate_risk: "high",
    clamp_floor: null,
    confidence: 0.62,
    confidence_ceilings: [],
    factors: [aFactor()],
    summary: "Four breaking changes reach code this repository executes.",
    qualitative_notes: [],
    ...overrides,
  };
}

export function aReport(overrides: Partial<FinalReport> = {}): FinalReport {
  return {
    thread_id: "t-1",
    repo_ref: { kind: "local", path: "/srv/repo" },
    dependency: { name: "pydantic", current_version: "1.10.13", target_version: "2.9.2" },
    constraints: { zero_downtime: false, minimize_effort: false, deadline: null, risk_tolerance: "medium" },
    commit_sha: null,
    completed_at: "2026-08-25T12:00:00Z",
    repo_analysis: null,
    affected_files: [],
    breaking_changes: [],
    rag_context: null,
    risk_analysis: aRiskAnalysis(),
    migration_plan: null,
    validation: null,
    human_decisions: [],
    usage: {
      calls: 4,
      input_tokens: 320,
      output_tokens: 40,
      total_tokens: 360,
      by_model: [],
      by_node: [],
      estimated: false,
      pricing_complete: true,
      estimated_cost_usd: 0.00042,
    },
    agent_trace: [],
    errors: [],
    completed_with_warnings: false,
    version_discrepancy: null,
    ...overrides,
  };
}
```

If `tsc` reports missing or extra keys, the generated schema is the authority — match it, and do not cast.

- [ ] **Step 3: Run the test to verify it fails, then write `ReportView`**

```bash
cd frontend && npm test -- --run src/components/report/ReportView.test.tsx
```

Expected: FAIL — `Failed to resolve import "./ReportView"`.

```tsx
/**
 * The report, and the only tab bar in the application.
 *
 * Tabs live here and nowhere else. Workflow view selection stays derived from
 * status, so there is no navigation that walks past an unanswered decision or
 * reaches a report that does not exist yet — which is what a navigable
 * workflow tab bar would have permitted.
 *
 * Five tabs, and the two the design pack asked for are absent for stated
 * reasons. There is no unified-diff Changes tab: `MigrationStep` carries no
 * patch and `validate_plan` has no check that a patch parses or applies, so
 * the tab would display LLM-authored code with nothing verifying it. `Code`
 * shows the cited *existing* code instead. And there is no PR Draft tab —
 * writing to GitHub is sub-project 2, and a PR body behind a button that
 * cannot create anything offers a capability the product does not have.
 */

import { useState } from "react";

import type { RunSnapshot } from "../../api/types";
import { EmptyState, Panel } from "../ui";
import { CodeTab } from "./CodeTab";
import { EvidenceTab } from "./EvidenceTab";
import { OverviewTab } from "./OverviewTab";
import { PlanTab } from "./PlanTab";
import { RiskFactorsTab } from "./RiskFactorsTab";

export type ReportTab = "overview" | "risk" | "evidence" | "plan" | "code";

const TABS: { id: ReportTab; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "risk", label: "Risk Factors" },
  { id: "evidence", label: "Evidence" },
  { id: "plan", label: "Plan" },
  { id: "code", label: "Code" },
];

export function ReportView({ snapshot }: { snapshot: RunSnapshot }) {
  const [tab, setTab] = useState<ReportTab>("overview");
  const report = snapshot.final_report;

  if (report === null) {
    return (
      <Panel title="Report">
        <EmptyState>
          This run finished without producing a report. The activity trace shows how far it got.
        </EmptyState>
      </Panel>
    );
  }

  return (
    <div className="space-y-4">
      {report.completed_with_warnings && (
        <p
          role="alert"
          className="rounded-md border border-risk-medium/50 bg-risk-medium/10 px-3 py-2 text-sm text-risk-medium"
        >
          Validation did not pass. The failed checks are listed under Plan — the plan below is
          reported with them rather than without them.
        </p>
      )}

      <div role="tablist" aria-label="Report sections" className="flex gap-1 border-b border-edge">
        {TABS.map((each) => (
          <button
            key={each.id}
            type="button"
            role="tab"
            aria-selected={tab === each.id}
            onClick={() => setTab(each.id)}
            className={`-mb-px border-b-2 px-3 py-2 text-sm ${
              tab === each.id
                ? "border-ink text-ink"
                : "border-transparent text-ink-muted hover:text-ink"
            }`}
          >
            {each.label}
          </button>
        ))}
      </div>

      {tab === "overview" && <OverviewTab report={report} />}
      {tab === "risk" && <RiskFactorsTab analysis={report.risk_analysis} />}
      {tab === "evidence" && <EvidenceTab report={report} />}
      {tab === "plan" && <PlanTab report={report} />}
      {tab === "code" && <CodeTab report={report} />}
    </div>
  );
}
```

The three tabs from Task 13 are imported here. Create them as one-line stubs returning `null` so this task's tests run, and replace them in Task 13 — a stub that a later task replaces is not a placeholder in the plan's sense, because the plan says exactly when and with what.

- [ ] **Step 4: Write the failing `OverviewTab` test**

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { aReport, aRiskAnalysis } from "../../test/fixtures";
import { OverviewTab } from "./OverviewTab";

describe("OverviewTab", () => {
  it("shows the verdict and the executive summary", () => {
    render(<OverviewTab report={aReport()} />);

    expect(screen.getByText(/four breaking changes reach code/i)).toBeInTheDocument();
    expect(screen.getByText("high")).toBeInTheDocument();
  });

  it("never shows a confidence figure without its ceilings", () => {
    // A confidence number alone is the least useful honest figure in the
    // product. Spec 8.1's ceilings each carry a reason a user can act on.
    render(
      <OverviewTab
        report={aReport({
          risk_analysis: aRiskAnalysis({
            confidence: 0.3,
            confidence_ceilings: [
              { reason: "No supporting evidence was retrieved.", ceiling: 0.3 },
            ],
          }),
        })}
      />,
    );

    expect(screen.getByText("30%")).toBeInTheDocument();
    expect(screen.getByText(/no supporting evidence was retrieved/i)).toBeInTheDocument();
    expect(screen.getByText(/capped at 30%/i)).toBeInTheDocument();
  });

  it("says a clamped verdict was clamped", () => {
    render(
      <OverviewTab
        report={aReport({
          risk_analysis: aRiskAnalysis({
            aggregate_risk: "low",
            overall_risk: "high",
            clamp_floor: "high",
          }),
        })}
      />,
    );

    expect(screen.getByText(/raised from low/i)).toBeInTheDocument();
  });

  it("does not mention a clamp when there was none", () => {
    render(<OverviewTab report={aReport()} />);

    expect(screen.queryByText(/raised from/i)).not.toBeInTheDocument();
  });

  it("shows a version discrepancy as both values, side by side", () => {
    render(<OverviewTab report={aReport({ version_discrepancy: ["1.9.0", "1.10.13"] })} />);

    expect(screen.getByText(/you stated/i)).toBeInTheDocument();
    expect(screen.getByText("1.9.0")).toBeInTheDocument();
    expect(screen.getByText(/manifests declare/i)).toBeInTheDocument();
    expect(screen.getByText("1.10.13")).toBeInTheDocument();
  });

  it("shows no complexity score, grade, or duration", () => {
    // READINESS 2.1-2.3: no field backs any of them, and the factor table
    // answers what they were gesturing at.
    render(<OverviewTab report={aReport()} />);

    expect(screen.queryByText(/complexity/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/grade/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/duration/i)).not.toBeInTheDocument();
  });

  it("counts what it can count and names what it counted", () => {
    render(
      <OverviewTab
        report={aReport({
          affected_files: [
            {
              path: "src/app/models.py",
              usage_sites: [
                { file: "src/app/models.py", line: 12, column: 0, symbol: "BaseModel", kind: "import", confidence: "high", snippet: null },
              ],
              is_test: false,
              commit_count: null,
              last_modified: null,
            },
          ],
        })}
      />,
    );

    expect(screen.getByText(/affected files/i)).toBeInTheDocument();
    expect(screen.getByText(/usage sites/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 5: Write `OverviewTab`**

```tsx
/**
 * The report's front page. Everything here names the field it came from.
 *
 * Three rules this tab exists to keep:
 *
 *   - **Confidence never appears alone.** A percentage on a gradient bar tells
 *     a reader nothing they can act on; "capped at 30% because no supporting
 *     evidence was retrieved" tells them what to fix.
 *   - **A clamped verdict says it was clamped.** `aggregate_risk` is what the
 *     factors summed to; `overall_risk` is what is reported; `clamp_floor` is
 *     why they differ.
 *   - **A version discrepancy shows both values.** Overriding in either
 *     direction would leave every version-dependent claim downstream resting
 *     on a guess the reader never saw.
 *
 * What is absent: complexity out of ten, a letter grade, an estimated
 * duration, and an impact donut. No field backs any of them (READINESS
 * §2.1–2.4), and the factor table answers the question they gestured at.
 */

import type { FinalReport } from "../../api/types";
import { EmptyState, Field, LevelBadge, Mono, Panel } from "../ui";

export function OverviewTab({ report }: { report: FinalReport }) {
  const risk = report.risk_analysis;
  const usageSites = report.affected_files.reduce(
    (total, file) => total + file.usage_sites.length,
    0,
  );

  return (
    <div className="space-y-4">
      <Panel title="Verdict">
        {risk === null ? (
          <EmptyState>No risk assessment was produced.</EmptyState>
        ) : (
          <div className="space-y-3">
            <dl className="grid grid-cols-2 gap-4 sm:grid-cols-3">
              <Field label="Overall risk" value={<LevelBadge level={risk.overall_risk} />} />
              <Field
                label="Confidence"
                value={`${Math.round(risk.confidence * 100)}%`}
              />
              <Field label="Factors measured" value={String(risk.factors.length)} />
            </dl>

            {risk.clamp_floor !== null && risk.clamp_floor !== risk.aggregate_risk && (
              <p className="rounded-md border border-risk-medium/40 bg-risk-medium/10 px-3 py-2 text-xs text-risk-medium">
                Raised from {risk.aggregate_risk} to {risk.overall_risk} by a floor the factors
                cannot lower.
              </p>
            )}

            {risk.confidence_ceilings.length > 0 && (
              <ul className="space-y-1">
                {risk.confidence_ceilings.map((ceiling) => (
                  <li key={ceiling.reason} className="text-xs text-risk-medium">
                    Capped at {Math.round(ceiling.ceiling * 100)}% — {ceiling.reason}
                  </li>
                ))}
              </ul>
            )}

            <p className="text-sm text-ink">{risk.summary}</p>

            {risk.qualitative_notes.length > 0 && (
              <div>
                <p className="text-[11px] tracking-wide text-ink-faint uppercase">
                  Notes — these carry no weight in any level
                </p>
                <ul className="mt-1 space-y-0.5">
                  {risk.qualitative_notes.map((note) => (
                    <li key={note} className="text-xs text-ink-muted">
                      — {note}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
      </Panel>

      <Panel title="Scope">
        <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          <Field label="Affected files" value={String(report.affected_files.length)} />
          <Field label="Usage sites" value={String(usageSites)} />
          <Field label="Breaking changes" value={String(report.breaking_changes.length)} />
          <Field
            label="Commit"
            value={
              report.commit_sha === null ? "—" : <Mono>{report.commit_sha.slice(0, 10)}</Mono>
            }
          />
        </dl>
      </Panel>

      {report.version_discrepancy !== null && (
        <Panel title="Version discrepancy">
          <dl className="grid grid-cols-2 gap-4">
            <Field
              label="You stated"
              value={<Mono>{report.version_discrepancy[0]}</Mono>}
            />
            <Field
              label="The manifests declare"
              value={<Mono>{report.version_discrepancy[1]}</Mono>}
            />
          </dl>
          <p className="mt-2 text-xs text-risk-medium">
            Neither was silently preferred. Every claim below is resolved against the analyzed
            tree.
          </p>
        </Panel>
      )}

      {report.migration_plan !== null && (
        <Panel title="Recommended strategy">
          <p className="text-sm font-medium">
            {report.migration_plan.strategy_id.replace(/_/g, " ")}
          </p>
          <p className="mt-1 text-sm text-ink-muted">{report.migration_plan.summary}</p>
        </Panel>
      )}

      <Panel title="Key breaking changes">
        {report.breaking_changes.length === 0 ? (
          <EmptyState>None established.</EmptyState>
        ) : (
          <ul className="space-y-2">
            {report.breaking_changes.map((change) => (
              <li key={change.id} className="flex items-start gap-2">
                <LevelBadge level={change.severity} />
                <div className="min-w-0">
                  <p className="text-sm font-medium">{change.title}</p>
                  <p className="text-xs text-ink-muted">{change.description}</p>
                  <p className="mt-0.5 text-[11px] text-ink-faint">
                    Symbols: {change.affected_symbols.join(", ")} · Source: {change.source.title}
                  </p>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Panel>
    </div>
  );
}
```

- [ ] **Step 6: Write the failing `RiskFactorsTab` test, then the component**

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { aFactor, aRiskAnalysis } from "../../test/fixtures";
import { RiskFactorsTab } from "./RiskFactorsTab";

describe("RiskFactorsTab", () => {
  it("lists each factor with its level, weight and detail", () => {
    render(<RiskFactorsTab analysis={aRiskAnalysis()} />);

    expect(screen.getByText("Breaking change exposure")).toBeInTheDocument();
    expect(screen.getByText("high")).toBeInTheDocument();
    expect(screen.getByText(/0\.25/)).toBeInTheDocument();
    expect(screen.getByText(/four breaking changes touch symbols/i)).toBeInTheDocument();
  });

  it("discloses the evidence a factor cites", async () => {
    // Every factor carries `evidence` with min_length=1, and per-factor
    // disclosure is what makes the verdict inspectable rather than asserted.
    const user = userEvent.setup();
    render(<RiskFactorsTab analysis={aRiskAnalysis()} />);

    await user.click(screen.getByRole("button", { name: /evidence/i }));

    expect(screen.getByText(/src\/app\/models\.py/)).toBeInTheDocument();
    expect(screen.getByText(/:12/)).toBeInTheDocument();
  });

  it("shows every factor, not only the alarming ones", () => {
    render(
      <RiskFactorsTab
        analysis={aRiskAnalysis({
          factors: [
            aFactor({ id: "a", name: "Blast radius", level: "low" }),
            aFactor({ id: "b", name: "Test coverage of affected", level: "medium" }),
          ],
        })}
      />,
    );

    expect(screen.getByText("Blast radius")).toBeInTheDocument();
    expect(screen.getByText("Test coverage of affected")).toBeInTheDocument();
  });

  it("says the levels came from a threshold table, not the model", () => {
    // Rule 19. The claim a reader most needs about this table is who computed
    // it.
    render(<RiskFactorsTab analysis={aRiskAnalysis()} />);

    expect(screen.getByText(/threshold table/i)).toBeInTheDocument();
  });

  it("says so when there is no assessment", () => {
    render(<RiskFactorsTab analysis={null} />);

    expect(screen.getByText(/no risk assessment/i)).toBeInTheDocument();
  });
});
```

```tsx
/**
 * The seven-factor table — the product's core honesty artifact.
 *
 * Each factor's level comes from a documented threshold table without an LLM
 * (rule 19), each carries evidence with `min_length=1`, and each row can be
 * opened to see exactly what it cited. That combination is the reason a
 * developer should believe the verdict, and it is what an earlier design
 * replaced with a letter grade, a score out of ten and a donut chart.
 *
 * Every factor is shown, not only the alarming ones: a table that hid its low
 * rows would be a table whose absences the reader cannot interpret.
 */

import { useState } from "react";

import type { RiskAnalysis, RiskFactor } from "../../api/types";
import { EmptyState, LevelBadge, Mono, Panel } from "../ui";

export function EvidenceRefList({ refs }: { refs: RiskFactor["evidence"] }) {
  return (
    <ul className="mt-2 space-y-1 border-l border-edge pl-3">
      {refs.map((ref, index) => (
        <li key={index} className="text-xs">
          {ref.kind === "repo" && (
            <>
              <Mono>
                {ref.file}:{ref.line}
              </Mono>
              {ref.snippet !== null && (
                <pre className="mt-1 overflow-x-auto rounded bg-surface-sunken p-2 font-mono text-[12px] text-ink-muted">
                  {ref.snippet}
                </pre>
              )}
            </>
          )}
          {ref.kind === "doc" && (
            <span className="text-ink-muted">
              Document <Mono>{ref.source_id}</Mono>
              {ref.relevance !== null && ` · similarity ${ref.relevance.toFixed(2)}`}
            </span>
          )}
          {ref.kind === "constraint" && (
            <span className="text-ink-muted">
              Your constraint <Mono>{ref.field}</Mono> = <Mono>{ref.value}</Mono>
            </span>
          )}
        </li>
      ))}
    </ul>
  );
}

function FactorRow({ factor }: { factor: RiskFactor }) {
  const [open, setOpen] = useState(false);

  return (
    <li className="border-b border-edge py-3 last:border-0">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <LevelBadge level={factor.level} />
        <span className="text-sm font-medium">{factor.name}</span>
        <span className="text-[11px] text-ink-faint">weight {factor.weight.toFixed(2)}</span>
        <button
          type="button"
          onClick={() => setOpen(!open)}
          aria-expanded={open}
          className="ml-auto text-[11px] text-ink-muted underline hover:text-ink"
        >
          {factor.evidence.length} evidence {factor.evidence.length === 1 ? "ref" : "refs"}
        </button>
      </div>
      <p className="mt-1 text-sm text-ink-muted">{factor.detail}</p>
      {open && <EvidenceRefList refs={factor.evidence} />}
    </li>
  );
}

export function RiskFactorsTab({ analysis }: { analysis: RiskAnalysis | null }) {
  if (analysis === null) {
    return (
      <Panel title="Risk factors">
        <EmptyState>No risk assessment was produced for this run.</EmptyState>
      </Panel>
    );
  }

  return (
    <Panel title="Risk factors">
      <p className="mb-3 text-xs text-ink-faint">
        Every level below is computed from a documented threshold table, not generated by the
        model. Open a row to see what it cited.
      </p>
      {analysis.factors.length === 0 ? (
        <EmptyState>No factors were measured.</EmptyState>
      ) : (
        <ul>
          {analysis.factors.map((factor) => (
            <FactorRow key={factor.id} factor={factor} />
          ))}
        </ul>
      )}
    </Panel>
  );
}
```

- [ ] **Step 7: Run the tests and the typecheck**

```bash
cd frontend && npm test -- --run && npx tsc -b
```

Expected: 18 new tests passing, 119 in total, `tsc -b` silent.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/report frontend/src/test/fixtures.ts
git commit -m "feat(ui): the report's honesty core -- verdict, ceilings, factor table

Confidence never renders alone. A percentage on a gradient bar tells a reader
nothing to act on; "capped at 30% because no supporting evidence was
retrieved" tells them what to fix, and spec 8.1's ceilings each carry that
reason. A clamped verdict says it was clamped, naming both the aggregate the
factors summed to and the floor that raised it. A version discrepancy shows
both values side by side, because overriding either way leaves every
version-dependent claim downstream resting on a guess the reader never saw.

The seven-factor table is the centrepiece it should always have been: every
factor shown -- not only the alarming ones, since hidden low rows make the
absences uninterpretable -- each openable to the evidence it cited, and the
table stating that its levels came from a threshold table rather than the
model (rule 19).

Absent, with tests asserting they stay absent: complexity out of ten, a letter
grade, an estimated duration, the impact donut, and a PR draft tab. No field
backs the first four; the fifth would offer a capability sub-project 2 has not
built."
```

---

## Task 13: Evidence, Plan, and Code tabs

The three detail tabs. `PlanTab` carries the two outputs the design pack had nowhere to put and which the graded requirements depend on: `human_decisions_applied`, which is how "the human decision provably changes downstream generation" gets *shown* rather than asserted in a test, and `unaddressed_with_reason`.

**Files:**
- Create (replacing the Task 12 stubs): `frontend/src/components/report/EvidenceTab.tsx`, `PlanTab.tsx`, `CodeTab.tsx`
- Test: `frontend/src/components/report/PlanTab.test.tsx`
- Test: `frontend/src/components/report/CodeTab.test.tsx`

**Interfaces:**
- Consumes: `FinalReport` from `api/types`; `EvidenceRefList` from `./RiskFactorsTab`; `EvidencePanel`/`selectedSourceIds` from `../EvidencePanel`.
- Produces: `EvidenceTab({ report })`, `PlanTab({ report })`, `CodeTab({ report })`.

- [ ] **Step 1: Write the failing `PlanTab` test**

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { aReport } from "../../test/fixtures";
import { PlanTab } from "./PlanTab";

const plan = {
  strategy_id: "staged_rollout" as const,
  summary: "Migrate module by module behind a flag.",
  steps: [
    {
      order: 1,
      title: "Replace @validator with @field_validator",
      description: "Four call sites in two modules.",
      files: ["src/app/models.py"],
      rationale_evidence: [],
      validation: "pytest tests/unit",
      requires_downtime: false,
    },
  ],
  human_decisions_applied: [
    { decision_id: "q-1", how_it_changed_the_plan: "Staged rollout chosen, so step 3 gates on a flag." },
  ],
  unaddressed_with_reason: [
    { path: "tests/test_legacy.py", reason: "Test-only; no runtime exposure." },
  ],
  mitigations: ["Keep the compatibility shim for one release."],
};

describe("PlanTab", () => {
  it("lists the steps in order with their files", () => {
    render(<PlanTab report={aReport({ migration_plan: plan })} />);

    expect(screen.getByText(/replace @validator/i)).toBeInTheDocument();
    expect(screen.getByText("src/app/models.py")).toBeInTheDocument();
  });

  it("shows how each human decision changed the plan", () => {
    // The graded requirement "human decision provably changes downstream
    // generation", shown to a user rather than asserted in a test.
    render(<PlanTab report={aReport({ migration_plan: plan })} />);

    expect(screen.getByText(/step 3 gates on a flag/i)).toBeInTheDocument();
  });

  it("shows unaddressed files with their reasons, not behind a disclosure", () => {
    // Spec 8.4 check 8. Bad news is not detail.
    render(<PlanTab report={aReport({ migration_plan: plan })} />);

    expect(screen.getByText("tests/test_legacy.py")).toBeInTheDocument();
    expect(screen.getByText(/test-only; no runtime exposure/i)).toBeInTheDocument();
  });

  it("lists every validation check, and names the failures", () => {
    render(
      <PlanTab
        report={aReport({
          migration_plan: plan,
          validation: {
            attempt: 2,
            outcomes: [
              { check_id: "sources_resolve", passed: true, detail: "All 3 sources resolve.", offenders: [] },
              { check_id: "plan_is_ordered", passed: false, detail: "Step order is not contiguous.", offenders: ["step 3"] },
            ],
            passed: false,
            failures: [
              { check_id: "plan_is_ordered", passed: false, detail: "Step order is not contiguous.", offenders: ["step 3"] },
            ],
          },
        })}
      />,
    );

    expect(screen.getByText(/sources resolve/i)).toBeInTheDocument();
    expect(screen.getByText(/plan is ordered/i)).toBeInTheDocument();
    expect(screen.getByText("step 3")).toBeInTheDocument();
    expect(screen.getByText(/1 of 2 checks passed/i)).toBeInTheDocument();
  });

  it("marks a step that requires downtime", () => {
    render(
      <PlanTab
        report={aReport({
          migration_plan: { ...plan, steps: [{ ...plan.steps[0], requires_downtime: true }] },
        })}
      />,
    );

    expect(screen.getByText(/requires downtime/i)).toBeInTheDocument();
  });

  it("says so when no plan was produced", () => {
    render(<PlanTab report={aReport({ migration_plan: null })} />);

    expect(screen.getByText(/no plan was produced/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Write the failing `CodeTab` test**

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { aReport } from "../../test/fixtures";
import { CodeTab } from "./CodeTab";

const file = {
  path: "src/app/models.py",
  usage_sites: [
    { file: "src/app/models.py", line: 12, column: 0, symbol: "BaseModel", kind: "import" as const, confidence: "high" as const, snippet: "from pydantic import BaseModel" },
    { file: "src/app/models.py", line: 31, column: 4, symbol: "Optional", kind: "optional_field" as const, confidence: "medium" as const, snippet: null },
  ],
  is_test: false,
  commit_count: 7,
  last_modified: null,
};

describe("CodeTab", () => {
  it("lists each cited usage site with its line, column, symbol and kind", () => {
    render(<CodeTab report={aReport({ affected_files: [file] })} />);

    expect(screen.getByText("src/app/models.py")).toBeInTheDocument();
    expect(screen.getByText(/12:0/)).toBeInTheDocument();
    expect(screen.getByText("BaseModel")).toBeInTheDocument();
    expect(screen.getByText(/optional field/i)).toBeInTheDocument();
  });

  it("shows the confidence of each site", () => {
    render(<CodeTab report={aReport({ affected_files: [file] })} />);

    expect(screen.getByText("high")).toBeInTheDocument();
    expect(screen.getByText("medium")).toBeInTheDocument();
  });

  it("shows the captured snippet where there is one", () => {
    render(<CodeTab report={aReport({ affected_files: [file] })} />);

    expect(screen.getByText(/from pydantic import BaseModel/)).toBeInTheDocument();
  });

  it("says this is existing code, not a proposed patch", () => {
    // The distinction the whole tab turns on. A reader who thinks these are
    // generated changes is reading unverified output as fact.
    render(<CodeTab report={aReport({ affected_files: [file] })} />);

    expect(screen.getByText(/existing code/i)).toBeInTheDocument();
  });

  it("marks test files, because they weigh differently", () => {
    render(<CodeTab report={aReport({ affected_files: [{ ...file, is_test: true }] })} />);

    expect(screen.getByText(/test file/i)).toBeInTheDocument();
  });

  it("says so when nothing was affected", () => {
    render(<CodeTab report={aReport({ affected_files: [] })} />);

    expect(screen.getByText(/no affected files/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 3: Run both to verify they fail**

```bash
cd frontend && npm test -- --run src/components/report/PlanTab.test.tsx src/components/report/CodeTab.test.tsx
```

Expected: FAIL — the stubs from Task 12 render `null`, so every query misses.

- [ ] **Step 4: Write `EvidenceTab`**

```tsx
/**
 * Repository evidence first, documents second.
 *
 * The ordering is a claim about weight, not layout: a file and a line is a
 * fact about this repository, while a retrieved document is context that may
 * or may not apply to it. `DESIGN.md`: evidence is supporting information, not
 * a replacement for repository analysis.
 */

import type { FinalReport } from "../../api/types";
import { EvidencePanel, selectedSourceIds } from "../EvidencePanel";
import { EmptyState, Field, Mono, Panel } from "../ui";

export function EvidenceTab({ report }: { report: FinalReport }) {
  const selected = selectedSourceIds(report.agent_trace);
  const sources = report.rag_context?.sources ?? [];

  return (
    <div className="space-y-4">
      <Panel title="Repository evidence">
        {report.affected_files.length === 0 ? (
          <EmptyState>No usage sites were found.</EmptyState>
        ) : (
          <ul className="space-y-1">
            {report.affected_files.flatMap((file) =>
              file.usage_sites.map((site) => (
                <li key={`${site.file}:${site.line}:${site.column}`} className="text-sm">
                  <Mono>
                    {site.file}:{site.line}
                  </Mono>{" "}
                  <span className="text-ink-muted">
                    {site.symbol} · {site.kind.replace(/_/g, " ")}
                  </span>
                </li>
              )),
            )}
          </ul>
        )}
      </Panel>

      <Panel title="Retrieved documents">
        <EvidencePanel sources={sources} selectedIds={selected} />
      </Panel>

      {report.rag_context !== null && (
        <Panel title="Retrieval">
          <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            <Field label="Rounds" value={String(report.rag_context.iterations)} />
            <Field label="Considered" value={String(report.rag_context.sources_considered)} />
            <Field
              label="Stopped because"
              value={report.rag_context.stop_reason.replace(/_/g, " ")}
            />
            <Field
              label="Evidence available"
              value={report.rag_context.evidence_available ? "yes" : "no"}
            />
          </dl>
          {report.rag_context.unknowns.length > 0 && (
            <div className="mt-3">
              <p className="text-[11px] tracking-wide text-ink-faint uppercase">
                What retrieval could not establish
              </p>
              <ul className="mt-1 space-y-0.5">
                {report.rag_context.unknowns.map((unknown) => (
                  <li key={unknown} className="text-xs text-risk-medium">
                    — {unknown}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </Panel>
      )}
    </div>
  );
}
```

If `rag_context.sources` is not the field name in the generated schema, read `RagContext` in `schema.d.ts` and use the real one — fall back to `report.rag_context?.selected_sources ?? []` only if that is what exists. Do not invent a field.

- [ ] **Step 5: Write `PlanTab`**

```tsx
/**
 * The plan, and the three things about it a reader most needs.
 *
 *   - **`human_decisions_applied`** with `how_it_changed_the_plan`. This is how
 *     "the human decision provably changes downstream generation" is *shown*
 *     to a user rather than asserted in a test, which makes it the single most
 *     load-bearing panel in the report.
 *   - **`unaddressed_with_reason`** — affected files no step addresses, with
 *     the reason (spec §8.4 check 8). Not behind a disclosure: bad news is not
 *     detail.
 *   - **All ten validation checks**, failures named with their offenders.
 *     Validation never silently passes, so the report never silently omits a
 *     failure.
 */

import type { FinalReport } from "../../api/types";
import { EmptyState, LevelBadge, Mono, Panel } from "../ui";
import { EvidenceRefList } from "./RiskFactorsTab";

export function PlanTab({ report }: { report: FinalReport }) {
  const plan = report.migration_plan;

  if (plan === null) {
    return (
      <Panel title="Plan">
        <EmptyState>No plan was produced for this run.</EmptyState>
      </Panel>
    );
  }

  const validation = report.validation;
  const passedCount = validation?.outcomes.filter((outcome) => outcome.passed).length ?? 0;

  return (
    <div className="space-y-4">
      <Panel title={`Strategy — ${plan.strategy_id.replace(/_/g, " ")}`}>
        <p className="text-sm text-ink-muted">{plan.summary}</p>
      </Panel>

      <Panel title="Steps">
        {plan.steps.length === 0 ? (
          <EmptyState>The plan has no steps.</EmptyState>
        ) : (
          <ol className="space-y-3">
            {plan.steps.map((step) => (
              <li key={step.order} className="border-b border-edge pb-3 last:border-0 last:pb-0">
                <div className="flex flex-wrap items-baseline gap-2">
                  <span className="font-mono text-[13px] text-ink-faint">{step.order}.</span>
                  <span className="text-sm font-medium">{step.title}</span>
                  {step.requires_downtime && (
                    <span className="rounded border border-risk-medium/50 px-1.5 py-0.5 text-[10px] tracking-wide text-risk-medium uppercase">
                      Requires downtime
                    </span>
                  )}
                </div>
                <p className="mt-1 text-sm text-ink-muted">{step.description}</p>
                {step.files.length > 0 && (
                  <p className="mt-1 flex flex-wrap gap-x-3">
                    {step.files.map((path) => (
                      <Mono key={path}>{path}</Mono>
                    ))}
                  </p>
                )}
                {step.validation !== null && (
                  <p className="mt-1 text-xs text-ink-faint">
                    Verify with <Mono>{step.validation}</Mono>
                  </p>
                )}
                {step.rationale_evidence.length > 0 && (
                  <EvidenceRefList refs={step.rationale_evidence} />
                )}
              </li>
            ))}
          </ol>
        )}
      </Panel>

      <Panel title="Your decisions, and what they changed">
        {plan.human_decisions_applied.length === 0 ? (
          <EmptyState>
            No human decision was required — the constraints settled every question.
          </EmptyState>
        ) : (
          <ul className="space-y-2">
            {plan.human_decisions_applied.map((applied) => (
              <li key={applied.decision_id} className="text-sm">
                <Mono>{applied.decision_id}</Mono>
                <span className="mt-0.5 block text-ink">{applied.how_it_changed_the_plan}</span>
              </li>
            ))}
          </ul>
        )}
      </Panel>

      <Panel title="Not addressed by any step">
        {plan.unaddressed_with_reason.length === 0 ? (
          <EmptyState>Every affected file is addressed by a step.</EmptyState>
        ) : (
          <ul className="space-y-1.5">
            {plan.unaddressed_with_reason.map((file) => (
              <li key={file.path} className="text-sm">
                <Mono>{file.path}</Mono>
                <span className="mt-0.5 block text-xs text-risk-medium">{file.reason}</span>
              </li>
            ))}
          </ul>
        )}
      </Panel>

      {plan.mitigations.length > 0 && (
        <Panel title="Mitigations">
          <ul className="space-y-0.5">
            {plan.mitigations.map((mitigation) => (
              <li key={mitigation} className="text-sm text-ink-muted">
                — {mitigation}
              </li>
            ))}
          </ul>
        </Panel>
      )}

      <Panel
        title={
          validation === null
            ? "Validation"
            : `Validation — ${passedCount} of ${validation.outcomes.length} checks passed, attempt ${validation.attempt}`
        }
      >
        {validation === null ? (
          <EmptyState>The plan was not validated.</EmptyState>
        ) : (
          <ul className="space-y-1.5">
            {validation.outcomes.map((outcome) => (
              <li key={outcome.check_id} className="flex items-start gap-2 text-sm">
                <LevelBadge level={outcome.passed ? "low" : "high"}>
                  {outcome.passed ? "pass" : "fail"}
                </LevelBadge>
                <div className="min-w-0">
                  <span className="font-medium">{outcome.check_id.replace(/_/g, " ")}</span>
                  <span className="mt-0.5 block text-xs text-ink-muted">{outcome.detail}</span>
                  {outcome.offenders.length > 0 && (
                    <span className="mt-0.5 block text-xs text-risk-high">
                      {outcome.offenders.join(", ")}
                    </span>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </Panel>
    </div>
  );
}
```

- [ ] **Step 6: Write `CodeTab`**

```tsx
/**
 * Affected files and the cited lines inside them.
 *
 * **This is existing code, not a proposed patch**, and the tab says so where a
 * reader cannot miss it. `MigrationStep` carries no patch field and
 * `validate_plan` has no check that a patch parses or applies, so a diff view
 * here would render LLM-authored code with nothing verifying it — the
 * strongest available form of what rule 1 forbids. Cited usage sites are
 * cheap, fully resolvable, and true.
 */

import type { FinalReport } from "../../api/types";
import { EmptyState, LevelBadge, Mono, Panel } from "../ui";

export function CodeTab({ report }: { report: FinalReport }) {
  if (report.affected_files.length === 0) {
    return (
      <Panel title="Code">
        <EmptyState>No affected files were found in this repository.</EmptyState>
      </Panel>
    );
  }

  return (
    <div className="space-y-4">
      <p className="text-xs text-ink-faint">
        Existing code at the cited usage sites, read from the analyzed commit. These are not
        proposed changes — no patch is generated, so none is shown.
      </p>

      {report.affected_files.map((file) => (
        <Panel
          key={file.path}
          title={file.path}
          action={
            <span className="flex items-center gap-2 text-[11px] text-ink-faint">
              {file.is_test && (
                <span className="rounded border border-edge px-1.5 py-0.5">test file</span>
              )}
              {file.commit_count !== null && <span>{file.commit_count} commits</span>}
              <span>
                {file.usage_sites.length} site{file.usage_sites.length === 1 ? "" : "s"}
              </span>
            </span>
          }
        >
          <ul className="space-y-2">
            {file.usage_sites.map((site) => (
              <li key={`${site.line}:${site.column}:${site.symbol}`}>
                <div className="flex flex-wrap items-baseline gap-2 text-sm">
                  <Mono>
                    {site.line}:{site.column}
                  </Mono>
                  <span className="font-medium">{site.symbol}</span>
                  <span className="text-xs text-ink-muted">{site.kind.replace(/_/g, " ")}</span>
                  <LevelBadge level={site.confidence}>{site.confidence}</LevelBadge>
                </div>
                {site.snippet !== null && (
                  <pre className="mt-1 overflow-x-auto rounded bg-surface-sunken p-2 font-mono text-[12px] text-ink-muted">
                    {site.snippet}
                  </pre>
                )}
              </li>
            ))}
          </ul>
        </Panel>
      ))}
    </div>
  );
}
```

- [ ] **Step 7: Run the tests and the typecheck**

```bash
cd frontend && npm test -- --run && npx tsc -b
```

Expected: 12 new tests passing, 131 in total, `tsc -b` silent.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/report
git commit -m "feat(ui): the evidence, plan and code tabs

PlanTab carries the two outputs the design pack had nowhere to put.
\`human_decisions_applied\` with how_it_changed_the_plan is how "the human
decision provably changes downstream generation" gets shown to a user rather
than asserted in a test, which makes it the most load-bearing panel in the
report. \`unaddressed_with_reason\` is not behind a disclosure -- bad news is
not detail. All ten checks are listed with their failures named and their
offenders, because validation never silently passes and so the report never
silently omits a failure.

The Code tab says on its face that it shows existing code, not a proposed
patch. MigrationStep carries no patch and validate_plan has no check that one
parses or applies, so a diff view would render LLM-authored code with nothing
verifying it -- the strongest available form of what rule 1 forbids.

Repository evidence is ordered before retrieved documents, which is a claim
about weight rather than layout: a file and a line is a fact about this
repository, a retrieved document is context that may not apply to it."
```

---

## Task 14: `ErrorView` — retry, and the resume that is not a restart

`orphaned` is the status the whole derived-status ladder exists for (ADR-001:410, spec §12 item 14): a checkpoint that outlived its process. The alternative it replaces is a spinner that never resolves. Its view must make clear that the work already done survives, and that resuming continues rather than restarts.

**Files:**
- Create: `frontend/src/components/ErrorView.tsx`
- Test: `frontend/src/components/ErrorView.test.tsx`
- Modify: `frontend/src/App.tsx`

**Interfaces:**
- Consumes: `resumeRun`, `ApiFailure` from `api/client`; `RunSnapshot`, `ApiError` from `api/types`; `stepStates` from `derive/steps`.
- Produces: `ErrorView({ snapshot, pollError, onRetry, onResumed }: { snapshot: RunSnapshot | null; pollError: ApiError | null; onRetry: () => void; onResumed: () => void })`.

- [ ] **Step 1: Write the failing test**

```tsx
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import { aSnapshot } from "../test/fixtures";
import { server } from "../test/server";
import { ErrorView } from "./ErrorView";

const RESUME = "http://localhost/api/agent/resume";

describe("ErrorView", () => {
  it("offers a resume, not a restart, for an orphaned run", () => {
    // The distinction is the whole point. Offering "start again" would discard
    // a checkpoint that survived and bill for the work a second time.
    render(
      <ErrorView
        snapshot={aSnapshot({ status: "orphaned", completed_steps: ["analyze_repo", "inspect_dependency"] })}
        pollError={null}
        onRetry={() => {}}
        onResumed={() => {}}
      />,
    );

    expect(screen.getByRole("button", { name: /resume/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /start again|restart/i })).not.toBeInTheDocument();
  });

  it("says what survived the interruption", () => {
    render(
      <ErrorView
        snapshot={aSnapshot({ status: "orphaned", completed_steps: ["analyze_repo", "inspect_dependency"] })}
        pollError={null}
        onRetry={() => {}}
        onResumed={() => {}}
      />,
    );

    expect(screen.getByText(/2 of 8 steps/i)).toBeInTheDocument();
    expect(screen.getByText(/continues from where it stopped/i)).toBeInTheDocument();
  });

  it("resumes without a decision, because an abandoned run is not waiting for one", async () => {
    // Spec 9.1: asking the client to invent a decision for an orphaned run
    // would be asking for a lie.
    const user = userEvent.setup();
    let body: { thread_id: string; decision: unknown } | null = null;
    server.use(
      http.post(RESUME, async ({ request }) => {
        body = (await request.json()) as typeof body;
        return HttpResponse.json(
          { thread_id: "t-1", status: "running", poll_url: "/api/agent/status/t-1" },
          { status: 202 },
        );
      }),
    );
    render(
      <ErrorView
        snapshot={aSnapshot({ status: "orphaned" })}
        pollError={null}
        onRetry={() => {}}
        onResumed={() => {}}
      />,
    );

    await user.click(screen.getByRole("button", { name: /resume/i }));

    await waitFor(() => expect(body).not.toBeNull());
    expect(body!.decision).toBeNull();
  });

  it("offers a new run for a failed one, and shows the recorded errors", () => {
    const onRetry = vi.fn();
    render(
      <ErrorView
        snapshot={aSnapshot({
          status: "failed",
          errors: [
            { code: "repo_unavailable", message: "The repository could not be cloned.", retryable: true, node: "analyze_repo" },
          ],
        })}
        pollError={null}
        onRetry={onRetry}
        onResumed={() => {}}
      />,
    );

    expect(screen.getByText(/repository could not be cloned/i)).toBeInTheDocument();
    expect(screen.getByText("analyze_repo")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /new run/i })).toBeInTheDocument();
  });

  it("shows a poll error when there is no snapshot to describe", () => {
    render(
      <ErrorView
        snapshot={null}
        pollError={{ code: "thread_not_found", message: "No run with that id exists.", retryable: false, node: null }}
        onRetry={() => {}}
        onResumed={() => {}}
      />,
    );

    expect(screen.getByText(/no run with that id exists/i)).toBeInTheDocument();
  });

  it("reports a failed resume rather than appearing to succeed", async () => {
    const user = userEvent.setup();
    server.use(
      http.post(RESUME, () =>
        HttpResponse.json(
          { error: { code: "thread_not_awaiting_input", message: "That run has already completed.", retryable: false, node: null } },
          { status: 409 },
        ),
      ),
    );
    render(
      <ErrorView
        snapshot={aSnapshot({ status: "orphaned" })}
        pollError={null}
        onRetry={() => {}}
        onResumed={() => {}}
      />,
    );

    await user.click(screen.getByRole("button", { name: /resume/i }));

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(/already completed/i),
    );
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd frontend && npm test -- --run src/components/ErrorView.test.tsx
```

Expected: FAIL — `Failed to resolve import "./ErrorView"`.

- [ ] **Step 3: Write the implementation**

```tsx
/**
 * The `failed` and `orphaned` views.
 *
 * `orphaned` is the reason the derived-status ladder exists at all
 * (ADR-001:410): a checkpoint that outlived its process, which a spinner
 * cannot represent and which the design pack gave no view. The wording matters
 * as much as the button — the user needs to know the work already done
 * survived, and that resuming *continues* rather than restarts, because
 * offering "start again" would discard a live checkpoint and bill for the same
 * work twice.
 *
 * The resume carries no decision. Spec §9.1: an abandoned run is not waiting
 * for an answer, and asking the client to invent one would be asking for a lie.
 */

import { AlertTriangle, RotateCcw } from "lucide-react";
import { useState } from "react";

import { ApiFailure, resumeRun } from "../api/client";
import type { ApiError, RunSnapshot } from "../api/types";
import { Mono, Panel } from "./ui";

export function ErrorView({
  snapshot,
  pollError,
  onRetry,
  onResumed,
}: {
  snapshot: RunSnapshot | null;
  pollError: ApiError | null;
  onRetry: () => void;
  onResumed: () => void;
}) {
  const [resuming, setResuming] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  const orphaned = snapshot?.status === "orphaned";
  const errors = snapshot?.errors ?? (pollError === null ? [] : [pollError]);
  const done = snapshot?.completed_steps.length ?? 0;

  async function resume() {
    if (snapshot === null || resuming) return;
    setResuming(true);
    setProblem(null);
    try {
      // No decision: this run is not waiting for an answer, it is waiting for
      // a process.
      await resumeRun({ thread_id: snapshot.thread_id, decision: null });
      onResumed();
    } catch (error) {
      setProblem(
        error instanceof ApiFailure ? error.error.message : "The backend is unreachable.",
      );
      setResuming(false);
    }
  }

  return (
    <div className="space-y-4">
      <Panel title={orphaned ? "Interrupted by a restart" : "This run failed"}>
        <div className="flex items-start gap-3">
          <AlertTriangle
            className={`mt-0.5 size-5 shrink-0 ${orphaned ? "text-risk-medium" : "text-risk-high"}`}
            aria-hidden
          />
          <div className="min-w-0 space-y-2">
            {orphaned ? (
              <>
                <p className="text-sm">
                  The process running this migration is gone, but its checkpoint survived.{" "}
                  <span className="font-medium">{done} of 8 steps</span> are already recorded.
                </p>
                <p className="text-sm text-ink-muted">
                  Resuming continues from where it stopped — it does not re-run the work already
                  done, and it does not charge for it again.
                </p>
              </>
            ) : (
              <p className="text-sm">
                The run stopped before producing a report. What it established up to that point is
                in the agent trace.
              </p>
            )}

            {errors.length > 0 && (
              <ul className="space-y-1.5">
                {errors.map((error, index) => (
                  <li key={`${error.code}-${index}`} className="text-sm">
                    <span className="text-risk-high">{error.message}</span>
                    <span className="mt-0.5 block text-[11px] text-ink-faint">
                      <Mono>{error.code}</Mono>
                      {error.node !== null && (
                        <>
                          {" · "}
                          <Mono>{error.node}</Mono>
                        </>
                      )}
                      {error.retryable && " · retryable"}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>

        {problem !== null && (
          <p
            role="alert"
            className="mt-3 rounded-md border border-risk-high/50 bg-risk-high/10 px-3 py-2 text-sm text-risk-high"
          >
            {problem}
          </p>
        )}

        <div className="mt-4 flex gap-2">
          {orphaned && (
            <button
              type="button"
              onClick={resume}
              disabled={resuming}
              className="flex items-center gap-1.5 rounded-md border border-edge-strong bg-surface-raised px-3 py-2 text-sm font-medium disabled:opacity-50"
            >
              <RotateCcw className="size-4" aria-hidden />
              {resuming ? "Resuming…" : "Resume from checkpoint"}
            </button>
          )}
          <button
            type="button"
            onClick={onRetry}
            className="rounded-md border border-edge px-3 py-2 text-sm text-ink-muted hover:text-ink"
          >
            Configure a new run
          </button>
        </div>
      </Panel>
    </div>
  );
}
```

- [ ] **Step 4: Complete `App.tsx`**

The final wiring. Every view is real; the timeline renders above whichever one status selected.

```tsx
        {view === "report" && snapshot !== null && <ReportView snapshot={snapshot} />}
        {view === "error" && (
          <ErrorView
            snapshot={snapshot}
            pollError={error}
            onRetry={() => setThreadId(null)}
            onResumed={() => undefined}
          />
        )}
```

Remove the "arrives in a later task" placeholder `Panel` entirely — every branch of `viewFor` now has a component.

- [ ] **Step 5: Run the whole suite and the typecheck**

```bash
cd frontend && npm test -- --run && npx tsc -b && npm run build
```

Expected: 137 tests passing, `tsc -b` silent, and a clean production build.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/ErrorView.tsx frontend/src/components/ErrorView.test.tsx frontend/src/App.tsx
git commit -m "feat(ui): the orphan view the architecture was shaped to make possible

\`orphaned\` is why the derived-status ladder exists (ADR-001:410) -- a
checkpoint that outlived its process, which a spinner cannot represent and
which the design pack gave no view at all. So the wording carries as much
weight as the button: the user is told the work already done survived, how much
of it there is, and that resuming continues rather than restarts. Offering
"start again" would discard a live checkpoint and charge for the same work
twice.

The resume carries no decision, because an abandoned run is not waiting for an
answer -- spec 9.1 refuses to ask the client to invent one, and this view is
the client that would have had to.

Every branch of viewFor now has a component; the placeholder panel is gone."
```

---

## Task 15: Demonstrate the exit criteria

Rules 9–11: a phase is done when its exit criteria are demonstrably met, not when the code exists. Phase 10's exit is *the full journey is usable in the browser and the workflow never appears complete while waiting for input.* That second clause is a claim about a running system, so this task drives one.

**Files:**
- Modify: `PLANNING.md`
- Modify: `docs/adr/ADR-001-system-architecture.md`

- [ ] **Step 1: Verify both gate sets are clean**

```bash
cd backend && .venv/bin/python -m pytest -q 2>&1 | tail -2 \
  && .venv/bin/python -m mypy 2>&1 | tail -1 \
  && .venv/bin/python -m ruff check && .venv/bin/python -m ruff format --check
cd ../frontend && npm test -- --run 2>&1 | tail -5 && npx tsc -b && npm run build 2>&1 | tail -3
```

Record the actual numbers. The backend baseline is 1164 passed / 8 skipped; the frontend should report 137 passing across 13 files.

- [ ] **Step 2: Confirm the generated types are current**

A stale `schema.d.ts` is a frontend typechecking against a contract the backend no longer serves — the exact bug class `openapi-typescript` was adopted to remove, so it needs a check rather than a habit.

```bash
cd backend && .venv/bin/python scripts/dump_openapi.py
cd ../frontend && npm run gen:api
cd .. && git diff --stat frontend/src/api/
```

Expected: no diff. A diff means the contract moved and the frontend has not caught up — commit the regenerated files and re-run the frontend gates before continuing.

- [ ] **Step 3: Drive the full journey in a browser**

Start both processes, then walk the whole path against the fixture repository, recording what each state actually showed.

```bash
cd backend && ./.venv/bin/python -m uvicorn upgradepilot.api.app:create_app --factory --port 8000 &
cd frontend && npm run dev
```

At http://localhost:5173, confirm each of the following and write down the real values:

1. **Configuration** — the form shows six fields and no model, temperature, or additional-context input. Submit with a local path pointing at `backend/tests/fixtures/sample_repo`, dependency `pydantic`, `1.10.13` → `2.9.2`, zero-downtime on, and a deadline.
2. **Activity** — the timeline advances, the trace fills, affected files and breaking changes appear as they are established, and telemetry shows a rising token count. The top bar reads *Live · 1s poll* and never "streaming".
3. **The load-bearing check** — when the run reaches `awaiting_human`: the decision panel is the dominant surface, the timeline above it shows Human Review as *waiting for you*, and **Migration Plan, Validation and Report all still read `pending`**. Screenshot this state; it is the one Phase 10's exit criterion names.
4. **The guard** — press Submit twice quickly. The second press does nothing. Then, in a second browser tab open on the same run, submit an answer to the same question and confirm it reports *This question has already been answered*.
5. **Report** — five tabs, no PR draft tab. Confirm the factor table opens to per-factor evidence, confidence renders with its ceilings, the Plan tab shows *what your decision changed*, the validation list shows all ten checks, and the Code tab labels itself existing code.
6. **Orphan** — with the run mid-flight, stop the backend, restart it, and reload. The status must read *Interrupted by a restart* with a resume button, not a spinner. Resume and confirm it continues rather than restarting, by checking the completed step count does not reset.
7. **Accessibility** — tab through the decision panel using only the keyboard and submit an answer without a mouse.

- [ ] **Step 4: Update `PLANNING.md`**

Tick all thirteen Phase 10 items. Write the exit paragraph in the house style the earlier phases use: what was demonstrated, with the real figures from Step 3, and what was learned. Record at minimum:

- The four defects or surprises found while building, each with what it would have produced if shipped.
- The decisions that changed shape during the build, if any, and why.
- The real numbers: test counts for both suites, the token/cost figures the telemetry panel showed, the validation check tally.
- Any limitation Phase 11 or 12 inherits.

- [ ] **Step 5: Record the frontend dependencies in ADR-001**

The six dev dependencies added in Task 1, with their resolved versions and the one-line reason each was admitted (rule 12, rule 13). If any resolved to a version whose behaviour differed from what this plan assumed — notably MSW's handler API or Vitest's fake-timer semantics — record that as a verification note, because the next reader will otherwise rediscover it.

- [ ] **Step 6: Commit**

```bash
git add PLANNING.md docs/adr/ADR-001-system-architecture.md
git commit -m "docs: Phase 10 complete, with the journey demonstrated rather than asserted

Exit criteria met and recorded with real figures: the full path driven in a
browser, the awaiting_human state showing Migration Plan, Validation and Report
all still pending above a decision panel, the duplicate-submit guard defeated
in one tab and caught by the server's 409 in a second, and an orphaned run
resumed from its checkpoint without resetting its completed step count.

Frontend dependency versions recorded in ADR-001 per rule 13."
```

---

## Self-Review

Run against the plan as written.

**1. Spec coverage.** Every Phase 10 checklist item maps to a task: `openapi-typescript` (1), `useRunPolling` (6), status-derived routing (2, 8, 14), configuration form with inline 422 (9), activity timeline with expandable steps (10), evidence panel with relevance and source references (10, 13), Human Review above an incomplete timeline with the triple guard (7, 11), report across risk/confidence/affected files/breaking changes/evidence/plan/mitigations/decisions (12, 13), persistent metrics with estimated and pricing-unknown flags (4, 10), agent trace drawer (10), error and orphan views with retry/resume (14), semantic tokens and `aria-live` (7, 8), tests for the polling hook with MSW and the Human Review panel (6, 11). Spec §10's "no form library" and "one route" are Global Constraints and are asserted by tests in Tasks 9 and 12.

**2. Placeholders.** Three deliberate forward references, each naming its resolving task rather than trailing off: `test/fixtures.ts` is created in Task 3 and extended in Tasks 11 and 12; the three report tabs are stubbed in Task 12 and written in Task 13; `remember` is bound in Task 8 and used in Task 9. Two places instruct the implementer to read the generated schema rather than trust the plan's literal — the `EvidenceRef` union in Task 11 and `RagContext.sources` in Task 13 — because the generated type is the authority and guessing there is exactly the drift this phase spent a dependency to prevent.

**3. Type consistency.** `RunSnapshot`, `UsageView`, `InterruptPayload`, `FinalReport`, `RiskFactor` and `ApiError` are used with the same names throughout, all sourced from `api/types`. `viewFor` returns `View` and is consumed only in Tasks 8, 9, 11, 14. `stepStates` returns `Step[]` and is consumed only by `WorkflowTimeline` and `ErrorView`. `costLabel` returns `CostLabel` and is consumed only by `RunMetrics`. `selectedSourceIds` is defined in `EvidencePanel.tsx` (Task 10) and reused in `EvidenceTab` (Task 13); `EvidenceRefList` is defined in `RiskFactorsTab.tsx` (Task 12) and reused in `PlanTab` (Task 13). `SessionRun` is defined in `useSessionRuns.ts` and consumed by `TopBar`, `LeftSidebar` and `ConfigurationForm`.

**4. Running test count.** 3 + 7 + 10 + 7 + 7 + 14 + 5 + 5 + 6 + 12 + 6 + 5 + 14 + 6 + 12 + 12 + 6 = **137** across 13 files. Each task states its own expected total, so a divergence is caught at the task that caused it rather than at the end.
