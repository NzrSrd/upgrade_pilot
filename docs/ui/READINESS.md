# UI readiness review — 2026-08-25

> **Status: closed 2026-08-25.** Every item below is resolved. §1 and §2's
> nine decisions, all of §3, and §4's screenshot defects were settled in
> `DESIGN.md` and `COMPONENTS.md` before Phase 10 began — see `DESIGN.md`
> §Amendments for what changed and why, and §5 here for the disposition of
> each blocking decision. Two items resolved differently from this review's
> own recommendation and are marked as such. This file stays as the record of
> what was wrong and what it would have cost; it is no longer a to-do list.

A review of `DESIGN.md`, `COMPONENTS.md` and the six screenshots against the
contract Phase 10 will actually build on: spec §10 (UI), §9 (API and run
lifecycle), §8 (judgment layer), ADR-001 A3, and the models in
`backend/src/upgradepilot/models/`.

The layout, the density, the developer-console direction and the "not a chatbot"
constraint are right and worth defending. What follows is everything that would
either stall Phase 10 or push a builder into producing an uncited claim.

Four broken references were fixed in the same change as this file: the screenshot
filenames in both documents used hyphens where the files use underscores,
`DESIGN.md` pointed at `docs/ui/Components.md` (wrong case — works on macOS,
breaks in CI), and `CLAUDE.md` named `docs/ui/screenshots/dashboard-reference.png`,
which does not exist.

---

## 1. Conflicts with a decision already recorded elsewhere

These are not matters of taste. Each one contradicts the spec or an ADR, so
resolving it means amending one document or the other (CLAUDE.md rules 7, 8).

**1.1 "Streaming" contradicts ADR-001 A3.** `DESIGN.md:50,178` list "Streaming
agent activity" and screenshot 02 renders a `● Streaming` badge. ADR-001 A3
defers SSE explicitly, ADR-001:68 states the payoff of polling is that each
snapshot is complete so React needs no merge logic, and Phase 10's own item is
`useRunPolling`. Either the wording becomes "live activity (1s poll)" or A3 is
reopened. The badge is the more dangerous half — someone will build to it.

**1.2 Metrics live on the left in the spec and on the right in the design.**
Spec §10: "Persistent left sidebar (config summary and run metrics), main
workspace, top bar with a status pill and the Agent Trace drawer trigger" — two
regions plus a drawer. `DESIGN.md` specifies three regions with a right
`TelemetrySidebar`. `CLAUDE.md` now instructs "preserve the three-column
structure", so the repo currently tells a builder to violate its own spec.
Recommendation: keep the three-region design (telemetry as a first-class region
serves the token/cost requirement better than a left-sidebar block) and amend
spec §10 to match.

**Resolved 2026-08-25.** Spec §10 amended to the three-region layout, with the
reason recorded there. `DESIGN.md`, `COMPONENTS.md`, `CLAUDE.md` and the
screenshots already agreed; the spec was the document out of step, so nothing
else changed. ADR-001 records no layout decision (its only UI entries are A3 and
line 68, both about polling), so no ADR amendment was needed. `RunMetrics` now
lives in the right region — its component name is unchanged, so 1.4 below stays
open on its own terms.

**1.3 Four states designed, eight statuses derived.** Spec §9.2's ladder yields
`AWAITING_HUMAN`, `COMPLETED`, `COMPLETED_WITH_WARNINGS`, `FAILED`, `QUEUED`,
`RUNNING`, `ORPHANED`, plus `idle`. `DESIGN.md` designs `NEW`, `RUNNING`,
`INTERRUPTED`, `COMPLETED`. Missing: `QUEUED`, `COMPLETED_WITH_WARNINGS`,
`FAILED`, `ORPHANED`. `ORPHANED` matters most, because its entire reason for
existing (ADR-001:410, spec §12 item 14) is to replace a spinner that never
resolves — and the design gives it no view, so a builder following these
documents ships exactly the hanging spinner the architecture was shaped to
avoid. Phase 10's exit criterion depends on this.

Vocabulary also splits: the design says `INTERRUPTED`, the API says
`awaiting_human`. Adopt the backend's names in the design docs so the mapping is
identity rather than a translation table.

**1.4 Component names have no overlap with the spec's.** Spec §10 names
`ConfigurationForm`, `ActivityTimeline`, `EvidencePanel`, `HumanReviewPanel`,
`ReportView`, `AgentTraceDrawer`, `RunMetrics`. `COMPONENTS.md` names about
thirty, of which only `ReportView` matches. Pick one set. Phase 10's checklist
is written in the spec's vocabulary and will not map to this tree as it stands.

**1.5 The Agent Trace drawer is missing from the component tree.** Required by
CLAUDE.md rule 26, spec §10 and a Phase 10 line item. `COMPONENTS.md` has
`Diagnostics` inside `TelemetrySidebar` instead. The trace is the observable
event log with its own disclosure rules; diagnostics is latency and internals.
They are not the same surface.

**1.6 Free tab navigation defeats the interrupt guarantee.** `COMPONENTS.md:65`
lets users "manually navigate between available views when the state permits
it". Spec §10 is "one route", view derived from status, with Human Review
rendered above a still-incomplete timeline "so the workflow can never look
finished while waiting". `DESIGN.md:197` itself says the user must not be able
to accidentally continue without submitting a decision — which navigable
workflow tabs permit. Resolution: tabs exist **inside** `ReportView`
(Overview / Evidence / Changes / PR Draft) and nowhere else; workflow view
selection stays derived.

---

## 2. UI affordances with no data behind them

Each of these needs a real source, a documented derivation, or deletion.
CLAUDE.md rule 1 is the standard, and a mockup number is how a fabricated field
gets into a product.

**2.1 "Migration Complexity 7.5 / 10"** (`DESIGN.md:312,321`, screenshot 04).
No such field exists or is planned. The risk model is `RiskLevel`
(low/medium/high), a confidence float, and seven `RiskFactor`s. Either define
complexity as a documented function over the factor table — in the threshold
table, unit-testable without an LLM, like every other level — or remove it.
It currently appears in the design doc's own worked example, which makes it look
settled.

**2.2 "Grade: C"** (screenshot 04). Nothing grades a migration. No field, no
derivation.

**2.3 "Estimated Duration: 3–5 days"** (screenshot 04, in both the Executive
Summary and the Recommended Strategy card). Not modeled and not derivable from
anything the analyzer produces. Note the contrast: `DecisionOption.effort`
(spec §8.2) legitimately backs "Estimated Effort: Medium" in screenshot 03.
Effort has a source; duration does not.

**2.4 The Impact Breakdown donut does not add up and does not say what it
counts.** High 3 (30%), Medium 4 (30%), Low 3 (30%), Total 10 — Medium's
percentage is wrong and the three do not sum to 100. The header row above it
reads "Breaking API Changes 3 / Affected Files 8 / Potential Issues 5" while the
Key Breaking Changes table's own affected-file column sums to 10 against that 8.
"Potential Issues" is not a modeled quantity at all. Decide what the donut
segments *are* (breaking changes by severity? affected files by usage
confidence?) and label it.

**2.5 Model provider, model and temperature are user controls in the design and
environment variables in the product.** `COMPONENTS.md` has `ModelSelector` and
`TemperatureControl`; screenshot 01 shows a provider dropdown and a temperature
slider at 0.3. CLAUDE.md rule 14 puts configuration in env vars via
`pydantic-settings`, and Phase 9's contract has no configuration endpoint.
Render these read-only, or drop them.

**2.6 Target Dependency is a dropdown.** `DependencySpec.name` is free text, and
no endpoint enumerates a repository's manifest dependencies — populating that
dropdown would require analyzing the repo before the run starts, which is a new
endpoint nobody has planned. Make it a text input.

**2.7 The repository field is a GitHub slug.** Screenshot 01 shows
`acme/web-frontend` under a `GitHub | Local Project` toggle with a "Private
repository" affordance. `RemoteRepoRef.url` is a URL behind an `https`/`git`
scheme allowlist; GitHub authentication, OAuth and private repositories are all
sub-project 2. Label the toggle "Remote / Local" and the field "Repository URL".

**2.8 "Create Pull Request" is an action nothing implements.** Screenshot 06.
Writing to GitHub is sub-project 2. Rendering the PR *draft* is in scope;
offering the button is not (CLAUDE.md rule 3). The same card's
"Co-author: 87734" is meaningless and should go.

**2.9 The constraints form does not match `UserConstraints`, and the mismatch
costs real behavior.** Actual fields: `zero_downtime`, `minimize_effort`,
`deadline: date | None`, `risk_tolerance: RiskLevel`. Screenshot 01 offers
"Maintain API compatibility" and "Other (describe)", neither of which has a
field, and **omits `deadline` and `risk_tolerance` entirely**. This is not
cosmetic. `constraint_pressure` is derived from "zero-downtime, deadline, effort"
(spec §8.1), so a form with no deadline picker silently weakens a modeled risk
factor; and the `SCOPE_TRADEOFF` decision kind is deadline-versus-scope, so
without that input it is unreachable. `risk_tolerance` defaults to `MEDIUM`
whether or not the user meant it.

**2.10 "Additional Context" is unbacked free prose entering the judgment path.**
Screenshot 01: "We use ReactDOM.render in a few legacy areas". No field holds
it. If it stays, it needs a modeled home and an explicit rule that it carries no
weight in any level — the treatment `qualitative_notes` already gets in §8.1 —
plus a UI treatment that does not let it read as a finding.

**2.11 The cost card is missing both flags Phase 10 requires, and its pricing is
already wrong.** Screenshot 02/03/04: "Est. Cost (USD) $0.0261 / Model: gpt-4o /
Pricing: $0.0025 / 1K tokens". Phase 10 requires an *estimated* flag and a
*pricing-unknown* flag; neither is designed. Phase 0 resolved the stack to
OpenRouter (`openai/gpt-4.1-mini`), so hardcoded gpt-4o pricing does not
describe the running configuration, and pricing-unknown is the ordinary case
rather than the edge one. Design that state — it is the honest one.

**2.12 Every screenshot depicts a scenario the product cannot run.** React
17 → 18, `.jsx` diffs, a `Node.js / React` target stack. The demo target
throughout `PLANNING.md` is **Pydantic v1 → v2** on a Python repository; the
analyzer is Python `ast` only (`UsageKind`, `ManifestKind`, `import_root` are all
Python concepts) and the corpus is Pydantic primary sources. As reference images
these will be pixel-matched by whoever builds Phase 10, and the content will be
copied along with the layout. Re-render at least 01–04 against the real
scenario, or state prominently in `DESIGN.md` that only the layout is normative
and the content is placeholder.

**2.13 The Changes tab is the largest open scope question in the pack.**
Screenshot 05 shows split/unified toggles and `+12 −6` hunks — a real unified
diff. Nothing in the spec produces one: §8.3 requires each `MigrationStep` to
reference an affected file or carry `rationale_evidence`, and §8.4 check 3 only
verifies that named files exist. If this tab is meant to render generated
patches, then `MigrationStep` needs a patch field *and* `validate_plan` needs a
check that the patch parses and applies — otherwise the tab displays
LLM-authored code with no verification, which is the strongest available form of
the thing rule 1 forbids. If it is meant to render before/after excerpts of
*existing* code at cited usage sites, that is cheap, fully citable, and should be
said in the design so nobody builds the other thing. Decide before Phase 8, not
during Phase 10.

---

## 3. Backend output with no home in the UI

**3.1 The seven risk factors are absent from all six screenshots.** Factor
levels, each with its evidence refs, computed from a documented threshold table
without an LLM — this is the product's core honesty artifact, and the report
surfaces a letter grade, a donut and prose instead. It needs a first-class
factor table with per-factor evidence disclosure.

**3.2 The migration plan has no tab.** Phase 10's report line reads "risk,
confidence, affected files, breaking changes, evidence, plan, mitigations,
decisions". The design's four tabs are Overview / Evidence / Changes / PR Draft.
`MigrationPlan`, `MigrationStep` and `MigrationPlan.human_decisions_applied`
have nowhere to render — and `human_decisions_applied` is how the graded
requirement "human decision provably changes downstream generation" gets *shown*
to a user rather than merely asserted in a test.

**3.3 `unaddressed_with_reason`** (§8.4 check 8) — affected files that no plan
step addresses, with the reason. A headline honesty output with no surface.

**3.4 `COMPLETED_WITH_WARNINGS` and the failed validation checks.** §8.4 ends
"never silently passes"; the failures are meant to be shown in the report. No
state, no surface.

**3.5 Confidence ceilings need their reason, not just a number.** §8.1 ceilings:
no evidence available (≤0.3), skipped files over 10%, `TRANSITIVE_ONLY` role, a
high-confidence symbol with no documented evidence. Screenshot 04 shows "88%" on
a gradient bar. "Confidence is capped at 30% because no supporting evidence was
retrieved" is the claim that has to be legible.

**3.6 The no-interrupt path has no representation.** §8.2: when constraints
already settle the choice, no interrupt fires and a trace event records "resolved
by constraints, no human input required". `WorkflowProgress` always shows a Human
Review step, and `DESIGN.md:229-235`'s five step states have no *skipped*.
Without it, a correct run looks like it lost a step.

**3.7 Only one of four decision kinds is designed, and only one interrupt.**
`InterruptPayload.kind` is `STRATEGY_CHOICE`, `RISK_ACCEPTANCE`,
`SCOPE_TRADEOFF`, `DISCREPANCY_RESOLUTION`; `human_decisions` is an append
channel precisely so sequential interrupts work. `DESIGN.md:190` says the user
"must explicitly choose a migration strategy" — one kind, once. The other three
need layouts, and a second interrupt needs to not look like a bug.

**3.8 `consequences_if_unanswered`** is carried on every `InterruptPayload` and
has no home. Screenshot 03's "Additional Notes" panel shows the user's own
constraints back to them instead — which is the less useful of the two.

**3.9 `version_discrepancy` and `TRANSITIVE_ONLY`.** `RepoAnalysis.version_discrepancy`
(`models/repo.py:326`) surfaces detected-versus-stated rather than overriding it,
and `DependencyRole.TRANSITIVE_ONLY` means the user does not control the pin.
Both are confidence ceilings and one is an entire decision kind
(`DISCREPANCY_RESOLUTION`). Neither appears. Related: screenshot 01 makes
"Current Version" a user input, so the UI needs to show, after analysis, what
was detected instead.

**3.10 The triple duplicate-submit guard is unmentioned.** Spec §10 and Phase 10:
disabled button, local `submitting` flag, server 409 — "the last being the only
real guarantee". The design should say what the user sees when the 409 comes
back.

**3.11 Semantic tokens and accessibility.** Spec §10 names the tokens
(`risk-high`, `risk-medium`, `risk-low`, `pending-input`) and requires
`aria-live` on the status region so the transition into Human Review is
announced. `DESIGN.md` instead defines Success / Warning / Error / Neutral, and
folds "human decisions" and "medium-risk findings" both into Warning — which is
exactly the collision `pending-input` and `risk-medium` exist to separate. There
is no accessibility line anywhere in either document.

---

## 4. Defects inside the screenshots that a pixel-matcher will copy

**4.1** Screenshot 02 telemetry reads "Total Tokens 8,721 / **Output** Tokens
6,342 / **Output** Tokens 2,379". The first of the two should be Input.

**4.2** Screenshot 03 reads "Input Tokens 6,342 / Output Tokens 6,342" — the
same value twice.

**4.3** Screenshot 03's fourth step is labelled "Assessment Assessment", and its
stepper has six steps where screenshot 02 has seven: Migration Plan is gone.

**4.4** Screenshots 04, 05 and 06 show the run pill as **ACTIVE** and the
LangGraph panel with `hitl_strategy_review: Waiting` plus three Pending nodes,
while displaying a finished report. Under derived status that combination is
unreachable, and it inverts the design's own rule — instead of never looking
finished while waiting, it looks like it is running while finished.

**4.5** Screenshot 05 has no telemetry sidebar; 06 has one. `DESIGN.md:152` only
covers "during an active migration run", leaving the completed case undefined,
and the two images disagree about it.

**4.6** Step names drift three ways. `DESIGN.md:221-227` says Dependency
Analysis / Agentic RAG / Validation; the screenshots say Dependency Inspection /
Agentic RAG Search / Validation & Summary. Separately, the LangGraph panel
exposes node ids — `parse_dependencies`, `agentic_rag_search`,
`hitl_strategy_review`, `generate_migration_plan`, `validate_plan`,
`summarize_results` — and four of the six are not the spec §8.5 topology's names
(`inspect_dependency`, `agentic_rag`, `human_review`, `generate_plan`,
`finalize`). So the one place the design deliberately shows implementation
detail shows the wrong implementation detail. Exposing node boundaries is
permitted by rule 26, but then the names must be the real ones — and this sits
awkwardly beside `DESIGN.md:237`'s "user-facing workflow states rather than
individual LangGraph implementation details". Say which it is.

**4.7** The left sidebar drops its Knowledge Base and integration detail in
screenshots 05 and 06, contradicting `DESIGN.md:125`'s "the sidebar should remain
visually stable while the main workspace changes".

---

## 5. Decisions needed before Phase 10 starts

Ordered by how much downstream work each one blocks. **All eight resolved
2026-08-25**, before any Phase 10 code was written.

1. ~~**Changes tab: generated patches or cited existing code?**~~ (2.13)
   **Resolved: cited existing code.** This review filed it as "needed before
   Phase 8", and Phase 8 came and went without it being asked — which
   decided it by default. `MigrationStep` has no patch field and
   `validate_plan` has no check that a patch parses or applies. Adding both
   now would reopen a completed phase in order to build the one surface most
   likely to present unverified LLM output as fact. The tab renders
   `AffectedFile` → `UsageSite` instead: file, line, column, symbol,
   `UsageKind`, confidence, snippet. Existing code, fully cited, and renamed
   **Code** so nothing about it promises a diff.
2. ~~**Which screenshot scenario is normative**~~ (2.12) **Resolved: content
   declared non-normative**, layout normative, with a per-screenshot table in
   `DESIGN.md` of what must not be copied out of each. Re-rendering against
   Pydantic v1 → v2 remains worth doing and is not blocking; declaring the
   content non-normative removes the hazard for a fraction of the cost, and
   the hazard was the point of the item.
3. ~~**Two regions or three** (1.2)~~ — **decided 2026-08-25**: three regions,
   spec §10 amended.
4. ~~**Complexity, grade, duration, impact-breakdown, "potential issues"**~~
   (2.1–2.4) **Resolved: all five deleted**, none derived. This review offered
   "derive from the factor table or delete"; deriving would have invented a
   second scoring system alongside the seven factors, and the factors already
   answer the question those figures were gesturing at. `DecisionOption.effort`
   survives and legitimately backs "Estimated Effort". The seven-factor table
   is now the report's centrepiece, which is what §3.1 asked for — so the
   deletion and the addition are the same change.
5. ~~**The four missing statuses**~~ (1.3) **Resolved: all eight**, in the
   backend's own vocabulary, so the mapping is identity. `orphaned` has a view
   with resume-from-checkpoint, `failed` has one with retry,
   `completed_with_warnings` surfaces the failed checks, `queued` renders the
   activity view in a queued state.
6. ~~**Constraint fields**~~ (2.9) **Resolved: the form is exactly
   `UserConstraints`** — `zero_downtime`, `minimize_effort`, `deadline`,
   `risk_tolerance`. The two unbacked checkboxes are gone. `Additional
   Context` (2.10) is **dropped** rather than given a modeled home: the only
   honest treatment was a field carrying no weight in any level, and a UI
   that renders prose next to findings while insisting it is not a finding is
   a UI that will be misread.
7. ~~**Tabs inside the report only** (1.6), and one component vocabulary
   (1.4)~~ **Resolved: both.** `ReportTabs` is the only tab bar in the
   application; workflow view selection stays derived. `COMPONENTS.md` is
   rewritten in spec §10's vocabulary, which is also the vocabulary Phase
   10's checklist is written in.
8. ~~**Streaming wording**~~ (1.1) **Resolved:** *live · 1s poll*. The badge
   is gone with it, which was the dangerous half.

**One backend change fell out of §3.9.** `RepoAnalysis.version_discrepancy`
is a method taking `stated`, not a field, so nothing about it reaches the
client and the report could not have rendered it. It is now a
`@computed_field` on `FinalReport`, the one model holding both the stated
version and the analysis — the same move, for the same stated reason, as the
four computed fields Phase 9 added. No rule changed; an already-written rule
became visible.

**What this review cost and returned.** Nine of its findings would have
produced a fabricated figure on screen, and one (§1.3, `orphaned`) would have
shipped the hanging spinner the architecture was shaped to avoid. The
expensive one it caught too late was §2.13: filed as "decide before Phase 8",
read after Phase 8, and therefore decided by omission rather than on the
merits. The decision it defaulted to happens to be the right one, which is
luck rather than process — **a readiness review's blocking items need to be
tied to the phase that closes them, not to the phase they were noticed in.**
