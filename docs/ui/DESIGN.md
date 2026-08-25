# UpgradePilot UI Design

## Design Direction

UpgradePilot is a professional developer tool for dependency upgrade
risk analysis and migration planning, not a chatbot.

The interface should feel like a modern developer platform:
information-dense, technical, trustworthy, and evidence-driven.

The UI should communicate three things clearly:

1. What the user is configuring.
2. What the agent is currently doing.
3. Why the agent reached its conclusions.

Do not optimize the interface for conversational chat.

---

## The rule this document is subordinate to

`CLAUDE.md` rule 1: every claim the product makes must trace to a real line
of code or a real corpus document.

For the UI that has a specific and unforgiving consequence: **a number on
screen must name the field it came from.** A figure that exists only in a
mockup is a fabricated finding the moment someone renders it. Where this
document previously specified such figures, they have been removed rather
than derived — see *Amendments* at the end.

The authoritative field list is `backend/src/upgradepilot/api/schemas.py`
(`RunSnapshot`) and the models it composes. If a panel described here has no
field behind it, the panel is wrong, not the backend.

---

## Canonical Visual References

The screenshots in `docs/ui/screenshots/` are the canonical reference for
**layout, density, and visual hierarchy only.**

**Their content is not normative.** Every screenshot depicts a React
17 → 18 JavaScript migration; the product analyzes Python via `ast` and its
corpus is Pydantic primary sources, so the demo target throughout
`PLANNING.md` is Pydantic v1 → v2. The screenshots also contain figures no
field backs, arithmetic that does not add up, transposed token labels, a
duplicated step name, and a run pill reading ACTIVE beside a finished
report — all catalogued in `READINESS.md` §2 and §4.

Read them for the shape of the thing. Do not copy a number, a label, or a
scenario out of them.

| Screenshot | Normative for | Do not copy |
|---|---|---|
| `01_new_migration_run_input.png` | Form layout, sidebar composition, section rhythm | The GitHub slug field, the dependency dropdown, the provider/temperature controls, "Additional Context", the constraint checkboxes |
| `02_agent_activity_running.png` | Activity + telemetry layout, stepper treatment | The `● Streaming` badge, the transposed Input/Output token labels, the node ids |
| `03_human_in_the_loop_interrupted.png` | Decision-panel prominence, option-card shape | The duplicated token value, the six-step stepper, "Assessment Assessment" |
| `04_migration_report_overview.png` | Report layout, card grid | Migration Complexity, Grade, Estimated Duration, the impact donut, "Potential Issues" |
| `05_code_changes_diff_view.png` | Dense code-listing layout | The unified diff, the split/unified toggle, the `+12 −6` hunks |
| `06_pull_request_draft_preview.png` | Nothing in Spec 1 | The entire surface — writing to GitHub is sub-project 2 |

---

## Layout

UpgradePilot uses a three-region developer-console layout, plus a drawer.

### 1. Left Sidebar

Persistent navigation and configuration summary:

- New migration run
- This session's runs
- Configuration summary (repository, dependency, versions, constraints)
- Knowledge base status
- LLM configuration status

The sidebar remains visually stable while the main workspace changes,
including after a run completes.

Two things it does **not** contain. There is no historical run list —
listing past runs requires the Postgres run registry, which is sub-project
3; the sidebar carries the runs *this browser session* started, labelled as
such. And there are no model or temperature controls: configuration lives in
environment variables via `pydantic-settings` (`CLAUDE.md` rule 14) and the
API exposes no configuration endpoint. The model in use is reported in the
telemetry region, taken from `UsageSummary.by_model` — that is, from calls
that actually happened.

### 2. Main Workspace

Whichever view the status table below selects. Never two, never a tab bar
of workflow views.

### 3. Right Telemetry Sidebar

Execution observability, visible during a run **and after it completes**:

- Token usage — input, output, total
- Estimated cost, with its two honesty flags
- LLM call count
- Model in use
- Tokens by node
- Recorded span — the interval between the first and last recorded trace
  event, not wall-clock time since the run started; see the Telemetry
  section below for why
- Graph execution state

Two items an earlier version of this list implied belong here and do not:

- **Retrieved sources** are not a sidebar panel. They render in the
  Activity view (`EvidencePanel`, scoped to the run in progress) and, once a
  run completes, in the report's Evidence tab — not in this region.
- **Diagnostics** — latency, internals — is deferred. Nothing computes it,
  Phase 10 does not implement it, and no field backs it. It is named as out
  of scope here rather than left looking settled, the same treatment the PR
  Draft tab gets below.

Telemetry earns a region rather than a block inside the left sidebar because
token and cost tracking is a graded capability, not a diagnostic: it has to
stay visible and updating while the main workspace changes underneath it.

### 4. Agent Trace Drawer

Triggered from the top bar, over any view. Its disclosure rules are strict
and are given their own section below. Diagnostics — latency, internals —
is a telemetry concern and a different surface.

---

## Workflow States

The main workspace is state-driven. The view derives from
`RunSnapshot.status` and from nothing else. The names below are the
backend's own, so the mapping is identity rather than a translation table.

| `status` | View |
|---|---|
| `idle` (no run started) | `ConfigurationForm` |
| `queued` | `ActivityTimeline`, queued state |
| `running` | `ActivityTimeline` |
| `awaiting_human` | `HumanReviewPanel`, above a still-visible, still-incomplete timeline |
| `completed` | `ReportView` |
| `completed_with_warnings` | `ReportView`, with failed validation checks surfaced |
| `failed` | `ErrorView`, with retry |
| `orphaned` | `ErrorView`, with resume-from-checkpoint |

`orphaned` is the status this table exists for. A run whose checkpoint
outlived its process is precisely the case a spinner cannot represent, and a
design that gives it no view ships the hanging spinner the architecture was
shaped to avoid.

There is no `INTERRUPTED` state and no `NEW` state; those were this
document's names for `awaiting_human` and `idle`.

---

## Workflow Progress

Eight steps, labelled for a user but derived from the real node ids in
`RunSnapshot.current_step` / `completed_steps`:

| Node id | Step label |
|---|---|
| `analyze_repo` | Repository Analysis |
| `inspect_dependency` | Dependency Analysis |
| `agentic_rag` | Evidence Retrieval |
| `assess_risk` | Risk Assessment |
| `human_review` | Human Review |
| `generate_plan` | Migration Plan |
| `validate_plan` | Validation |
| `finalize` | Report |

`finalize` is a step because it is a real node, and because completion is
read from the `final_report` it sets — an earlier seven-step stepper omitted
the one node whose output defines the end of the run.

Each step communicates one of six states:

- Pending
- Running
- Completed
- **Skipped**
- Waiting for user
- Failed

**Skipped exists for `human_review`.** When the user's constraints already
settle the choice, no interrupt fires and the trace records "resolved by
constraints, no human input required". Without a skipped state a correct run
looks like it lost a step.

The stepper shows these eight user-facing steps. The retrieval subgraph's
nodes run several times each and do not appear here; a progress list that
grew to fourteen entries for a three-round loop would read as a stalled run
rather than a working one. That detail belongs in the Agent Trace drawer.

---

## Visual Language

- Dark developer-tool interface
- Dense but readable information layout
- Subtle borders
- Rounded cards
- Strong information hierarchy
- Minimal decorative elements
- Semantic status colors
- Monospace typography for file paths, line numbers, symbols, ids, token counts
- Consistent spacing and alignment
- High contrast for important warnings and decisions

The design should feel closer to a professional engineering platform
than a consumer AI application.

---

## Semantic Status Colors

Four tokens, defined in the Tailwind theme, never raw colors at call sites:

| Token | Meaning |
|---|---|
| `risk-high` | High-risk findings, failed steps, failed validation checks, blocking issues |
| `risk-medium` | Medium-risk findings |
| `risk-low` | Low-risk findings, completed steps, passing checks, healthy integrations |
| `pending-input` | The run is waiting for a human |

`pending-input` is a separate token from `risk-medium` deliberately. An
earlier version of this document folded "human decisions" and "medium-risk
findings" both into a single Warning color, which is exactly the collision
these two tokens exist to prevent: "we need your answer" and "this is
moderately risky" are different messages and must not share a color.

Neutral — pending steps, metadata, secondary information, diagnostics —
uses the surface and text scales, not a status token.

Do not use status colors decoratively.

---

## Accessibility

- The status region carries `aria-live="polite"`, so the transition into
  Human Review is announced rather than merely rendered.
- Every control is keyboard reachable and has an accessible label. The
  decision options are a radio group, not clickable divs.
- Status is never communicated by color alone: a level always has a word or
  an icon beside it.
- Contrast meets WCAG AA against both surface scales.

---

## Risk Presentation

Risk must never be presented as an unsupported LLM opinion. The LLM does not
produce a level; levels come from the threshold table (`CLAUDE.md` rule 19).

The report distinguishes:

- **Overall risk** — `RiskAnalysis.overall_risk`
- **Aggregate risk** — `RiskAnalysis.aggregate_risk`, the value before the
  clamp
- **Clamp floor** — `RiskAnalysis.clamp_floor`, rendered whenever it differs
  from the aggregate, so a clamped verdict says it was clamped
- **Confidence** — `RiskAnalysis.confidence`
- **Confidence ceilings** — `RiskAnalysis.confidence_ceilings`, each with its
  reason
- **The seven risk factors** — `RiskAnalysis.factors`
- **Affected files** and **breaking changes**

### The factor table is the centrepiece

`RiskAnalysis.factors` is a first-class table in the report, not a
footnote. Each row shows the factor's name, its category, its level, its
weight, its detail, and — expandable — the `EvidenceRef`s it cites. Seven
factors, each computed from a documented threshold table without an LLM,
each carrying its own evidence: this is the product's core honesty artifact
and the reason a developer should believe the verdict.

An earlier version of this document surfaced a letter grade, a complexity
score out of ten and a donut chart instead, none of which had a field behind
them. See *Amendments*.

### Confidence renders with its reason or not at all

A confidence figure alone is the least useful honest number in the product.
`ConfidenceCeiling` carries `reason` and `ceiling`; both render beside the
value:

> Confidence 30% — capped: no supporting evidence was retrieved

The ceilings are: no evidence available, more than 10% of files skipped, a
`TRANSITIVE_ONLY` dependency role, and a high-confidence symbol with no
documented evidence. Each is a sentence the user can act on. A gradient bar
showing 88% is not.

---

## Evidence Presentation

RAG evidence is inspectable. A source communicates:

- Document title — `SourceRef.title`
- Source type — `SourceRef.source_type`
- Vector similarity — `SourceRef.relevance`, **labelled as what it is**
- Reference or URL — `SourceRef.url_or_reference`
- Chunk — `SourceRef.chunk_id`
- Whether it was *selected*, not merely *retrieved*

That last distinction is the point. The UI must never imply that a document
is relevant because vector search returned it. `relevance` is a similarity
score; the `sources_selected` trace event is what says the agent actually
used it. Retrieved-but-not-selected sources are shown as such.

Evidence is supporting information, not a replacement for repository
analysis. Repository evidence — a file and a line — outranks a retrieved
document, and the report orders them that way.

---

## Agent Activity

The activity view shows observable execution events: repository analysis
started, dependency detected, query issued, sources retrieved, sources
selected, retrieval evaluated, risk assessment generated, human review
requested, decision applied, plan generated, validation outcome recorded,
error recorded.

Activity is **live by polling** — a complete snapshot every second while the
run is non-terminal. There is no streaming, no SSE, and no `● Streaming`
badge; the badge in screenshot 02 contradicts ADR-001 A3, which defers SSE
explicitly. The payoff of polling is that each snapshot is complete, so the
client needs no merge logic; the honest label for it is *live · 1s poll*.

Do not expose:

- Internal prompts
- Hidden chain-of-thought
- Private reasoning
- Secrets or credentials
- Repository contents beyond the cited lines

---

## Human-in-the-Loop

The HITL state is one of the primary interactions of the application.

When the graph invokes `interrupt()`:

1. The timeline shows Human Review as waiting, with later steps still
   incomplete.
2. `HumanReviewPanel` renders above that timeline.
3. The user sees the question, the evidence, and every option's trade-offs.
4. The user explicitly selects an option.
5. The frontend submits it with the thread id.
6. The graph resumes; the view returns to Activity on the next poll.

The panel renders, from `InterruptPayload`:

- `question`, and `reason` — why this is being asked
- `evidence` — the refs behind the question
- `options`, each with `label`, `summary`, `risk_level`, `effort`,
  `downtime`, and its `consequences`
- `recommendation_id`, marked as a recommendation and not preselected
- **`consequences_if_unanswered`** — what happens if the user walks away.
  This is carried on every payload and is more useful than showing the user
  their own constraints back to them, which is what screenshot 03 does with
  the space.
- `validation_error`, when a previous answer was rejected

### Four decision kinds, and more than one interrupt

`InterruptPayload.kind` is one of `strategy_choice`, `risk_acceptance`,
`scope_tradeoff`, `discrepancy_resolution`. All four get the same panel
shape — question, evidence, option cards — because all four are the same
interaction; the kind sets the heading and the framing, not the layout.

`human_decisions` is an append channel precisely so interrupts can fire in
sequence. A second question must not look like a bug: answered decisions
remain visible in the timeline and the report, and the panel shows which
question of how many this is.

### The interrupt is never simulated

The interruption originates from the backend workflow. The frontend has no
code path that produces a decision panel without an `awaiting_human`
snapshot carrying a `pending_decision`.

### Duplicate submission is blocked three ways

The button is disabled until an option is selected and while a submission is
in flight; a local `submitting` flag is set before the request and is not
cleared on success; and the server answers 409. The last is the only real
guarantee. A 409 renders as "This question has already been answered" with
the button left disabled, and the next poll moves the view on.

---

## Telemetry

Token and cost information is a first-class part of the interface. During
and after execution:

- Input, output and total tokens
- Estimated cost
- LLM call count
- Model in use, from `UsageSummary.by_model`
- Tokens by node — "where did the tokens go" is the second question a
  developer asks
- Recorded span — the interval between the first and last recorded trace
  event, not wall-clock time since the run started. The server's actual
  start time is not observable from here, and a checkpointed run can be
  resumed hours or days later, so wall-clock across a resume would be a
  number that looks authoritative and is not.
- Graph execution state

### The cost card's two flags are not optional

`UsageView` carries `estimated` and `pricing_complete`, and the cost is
unreadable without them:

| Condition | Rendering |
|---|---|
| `estimated_cost_usd` is `null` | **not priced** — never `$0.00` |
| `pricing_complete` is `false` | **≥ $0.00056**, "lower bound — some calls have no price" |
| `estimated` is `true` | a **tokens partly estimated** badge |
| otherwise | the figure, plain |

Pricing-unknown is the ordinary case rather than the edge one: the stack
resolved to OpenRouter, and a hardcoded per-1K price for a model that is not
running describes nothing. Screenshot 02's "Pricing: $0.0025 / 1K tokens /
Model: gpt-4o" is wrong twice over.

Detailed latency belongs under diagnostics.

---

## Report Structure

Five tabs. Tabs exist **inside the report and nowhere else** — workflow view
selection stays derived, so there is no navigation that lets a user walk
past an unanswered question or reach a report that does not exist yet.

### Overview

- Overall risk, aggregate risk, clamp floor when it differs
- Confidence with its ceilings and their reasons
- Counts: affected files, breaking changes, usage sites
- Executive summary — `RiskAnalysis.summary`
- Recommended strategy — `MigrationPlan.strategy_id` and `summary`
- Key breaking changes, by `Severity`
- Version discrepancy, when `FinalReport.version_discrepancy` is set: what
  the user stated against what was detected, shown side by side rather than
  silently overridden. `RepoAnalysis.version_discrepancy` is a *method*
  taking the stated version, so it never reaches JSON; it is exposed as a
  `@computed_field` on `FinalReport`, which is the one model holding both
  the stated version and the analysis. Re-deriving the comparison in
  TypeScript would be a second implementation of the rule in a language
  that cannot check it against the first.
- `TRANSITIVE_ONLY` role, when detected — the user does not control this pin
- Warnings banner when the status is `completed_with_warnings`

### Risk Factors

The seven-factor table described above, with per-factor evidence
disclosure.

### Evidence

- Repository evidence — file and line, ranked first
- Retrieved documents, selected and unselected, with type and similarity
- Which findings each source supports
- RAG stop reason and iteration count

### Plan

- `MigrationPlan.steps` in order, each with its files, its
  `rationale_evidence`, its `validation` note and its `requires_downtime`
  flag
- `mitigations`
- **`human_decisions_applied`** — each `DecisionApplication` with
  `how_it_changed_the_plan`. This is how "the human decision provably
  changes downstream generation" gets shown to a user rather than merely
  asserted in a test, and it is the single most important panel in the
  report for that claim.
- **`unaddressed_with_reason`** — affected files no step addresses, with the
  reason. A headline honesty output; it is not hidden behind a disclosure.
- The validation report: all ten checks, each with its outcome and detail,
  and **failures named with their offenders**. Validation never silently
  passes, so the report never silently omits a failure.

### Code

Affected files and the cited lines within them:
`AffectedFile.path`, and each `UsageSite` with its line, column, symbol,
`UsageKind` and confidence, plus the snippet where one was captured.

**This tab shows existing code, not generated patches.** `MigrationStep`
carries no patch field and `validate_plan` has no check that a patch parses
or applies; rendering LLM-authored code with no verification would be the
strongest available form of the thing rule 1 forbids. Screenshot 05's
unified diff is not buildable and is not being built. Cited existing code at
the usage sites is cheap, fully citable, and the honest version of that tab.

### There is no PR Draft tab

Writing to GitHub is sub-project 2. Rendering a PR body composed of plan
prose, behind a button that cannot create anything, offers a capability the
product does not have (`CLAUDE.md` rule 3).

---

## Responsive Behavior

Desktop-first. The three-region layout is optimized for desktop screens.

On smaller screens:

- Right telemetry sidebar becomes a drawer.
- Left navigation becomes collapsible.
- Main workspace remains primary.
- HITL decision cards remain fully usable.
- Tables and code listings scroll horizontally where necessary.

Do not sacrifice critical workflow information merely to fit everything into
a narrow viewport.

---

## UX Principles

### Evidence over prose

Prefer verifiable evidence over plausible AI-generated explanation. Where
the two compete for space, evidence wins.

### Transparency over complexity

Show what the agent is doing without exposing private reasoning.

### Human control

The agent recommends; explicit human decisions control interruption points.

### Progressive disclosure

Most important information first. Detail available without overwhelming the
workflow. Note the exception: `unaddressed_with_reason` and failed
validation checks are *not* progressive disclosure candidates. Bad news is
not detail.

### Developer-first

An engineering tool, not an AI chatbot.

---

## Component Architecture

The component hierarchy is `docs/ui/COMPONENTS.md`, in the vocabulary of
spec §10. This document defines visual design, behavior, workflow states,
and interaction principles. The two must remain consistent.

---

## Amendments

**2026-08-25 — the three-region layout.** Spec §10 originally specified two
regions with run metrics inside the left sidebar. It was amended to agree
with this document; telemetry is a graded capability and needs a region that
survives the main workspace changing underneath it. `READINESS.md` §1.2.

**2026-08-25 — the eight decisions `READINESS.md` §5 left open.** Resolved
here before Phase 10 began, because a builder following this document would
otherwise have produced fabricated findings. What changed, and why:

1. **Streaming removed** (§1.1). ADR-001 A3 defers SSE; Phase 10 builds
   `useRunPolling`. "Streaming agent activity" and screenshot 02's badge
   described a transport the system does not have. Now *live · 1s poll*.
2. **Backend status vocabulary, all eight statuses** (§1.3). `NEW` /
   `RUNNING` / `INTERRUPTED` / `COMPLETED` became the eight the API derives,
   so the mapping is identity. `orphaned` gained the view whose absence
   would have shipped a hanging spinner.
3. **Spec §10 component vocabulary** (§1.4, §1.5). Phase 10's checklist is
   written in it; `COMPONENTS.md`'s thirty names overlapped it once. The
   Agent Trace drawer is now a region in its own right rather than folded
   into telemetry diagnostics.
4. **Tabs inside the report only** (§1.6). Navigable workflow tabs permit
   exactly what this document forbids two sections earlier — continuing past
   an unanswered decision.
5. **Migration Complexity, Grade, Estimated Duration, the impact donut and
   "Potential Issues" deleted** (§2.1–2.4). No field, no derivation. The
   donut additionally did not sum to 100 and did not say what it counted.
   `DecisionOption.effort` legitimately backs "Estimated Effort"; duration
   has no such source. The seven-factor table replaces all of them, which is
   what should have been there.
6. **Form fields matched to the models** (§2.5–2.10). Provider, model and
   temperature controls removed — configuration is environment variables and
   there is no configuration endpoint. Dependency is a text input, not a
   dropdown: nothing enumerates a repository's manifest. Repository is
   "Remote / Local" with a URL field, not a GitHub slug with a private-repo
   toggle — authentication is sub-project 2. Constraints are exactly
   `UserConstraints`: `zero_downtime`, `minimize_effort`, `deadline`,
   `risk_tolerance`. The first two unbacked checkboxes are gone and the two
   missing fields are added, which is not cosmetic — `constraint_pressure`
   is derived partly from the deadline, and `scope_tradeoff` is unreachable
   without it. "Additional Context" removed: unbacked free prose entering
   the judgment path.
7. **Cost flags designed** (§2.11). `estimated` and `pricing_complete` now
   have specified renderings, and pricing-unknown is treated as the ordinary
   case it is.
8. **Screenshot content declared non-normative** (§2.12), with a table of
   what not to copy from each. Re-rendering against Pydantic v1 → v2 remains
   worth doing; until then, layout is normative and content is not.
9. **The Changes tab renders cited existing code** (§2.13). `MigrationStep`
   has no patch field and `validate_plan` has no patch check; Phase 8 shipped
   without either. Generated patches would need both, and adding them now
   would reopen a completed phase to build the one surface most likely to
   present unverified LLM output as fact.

**Also closed here**, all of `READINESS.md` §3 — backend output that had no
home: the factor table (§3.1), the plan tab (§3.2),
`unaddressed_with_reason` (§3.3), `completed_with_warnings` and the failed
checks (§3.4), confidence ceilings with their reasons (§3.5), the skipped
step state (§3.6), all four decision kinds and sequential interrupts (§3.7),
`consequences_if_unanswered` (§3.8), `version_discrepancy` and
`TRANSITIVE_ONLY` (§3.9), the triple duplicate-submit guard (§3.10), and the
semantic tokens plus the accessibility section this document did not
previously have (§3.11).

**One backend addition this required.** §3.9 asks the report to surface the
detected-versus-stated version, and `RepoAnalysis.version_discrepancy` is a
method taking `stated` rather than a field, so nothing about it reaches the
client. It is now a `@computed_field` on `FinalReport` — the one model
holding both halves — following exactly the reasoning Phase 9 recorded when
it made `ValidationReport.passed`, `FinalReport.completed_with_warnings`,
`RagContext.evidence_available` and `RagEvaluation.sufficient` computed
fields: a derived value the frontend cannot see is one the frontend
re-derives, which is a second implementation of the rule in a language that
cannot check it against this one. No rule changed; one already-written rule
became visible.

The screenshot defects in `READINESS.md` §4 are addressed by amendment 8
rather than individually: the images are no longer normative for content, so
a transposed token label or an unreachable status pill can no longer be
copied into the build.
