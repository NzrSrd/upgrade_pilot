/**
 * Sources, and whether the agent actually used them.
 *
 * `relevance` is labelled "similarity" because that is what it is — a vector
 * distance, not a judgement. `DESIGN.md` is explicit that the UI must never
 * imply a document is relevant because vector search returned it, so the
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
