/**
 * Sources, and whether the agent actually used them.
 *
 * `relevance` is labelled "similarity" because that is what it is — a vector
 * distance, not a judgement. `DESIGN.md` is explicit that the UI must never
 * imply a document is relevant because vector search returned it, so the
 * distinction that carries weight here is *selected* versus *retrieved*.
 *
 * "Selected" cannot come from the `sources_selected` trace event: its
 * `summary` is prose ("N documented breaking change(s) affect symbols this
 * repository uses...") and its `detail` holds `BreakingChange` ids, not
 * `SourceRef` ids (`graph/rag/nodes.py:690-712`). Parsing either would only
 * ever match stray words, never a real source id. `BreakingChange.source` is
 * already a full `SourceRef`, so "selected" is read from there instead: a
 * source counts as used exactly when a breaking change this report shows
 * cites it.
 */

import { FileText } from "lucide-react";

import type { BreakingChange, SourceRef } from "../api/types";
import { EmptyState, Mono } from "./ui";

/**
 * Source ids cited by the report's breaking changes.
 *
 * `BreakingChange.source` is a full `SourceRef` (`source.source_id` is
 * structured data, not text to parse), and `BreakingChange` requires a
 * `source` — no citation, no change. So every breaking change the report
 * shows names exactly one source it drew from, and that is the complete,
 * checkable definition of "selected" this surface can make.
 */
export function selectedSourceIds(breakingChanges: BreakingChange[]): Set<string> {
  return new Set(breakingChanges.map((change) => change.source.source_id));
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
