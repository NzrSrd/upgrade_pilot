/**
 * The observable event log. CLAUDE.md rule 26 defines what belongs here — node
 * boundaries, queries issued, sources retrieved and selected, decisions,
 * validation outcomes — and what does not: internal prompts and private
 * reasoning.
 *
 * The drawer says so on its face. A drawer that silently omits prompts is one a
 * user assumes is complete, and "this is everything the agent did" is a
 * stronger claim than this surface can make.
 *
 * Separate from `Diagnostics` in the telemetry region on purpose: that is
 * latency and internals, this is the event record, and they have different
 * disclosure rules.
 */

import { X } from "lucide-react";
import { useEffect } from "react";

import type { TraceEvent } from "../api/types";
import { EmptyState, Mono } from "./ui";

export function AgentTraceDrawer({
  trace,
  open,
  onClose,
}: {
  trace: TraceEvent[];
  open: boolean;
  onClose: () => void;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Agent trace"
      className="fixed inset-y-0 right-0 z-20 flex w-full max-w-xl flex-col border-l border-edge bg-surface-sunken shadow-2xl"
    >
      <div className="flex items-start justify-between gap-3 border-b border-edge px-4 py-3">
        <div>
          <h2 className="text-sm font-semibold">Agent trace</h2>
          <p className="mt-0.5 text-[11px] text-ink-faint">
            Observable events only — no prompts, no private reasoning.
          </p>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close agent trace"
          className="rounded-md border border-edge p-1 text-ink-muted hover:text-ink"
        >
          <X className="size-4" aria-hidden />
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        {trace.length === 0 ? (
          <EmptyState>No events recorded yet.</EmptyState>
        ) : (
          <ol className="space-y-2">
            {trace.map((event) => (
              <li key={event.event_id} className="rounded-md border border-edge bg-surface px-3 py-2">
                <p className="flex flex-wrap items-baseline gap-x-2 text-[11px] text-ink-faint">
                  <Mono>{new Date(event.at).toLocaleTimeString()}</Mono>
                  <span className="font-medium text-ink-muted">{event.kind.replace(/_/g, " ")}</span>
                  <span>·</span>
                  <span className="font-mono">{event.node}</span>
                </p>
                <p className="mt-1 text-sm">{event.summary}</p>
                {event.detail !== null && event.detail !== undefined && (
                  <p className="mt-1 text-xs text-ink-faint">{event.detail}</p>
                )}
              </li>
            ))}
          </ol>
        )}
      </div>
    </div>
  );
}
