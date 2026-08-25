/**
 * The top bar: what run this is, what it is doing, and the trace trigger.
 *
 * The status pill is the application's `aria-live` region (spec §10), because
 * the transition into Human Review is the one state change a user must not
 * miss and the only one that is otherwise announced by nothing.
 */

import { ScrollText, Wifi, WifiOff } from "lucide-react";

import type { ViewStatus } from "../api/types";
import type { SessionRun } from "../hooks/useSessionRuns";

/**
 * A sentence per status, not the enum echoed back.
 *
 * `orphaned` gets the longest one because it is the status a user has no
 * intuition for: their run's process is gone, the work it did survives, and
 * the thing to do is resume it.
 */
const WORDING: Record<ViewStatus, { text: string; className: string }> = {
  idle: { text: "No run started", className: "text-ink-faint border-edge" },
  queued: { text: "Queued", className: "text-ink-muted border-edge" },
  running: { text: "Running", className: "text-ink border-edge-strong" },
  awaiting_human: {
    text: "Waiting for your decision",
    className: "text-pending-input border-pending-input/50 bg-pending-input/10",
  },
  completed: { text: "Completed", className: "text-risk-low border-risk-low/50" },
  completed_with_warnings: {
    text: "Completed with warnings",
    className: "text-risk-medium border-risk-medium/50",
  },
  failed: { text: "Failed", className: "text-risk-high border-risk-high/50" },
  orphaned: {
    text: "Interrupted by a restart",
    className: "text-risk-medium border-risk-medium/50",
  },
};

const LIVE = new Set<ViewStatus>(["queued", "running", "awaiting_human"]);

export function TopBar({
  status,
  reconnecting,
  summary,
  onOpenTrace,
}: {
  status: ViewStatus;
  reconnecting: boolean;
  summary: SessionRun | null;
  onOpenTrace: () => void;
}) {
  const wording = WORDING[status];

  return (
    <header className="flex items-center gap-4 border-b border-edge bg-surface-sunken px-4 py-2.5">
      <span className="text-sm font-semibold tracking-tight">UpgradePilot</span>

      {summary !== null && (
        <span className="flex items-baseline gap-2 text-sm">
          <span className="font-medium">{summary.dependency}</span>
          <span className="font-mono text-[13px] text-ink-muted">
            {summary.from} → {summary.to}
          </span>
        </span>
      )}

      <div className="ml-auto flex items-center gap-3">
        {LIVE.has(status) &&
          (reconnecting ? (
            <span className="flex items-center gap-1.5 text-[11px] text-risk-medium">
              <WifiOff className="size-3.5" aria-hidden /> Reconnecting…
            </span>
          ) : (
            // ADR-001 A3 defers SSE. This is the honest label for what the
            // client actually does.
            <span className="flex items-center gap-1.5 text-[11px] text-ink-faint">
              <Wifi className="size-3.5" aria-hidden /> Live · 1s poll
            </span>
          ))}

        <span
          aria-live="polite"
          className={`rounded-full border px-2.5 py-1 text-[11px] font-semibold ${wording.className}`}
        >
          {wording.text}
        </span>

        <button
          type="button"
          onClick={onOpenTrace}
          className="flex items-center gap-1.5 rounded-md border border-edge px-2.5 py-1 text-xs text-ink-muted hover:text-ink"
        >
          <ScrollText className="size-3.5" aria-hidden /> Agent trace
        </button>
      </div>
    </header>
  );
}
