/**
 * The `queued` and `running` view: what has been established so far.
 *
 * Progressively populated rather than a spinner, because `RunSnapshot` carries
 * evidence as it accumulates and a developer watching a three-minute run
 * should be able to read what it has found. `queued` says so plainly — a run
 * beyond the concurrency cap has not started, and reporting it as working
 * would be a lie about work that has not happened.
 */

import { Circle } from "lucide-react";

import type { RunSnapshot } from "../api/types";
import { STEPS } from "../derive/steps";
import { EvidencePanel, selectedSourceIds } from "./EvidencePanel";
import { EmptyState, Field, LevelBadge, Mono, Panel } from "./ui";

export function ActivityTimeline({ snapshot }: { snapshot: RunSnapshot | null }) {
  if (snapshot === null || snapshot.status === "queued") {
    // `queued` alone does not distinguish a fresh run behind the
    // concurrency cap from a resumed orphan re-queued while its earlier
    // work is already recorded (`api/registry.py` sets `waiting=True`
    // before the task starts, for both). `completed_steps` does: it is the
    // same count `ErrorView` showed on the screen before this one, so a
    // resumed run never reads as having started from nothing.
    const completedSteps = snapshot?.completed_steps ?? [];
    return (
      <Panel title="Queued">
        <p className="flex items-center gap-2 text-sm text-ink-muted">
          {/* Static, not spinning: `WorkflowTimeline`'s APPEARANCE table
              reserves motion for `running` alone (`pending` gets this same
              `Circle`). A spinner here would imply work in progress on a
              run that, by definition, has not started. */}
          <Circle className="size-4" aria-hidden />
          {completedSteps.length > 0
            ? `Waiting for a run slot to resume — ${completedSteps.length} of ${STEPS.length} steps are already recorded.`
            : "Waiting for a run slot. Nothing has started yet."}
        </p>
      </Panel>
    );
  }

  // `RunSnapshot`'s list fields carry OpenAPI defaults, which
  // openapi-typescript marks optional even though the real API and the test
  // fixtures always send them. `?? []` is the typed equivalent of that
  // default, resolved once here rather than at every access below.
  const trace = snapshot.trace ?? [];
  const affectedFiles = snapshot.affected_files ?? [];
  const breakingChanges = snapshot.breaking_changes ?? [];
  const retrievedSources = snapshot.retrieved_sources ?? [];
  const riskAnalysis = snapshot.risk_analysis ?? null;
  const confidenceCeilings = riskAnalysis?.confidence_ceilings ?? [];
  const selected = selectedSourceIds(breakingChanges);

  return (
    <div className="space-y-4">
      <Panel title="Activity">
        {trace.length === 0 ? (
          <EmptyState>No events recorded yet.</EmptyState>
        ) : (
          <ol className="space-y-1.5">
            {trace.map((event) => (
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
          {affectedFiles.length === 0 ? (
            <EmptyState>Not analyzed yet.</EmptyState>
          ) : (
            <ul className="space-y-1">
              {affectedFiles.map((file) => (
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
          {breakingChanges.length === 0 ? (
            <EmptyState>None established yet.</EmptyState>
          ) : (
            <ul className="space-y-2">
              {breakingChanges.map((change) => (
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
        <EvidencePanel sources={retrievedSources} selectedIds={selected} />
      </Panel>

      {riskAnalysis !== null && (
        <Panel title="Risk so far">
          <dl className="grid grid-cols-2 gap-3">
            <Field label="Verdict" value={<LevelBadge level={riskAnalysis.overall_risk} />} />
            <Field label="Confidence" value={`${Math.round(riskAnalysis.confidence * 100)}%`} />
          </dl>
          {/* DESIGN.md: "Confidence renders with its reason or not at all."
              `confidenceCeilings` is empty precisely when nothing capped the
              figure, which is itself an honest state -- mirrors
              `OverviewTab.tsx`'s `VerdictDetail` rendering of the same
              field. */}
          {confidenceCeilings.length > 0 && (
            <ul className="mt-2 space-y-1">
              {confidenceCeilings.map((ceiling) => (
                <li key={ceiling.reason} className="text-xs text-ink-muted">
                  Capped at {Math.round(ceiling.ceiling * 100)}% — {ceiling.reason}
                </li>
              ))}
            </ul>
          )}
        </Panel>
      )}
    </div>
  );
}
