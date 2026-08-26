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
            <p className="mt-2 text-xs text-risk-medium">
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

  return (
    <div className="space-y-3">
      <dl className="grid grid-cols-2 gap-4 sm:grid-cols-3">
        <Field label="Overall risk" value={<LevelBadge level={risk.overall_risk} />} />
        <Field label="Confidence" value={`${Math.round(risk.confidence * 100)}%`} />
        <Field label="Factors measured" value={String(factorCount)} />
      </dl>

      {clampFloor !== null && clampFloor !== risk.aggregate_risk && (
        <p className="rounded-md border border-risk-medium/40 bg-risk-medium/10 px-3 py-2 text-xs text-risk-medium">
          Raised from {risk.aggregate_risk} to {risk.overall_risk} by a floor the factors cannot
          lower.
        </p>
      )}

      {confidenceCeilings.length > 0 && (
        <ul className="space-y-1">
          {confidenceCeilings.map((ceiling) => (
            <li key={ceiling.reason} className="text-xs text-risk-medium">
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
