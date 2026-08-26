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
 *
 * Failures are derived as `outcomes.filter((o) => !o.passed)` (ruling F2):
 * `ValidationReport.failures` is a bare Pydantic property, not a
 * `@computed_field`, so it never reaches the wire and this component never
 * reads it.
 */

import type { FinalReport } from "../../api/types";
import { EvidenceRefList } from "../EvidenceRefList";
import { EmptyState, LevelBadge, Mono, Panel } from "../ui";

export function PlanTab({ report }: { report: FinalReport }) {
  // `migration_plan` and `validation` are REQUIRED-shaped in practice --
  // `finalize` always sets one or `None`, never omits the key -- but the
  // generated type carries `undefined` too because every Pydantic field has
  // a default. Resolved once here to `null` (ruling T10b) so every check
  // below is a plain, correct `=== null` / `!== null` rather than one that
  // treats an absent field differently from an explicit `null` (ruling N1).
  const plan = report.migration_plan ?? null;
  const validation = report.validation ?? null;

  if (plan === null) {
    return (
      <Panel title="Plan">
        <EmptyState>No plan was produced for this run.</EmptyState>
      </Panel>
    );
  }

  // `human_decisions_applied`, `unaddressed_with_reason`, `mitigations` and
  // `steps` are all optional in the generated type only because every
  // Pydantic field carries a default -- `MigrationPlan` always sends `[]`
  // rather than omitting the key. Resolved once here (ruling T10b).
  const steps = plan.steps ?? [];
  const humanDecisionsApplied = plan.human_decisions_applied ?? [];
  const unaddressedWithReason = plan.unaddressed_with_reason ?? [];
  const mitigations = plan.mitigations ?? [];

  const outcomes = validation?.outcomes ?? [];
  const passedCount = outcomes.filter((outcome) => outcome.passed).length;

  // Fix round 1, CRITICAL. `unaddressed_with_reason` being empty does NOT
  // mean every file is covered: `_unaddressed`
  // (backend/.../graph/nodes/planning.py:258-272) only produces an entry
  // when there is an honest, documented reason -- a file a documented
  // change covers, that no step addresses and has no such reason, produces
  // no entry here and instead fails validate.py's `affected_files_addressed`
  // check (check 8). Inferring "fully covered" from the empty array is
  // exactly the re-derivation rule 19 forbids; this reads the backend's own
  // verdict on that question instead.
  const coverageOutcome =
    outcomes.find((outcome) => outcome.check_id === "affected_files_addressed") ?? null;

  return (
    <div className="space-y-4">
      <Panel title={`Strategy — ${plan.strategy_id.replace(/_/g, " ")}`}>
        <p className="text-sm text-ink-muted">{plan.summary}</p>
      </Panel>

      <Panel title="Steps">
        {steps.length === 0 ? (
          <EmptyState>The plan has no steps.</EmptyState>
        ) : (
          <ol className="space-y-3">
            {steps.map((step) => {
              // `files` and `rationale_evidence` are optional in the
              // generated type for the same reason as above -- `MigrationStep`
              // always sends `[]` (ruling T10b).
              const files = step.files ?? [];
              const rationaleEvidence = step.rationale_evidence ?? [];
              return (
                <li key={step.order} className="border-b border-edge pb-3 last:border-0 last:pb-0">
                  <div className="flex flex-wrap items-baseline gap-2">
                    <span className="font-mono text-[13px] text-ink-faint">{step.order}.</span>
                    <span className="text-sm font-medium">{step.title}</span>
                    {/* `requires_downtime` carries a default (`false`), so
                        the fallback below is defensive, not a fabrication:
                        an absent value and an explicit `false` mean the same
                        thing here (ruling T10b). Fix round 2, finding 2:
                        `requires_downtime` is a fact about the step, not a
                        verdict -- whether that fact is a problem is check
                        10's question (`_check_zero_downtime_respected`),
                        which passes outright when no zero-downtime
                        constraint was stated. Styling this `risk-medium`
                        unconditionally re-derived a severity the backend
                        may never have assigned, the same rule-19 shape as
                        round 1's coverage-panel Critical. Neutral chrome
                        states the fact; check 10's own outcome (rendered
                        correctly in the Validation panel below) carries the
                        verdict. */}
                    {(step.requires_downtime ?? false) && (
                      <span className="rounded border border-edge px-1.5 py-0.5 text-[10px] tracking-wide text-ink-muted uppercase">
                        Requires downtime
                      </span>
                    )}
                  </div>
                  <p className="mt-1 text-sm text-ink-muted">{step.description}</p>
                  {files.length > 0 && (
                    <p className="mt-1 flex flex-wrap gap-x-3">
                      {files.map((path) => (
                        <Mono key={path}>{path}</Mono>
                      ))}
                    </p>
                  )}
                  {/* `validation` is optional AND nullable -- loose, not
                      strict: the strict form (`!== null`) would leave an
                      absent value (`undefined`) truthy and try to render it
                      anyway (ruling N1). */}
                  {step.validation != null && (
                    <p className="mt-1 text-xs text-ink-faint">
                      Verify with <Mono>{step.validation}</Mono>
                    </p>
                  )}
                  {rationaleEvidence.length > 0 && <EvidenceRefList refs={rationaleEvidence} />}
                </li>
              );
            })}
          </ol>
        )}
      </Panel>

      <Panel title="Your decisions, and what they changed">
        {humanDecisionsApplied.length === 0 ? (
          // Fix round 1, finding 5. The component checks only that this
          // array is empty; "the constraints settled every question" names
          // a *cause* nothing here establishes -- it happens to hold by
          // construction today, but no type or check enforces it, so it is
          // one backend change from being false with nothing to catch it
          // (same defect class as Task 12's TRANSITIVE_ONLY bullet). State
          // only what the empty array says.
          <EmptyState>No human decision was applied to this plan.</EmptyState>
        ) : (
          <ul className="space-y-2">
            {humanDecisionsApplied.map((applied) => (
              <li key={applied.decision_id} className="text-sm">
                <Mono>{applied.decision_id}</Mono>
                <span className="mt-0.5 block text-ink">{applied.how_it_changed_the_plan}</span>
              </li>
            ))}
          </ul>
        )}
      </Panel>

      <Panel title="Not addressed by any step">
        <div className="space-y-3">
          {unaddressedWithReason.length > 0 && (
            <ul className="space-y-1.5">
              {unaddressedWithReason.map((file) => (
                <li key={file.path} className="text-sm">
                  <Mono>{file.path}</Mono>
                  {/* Fix round 2, finding 1: `UnaddressedFile` carries only
                      `path` and `reason` -- no grade, no severity, nothing
                      that ranks one unaddressed file against another.
                      `text-risk-medium` invented a rank the backend never
                      assigned (third instance of this defect in the phase,
                      after Task 11's `consequences_if_unanswered` and Task
                      12's clamp/ceiling text). This is an explanation, not a
                      graded finding, so it gets neutral ink/edge chrome. */}
                  <span className="mt-0.5 block text-xs text-ink-muted">{file.reason}</span>
                </li>
              ))}
            </ul>
          )}
          {coverageOutcome !== null && (
            // Fix round 3, finding I7: rendered unconditionally rather than
            // only in the `unaddressedWithReason.length === 0` branch. A
            // file with a documented reason (above) is not the same claim as
            // "check 8 passed" -- check 8 can still fail on *other* files
            // that have no step and no reason, which is exactly the silence
            // check 8's own docstring refuses to allow: "a file that is
            // neither addressed nor mentioned, which is how a partial plan
            // reads as a complete one." Sourced from the check that actually
            // decides coverage, not inferred from the reasoned list above
            // (see the CRITICAL comment on `coverageOutcome`). `detail` is
            // the backend's own sentence, mirrored rather than re-derived
            // (rule 19); `offenders` names the files this panel would
            // otherwise have gone silent about.
            <div>
              <p
                className={`text-sm ${coverageOutcome.passed ? "text-ink-faint" : "text-risk-high"}`}
              >
                {coverageOutcome.detail}
              </p>
              {(coverageOutcome.offenders ?? []).length > 0 && (
                <ul className="mt-1.5 space-y-0.5">
                  {(coverageOutcome.offenders ?? []).map((path) => (
                    <li key={path}>
                      <Mono>{path}</Mono>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
          {unaddressedWithReason.length === 0 && coverageOutcome === null && (
            // No validation to source a coverage claim from -- state only
            // what the empty array itself supports, which is not full
            // coverage.
            <EmptyState>No files were listed as unaddressed with a reason.</EmptyState>
          )}
        </div>
      </Panel>

      {mitigations.length > 0 && (
        <Panel title="Mitigations">
          <ul className="space-y-0.5">
            {mitigations.map((mitigation) => (
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
            : `Validation — ${passedCount} of ${outcomes.length} checks passed, attempt ${validation.attempt}`
        }
      >
        {validation === null ? (
          <EmptyState>The plan was not validated.</EmptyState>
        ) : (
          <ul className="space-y-1.5">
            {outcomes.map((outcome) => {
              const offenders = outcome.offenders ?? [];
              return (
                <li key={outcome.check_id} className="flex items-start gap-2 text-sm">
                  <LevelBadge level={outcome.passed ? "low" : "high"}>
                    {outcome.passed ? "pass" : "fail"}
                  </LevelBadge>
                  <div className="min-w-0">
                    <span className="font-medium">{outcome.check_id.replace(/_/g, " ")}</span>
                    <span className="mt-0.5 block text-xs text-ink-muted">{outcome.detail}</span>
                    {offenders.length > 0 && (
                      <span className="mt-0.5 block text-xs text-risk-high">
                        {offenders.join(", ")}
                      </span>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </Panel>
    </div>
  );
}
