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
 * §2.1-2.4), and the factor table answers the question they gestured at.
 */

import type { FinalReport, RiskAnalysis } from "../../api/types";
import { EmptyState, Field, LevelBadge, Mono, Panel } from "../ui";

export function OverviewTab({ report }: { report: FinalReport }) {
  // `affected_files`, `breaking_changes` and `migration_plan` are optional in
  // the generated type only because every Pydantic field carries a default
  // -- `finalize` always populates the first two (as `[]` when there is
  // nothing) and sets the third to `None` rather than omitting it. Resolved
  // once here (ruling T10b) rather than at each use, so nothing below has to
  // treat an absent field differently from an empty/null one (ruling N1).
  const affectedFiles = report.affected_files ?? [];
  const breakingChanges = report.breaking_changes ?? [];
  const migrationPlan = report.migration_plan ?? null;
  const risk = report.risk_analysis ?? null;

  const usageSites = affectedFiles.reduce((total, file) => total + file.usage_sites.length, 0);

  // `repo_analysis` and `detected_version` are both REQUIRED keys with
  // nullable values, so both stay strict (`=== null`) -- fix-round-1 finding
  // 6, mirroring the same rule finding 1's own commit_sha/version_discrepancy
  // checks already follow. `role` is required and non-nullable once the
  // object is known to exist.
  const detectedVersion =
    report.repo_analysis === null ? null : report.repo_analysis.detected_version;
  const isTransitiveOnly = detectedVersion !== null && detectedVersion.role === "transitive_only";

  return (
    <div className="space-y-4">
      <Panel title="Verdict">
        {risk === null ? (
          <EmptyState>No risk assessment was produced.</EmptyState>
        ) : (
          <VerdictDetail risk={risk} />
        )}
      </Panel>

      <Panel title="Scope">
        <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          <Field label="Affected files" value={String(affectedFiles.length)} />
          <Field label="Usage sites" value={String(usageSites)} />
          <Field label="Breaking changes" value={String(breakingChanges.length)} />
          <Field
            label="Commit"
            value={
              // `commit_sha` is a REQUIRED field, nullable only -- `null` is
              // the sole absent form the schema allows, so this stays strict
              // (ruling N1).
              report.commit_sha === null ? "—" : <Mono>{report.commit_sha.slice(0, 10)}</Mono>
            }
          />
        </dl>
        {isTransitiveOnly && (
          // Fix-round-2: the round-1 wording named the wrong subject (the
          // *target* version, which the user typed in and nothing pins) and
          // a cause the analysis never determined ("through another
          // package" -- plausible, but not what `versions.py:102-106`
          // measures, which is only that every declaration found is a lock
          // file). Corrected to attribute this to the *detected* version and
          // to state only what `role` means, and reworded so it does not
          // restate the mechanism the `TRANSITIVE_ONLY` confidence ceiling
          // below already gives -- this bullet's own job is the
          // consequence for the reader: a manifest edit in this repository
          // cannot reach this pin. Neutral ink/edge tokens, same reasoning
          // as finding 5: this is explanatory text, not a severity finding.
          <p className="mt-3 text-xs text-ink-muted">
            This dependency's detected version comes from a lock file, not a hand-written
            manifest — so a manifest edit in this repository will not move this pin.
          </p>
        )}
      </Panel>

      {
        // `version_discrepancy` is REQUIRED and nullable, same as
        // `commit_sha` above -- strict stays strict (ruling N1).
        report.version_discrepancy !== null && (
          <Panel title="Version discrepancy">
            <dl className="grid grid-cols-2 gap-4">
              <Field label="You stated" value={<Mono>{report.version_discrepancy[0]}</Mono>} />
              <Field
                label="The manifests declare"
                value={<Mono>{report.version_discrepancy[1]}</Mono>}
              />
            </dl>
            {/* Explanatory chrome, not a severity finding -- same reasoning
                as fix-round-1 finding 5, applied here even though the order
                named only the clamp banner and ceiling text by line number. */}
            <p className="mt-2 text-xs text-ink-muted">
              Neither was silently preferred. Every claim below is resolved against the analyzed
              tree.
            </p>
          </Panel>
        )
      }

      {migrationPlan !== null && (
        <Panel title="Recommended strategy">
          <p className="text-sm font-medium">{migrationPlan.strategy_id.replace(/_/g, " ")}</p>
          <p className="mt-1 text-sm text-ink-muted">{migrationPlan.summary}</p>
        </Panel>
      )}

      <Panel title="Key breaking changes">
        {breakingChanges.length === 0 ? (
          <EmptyState>None established.</EmptyState>
        ) : (
          <ul className="space-y-2">
            {breakingChanges.map((change) => (
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

/**
 * The verdict panel's body, once `risk` is known non-null. A separate
 * function so `RiskAnalysis`'s own optional fields are resolved next to the
 * fields that use them, rather than crowding the top of `OverviewTab` with
 * fields that only matter inside this branch.
 */
function VerdictDetail({ risk }: { risk: RiskAnalysis }) {
  // `clamp_floor` is optional and nullable -- loose, not strict: the strict
  // form would compare `undefined !== null`, pass, and print "Raised from
  // ... " for a verdict that was never clamped (ruling N1).
  const clampFloor = risk.clamp_floor ?? null;
  const confidenceCeilings = risk.confidence_ceilings ?? [];
  const qualitativeNotes = risk.qualitative_notes ?? [];
  const factorCount = (risk.factors ?? []).length;

  // Fix-round-1 finding 1 (CRITICAL): the *claim* that the verdict was
  // raised is a separate condition from the *disclosure* of the floor.
  // `overall_risk` is exactly `max(aggregate_risk, clamp_floor)` --
  // `backend/src/upgradepilot/models/risk.py:107` refuses both directions --
  // so a floor BELOW the aggregate raises nothing even though it is present
  // and differs from the aggregate. Mirrored from the backend's own
  // condition at `graph/nodes/judgment.py:244`
  // (`clamp_floor is not None and overall_risk is not aggregate_risk`)
  // rather than re-derived, per rule 19: a rule re-implemented in
  // TypeScript is a second implementation nothing can check against the
  // first, and that is exactly how this defect shipped.
  const wasRaised = clampFloor !== null && risk.overall_risk !== risk.aggregate_risk;

  return (
    <div className="space-y-3">
      <dl className="grid grid-cols-2 gap-4 sm:grid-cols-3">
        <Field label="Overall risk" value={<LevelBadge level={risk.overall_risk} />} />
        {/* Fix-round-1 finding 4: shown on every run, not only a clamped
            one -- DESIGN.md lists it as its own bullet ("the value before
            the clamp"), and it was previously reachable only inside the
            raise-claim sentence, which made it invisible on an unclamped
            run. */}
        <Field label="Aggregate risk" value={<LevelBadge level={risk.aggregate_risk} />} />
        <Field label="Confidence" value={`${Math.round(risk.confidence * 100)}%`} />
        <Field label="Factors measured" value={String(factorCount)} />
        {clampFloor !== null && (
          // Disclosure, independent of `wasRaised`: a floor present but
          // lower than the aggregate is still shown as a value and still
          // says nothing about a raise (fix-round-1 finding 1's second
          // bullet, and finding 4's "do these together" note).
          <Field label="Clamp floor" value={<LevelBadge level={clampFloor} />} />
        )}
      </dl>

      {wasRaised && (
        // Fix-round-1 finding 5: this is an explanation of the verdict, not
        // a finding with a severity of its own -- a clamp raising a verdict
        // TO high rendered in `risk-medium` would misstate what it is (the
        // same collision that moved `consequences_if_unanswered` off
        // `risk-medium` in Task 11). Neutral ink/edge tokens instead.
        <p className="rounded-md border border-edge bg-surface-sunken px-3 py-2 text-xs text-ink-muted">
          Raised from {risk.aggregate_risk} to {risk.overall_risk} by a floor the factors cannot
          lower.
        </p>
      )}

      {confidenceCeilings.length > 0 && (
        <ul className="space-y-1">
          {confidenceCeilings.map((ceiling) => (
            <li key={ceiling.reason} className="text-xs text-ink-muted">
              Capped at {Math.round(ceiling.ceiling * 100)}% — {ceiling.reason}
            </li>
          ))}
        </ul>
      )}

      <p className="text-sm text-ink">{risk.summary}</p>

      {qualitativeNotes.length > 0 && (
        <div>
          <p className="text-[11px] tracking-wide text-ink-faint uppercase">
            Notes — these carry no weight in any level
          </p>
          <ul className="mt-1 space-y-0.5">
            {qualitativeNotes.map((note) => (
              <li key={note} className="text-xs text-ink-muted">
                — {note}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
