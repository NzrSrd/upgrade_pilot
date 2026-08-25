# UpgradePilot UI Component Architecture

The vocabulary here is spec §10's, which is also the vocabulary
`PLANNING.md` Phase 10's checklist is written in. An earlier version of this
file named about thirty components of which one matched the spec's seven;
that mismatch is resolved in the spec's favour. See `DESIGN.md` §Amendments,
item 3.

Visual design, workflow states and interaction rules are in `DESIGN.md`.
This file is the React structure.

---

## Tree

```
App
│
├── AppShell
│   │
│   ├── TopBar
│   │   ├── ProductMark
│   │   ├── RunSummary              repo · dependency · versions
│   │   ├── StatusPill              aria-live="polite"
│   │   └── TraceDrawerTrigger
│   │
│   ├── LeftSidebar
│   │   ├── NewRunButton
│   │   ├── SessionRuns             this tab's runs; sessionStorage
│   │   │   └── SessionRunItem
│   │   ├── ConfigSummary           the submitted RunInput, read-only
│   │   └── IntegrationStatus       knowledge base + LLM, from /api/health
│   │
│   ├── MainWorkspace
│   │   ├── WorkflowTimeline        always rendered while a run exists
│   │   │   └── WorkflowStep        8 steps × 6 states
│   │   │
│   │   └── ⟨one view, derived from status⟩
│   │       ├── ConfigurationForm       idle
│   │       ├── ActivityTimeline        queued · running
│   │       │   └── ActivityEvent
│   │       ├── HumanReviewPanel        awaiting_human
│   │       │   ├── DecisionQuestion
│   │       │   ├── DecisionOptionCard
│   │       │   └── UnansweredConsequence
│   │       ├── ReportView              completed · completed_with_warnings
│   │       │   ├── ReportTabs
│   │       │   ├── OverviewTab
│   │       │   │   ├── RiskVerdict         overall · aggregate · clamp floor
│   │       │   │   ├── ConfidenceWithCeilings
│   │       │   │   ├── VersionDiscrepancy  stated vs detected
│   │       │   │   └── BreakingChangeList
│   │       │   ├── RiskFactorsTab
│   │       │   │   └── RiskFactorRow       expands to its EvidenceRefs
│   │       │   ├── EvidenceTab
│   │       │   │   └── EvidencePanel
│   │       │   │       ├── RepoEvidenceItem    file · line · snippet
│   │       │   │       └── DocSourceItem       selected vs retrieved
│   │       │   ├── PlanTab
│   │       │   │   ├── PlanStep
│   │       │   │   ├── DecisionsApplied        how_it_changed_the_plan
│   │       │   │   ├── UnaddressedFiles        with reasons
│   │       │   │   └── ValidationOutcomes      all ten checks
│   │       │   └── CodeTab
│   │       │       └── AffectedFileListing
│   │       │           └── UsageSiteRow
│   │       └── ErrorView               failed · orphaned
│   │           └── RetryOrResume
│   │
│   ├── RunMetrics                  right region; survives view changes
│   │   ├── TokenCounts
│   │   ├── CostCard                estimated + pricing_complete flags
│   │   ├── ModelInUse              from usage.by_model
│   │   ├── TokensByNode
│   │   ├── GraphExecutionState
│   │   ├── RetrievedSources
│   │   └── Diagnostics
│   │
│   └── AgentTraceDrawer            over any view; observable events only
│       └── TraceEventRow
│
└── ⟨hooks and pure derivations — no JSX⟩
    ├── useRunPolling(threadId)     the only source of run state
    ├── useHealth()
    ├── useSessionRuns()
    ├── viewFor(status)
    ├── stepStates(snapshot)
    └── costLabel(usage)
```

---

## What the structure is enforcing

**One source of server state.** `useRunPolling(threadId)` is the only thing
that talks to the API about a run. Because each `RunSnapshot` is complete
rather than incremental there is no accumulation and no merge logic anywhere
in the tree — the concrete payoff of polling over SSE (ADR-001:68), and what
keeps orchestration out of React.

**Derivations are functions, not components.** `viewFor`, `stepStates` and
`costLabel` each encode a rule that must not be re-implemented per-component:
which view a status selects, which timeline steps are skipped, and how a cost
figure is allowed to be worded. As pure functions they are unit-testable
without rendering anything, and there is one copy of each.

**One view at a time, chosen by the machine.** `MainWorkspace` renders
exactly one of the five views, selected by `viewFor(snapshot.status)`. There
is no user-navigable workflow tab bar. `ReportTabs` is the only tab bar in
the application and it lives inside a report that, by construction, only
exists once the run is finished.

**The timeline outlives the view.** `WorkflowTimeline` is a sibling of the
view rather than a child of any one of them, so `HumanReviewPanel` renders
*above a still-incomplete timeline*. The workflow can never look finished
while it is waiting.

**`RunMetrics` is a region, not a panel.** It sits in `AppShell` beside
`MainWorkspace` so it stays mounted and updating while the view underneath it
changes — including after the run completes, which screenshots 05 and 06
disagree about.

**`AgentTraceDrawer` is separate from `Diagnostics`.** The trace is the
observable event log with its own disclosure rules (`CLAUDE.md` rule 26);
diagnostics is latency and internals. Different surfaces, different rules,
so different components.

---

## Status → view

The full table, with the reason each status needs its own row, is in
`DESIGN.md` §Workflow States. Summarised:

```
idle ───────────────────────────► ConfigurationForm
queued · running ───────────────► ActivityTimeline
awaiting_human ─────────────────► HumanReviewPanel  (timeline still incomplete)
completed · …_with_warnings ────► ReportView
failed · orphaned ──────────────► ErrorView
```

Eight statuses, five views. The names are the backend's own, so the mapping
is identity rather than a translation table.

---

## Canonical visual references

`docs/ui/screenshots/` — normative for **layout only**. Their content
depicts a scenario the product cannot run and contains figures no field
backs. The per-screenshot table of what not to copy is in `DESIGN.md`
§Canonical Visual References.

- `01_new_migration_run_input.png`
- `02_agent_activity_running.png`
- `03_human_in_the_loop_interrupted.png`
- `04_migration_report_overview.png`
- `05_code_changes_diff_view.png`
- `06_pull_request_draft_preview.png`
