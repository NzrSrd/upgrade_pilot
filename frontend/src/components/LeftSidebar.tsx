/**
 * Left region: start a run, revisit one this tab started, see what was
 * configured and whether the stores are reachable.
 *
 * Two absences are deliberate. There is no historical run list — that needs
 * the Postgres registry, which is sub-project 3 — and there are no model or
 * temperature controls, because configuration is environment variables via
 * `pydantic-settings` (rule 14) and the API exposes no configuration endpoint.
 * The model actually in use is reported in the telemetry region, from calls
 * that happened.
 */

import { CheckCircle, Plus, ShieldAlert } from "lucide-react";

import type { HealthResponse } from "../api/types";
import type { SessionRun } from "../hooks/useSessionRuns";
import { EmptyState, Field } from "./ui";

function Check({ ok, label }: { ok: boolean; label: string }) {
  return (
    <li className="flex items-center gap-2 text-xs">
      {ok ? (
        <CheckCircle className="size-3.5 text-risk-low" aria-hidden />
      ) : (
        <ShieldAlert className="size-3.5 text-risk-high" aria-hidden />
      )}
      <span className={ok ? "text-ink-muted" : "text-risk-high"}>
        {label}: {ok ? "ready" : "unavailable"}
      </span>
    </li>
  );
}

export function LeftSidebar({
  runs,
  current,
  summary,
  health,
  onNewRun,
  onSelectRun,
}: {
  runs: SessionRun[];
  current: string | null;
  summary: SessionRun | null;
  health: HealthResponse | null;
  onNewRun: () => void;
  onSelectRun: (threadId: string) => void;
}) {
  return (
    <nav className="flex w-64 shrink-0 flex-col gap-5 overflow-y-auto border-r border-edge bg-surface-sunken p-3">
      <button
        type="button"
        onClick={onNewRun}
        className="flex items-center justify-center gap-1.5 rounded-md border border-edge-strong bg-surface-raised px-3 py-2 text-sm font-medium hover:border-ink-faint"
      >
        <Plus className="size-4" aria-hidden /> New migration run
      </button>

      <section>
        <h2 className="mb-2 text-[11px] font-semibold tracking-wide text-ink-faint uppercase">
          This session
        </h2>
        {runs.length === 0 ? (
          <EmptyState>No runs yet in this tab.</EmptyState>
        ) : (
          <ul className="space-y-1">
            {runs.map((run) => (
              <li key={run.threadId}>
                <button
                  type="button"
                  onClick={() => onSelectRun(run.threadId)}
                  aria-current={run.threadId === current ? "true" : undefined}
                  className={`w-full rounded-md px-2 py-1.5 text-left text-xs ${
                    run.threadId === current
                      ? "bg-surface-raised text-ink"
                      : "text-ink-muted hover:bg-surface-raised"
                  }`}
                >
                  <span className="block truncate font-medium">{run.dependency}</span>
                  <span className="block truncate font-mono text-[11px] text-ink-faint">
                    {run.from} → {run.to} · {run.threadId.slice(0, 8)}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      {summary !== null && (
        <section>
          <h2 className="mb-2 text-[11px] font-semibold tracking-wide text-ink-faint uppercase">
            Configuration
          </h2>
          <dl className="space-y-2">
            <Field label="Dependency" value={summary.dependency} />
            <Field
              label="Versions"
              value={
                <span className="font-mono text-[13px]">
                  {summary.from} → {summary.to}
                </span>
              }
            />
            <Field
              label="Thread"
              value={<span className="font-mono text-[12px]">{summary.threadId}</span>}
            />
          </dl>
        </section>
      )}

      <section className="mt-auto">
        <h2 className="mb-2 text-[11px] font-semibold tracking-wide text-ink-faint uppercase">
          Integrations
        </h2>
        {health === null ? (
          <EmptyState>Checking…</EmptyState>
        ) : (
          <ul className="space-y-1.5">
            <Check ok={health.checks.chroma_dir} label="Knowledge base" />
            <Check ok={health.checks.checkpoint_dir} label="Checkpoints" />
            <Check ok={health.checks.llm_configured} label="Model key" />
          </ul>
        )}
      </section>
    </nav>
  );
}
