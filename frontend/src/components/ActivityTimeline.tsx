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

  // `RunSnapshot`'s list fields carry OpenAPI defaults, which
  // openapi-typescript marks optional even though the real API and the test
  // fixtures always send them. `?? []` is the typed equivalent of that
  // default, resolved once here rather than at every access below.
  const trace = snapshot.trace ?? [];
  const affectedFiles = snapshot.affected_files ?? [];
  const breakingChanges = snapshot.breaking_changes ?? [];
  const retrievedSources = snapshot.retrieved_sources ?? [];
  const riskAnalysis = snapshot.risk_analysis ?? null;
  const selected = selectedSourceIds(trace);

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
        </Panel>
      )}
    </div>
  );
}
