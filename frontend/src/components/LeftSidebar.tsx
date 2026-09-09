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
import { isAuthConfigured } from "../auth/config";
import type { SessionRun } from "../hooks/useSessionRuns";
import { EmptyState, Field } from "./ui";

/**
 * `ok` renders exactly what `api/routes/health.py` measured, no more:
 * "the readiness of the store *locations* and the presence of a key, which
 * is exactly what the field names say and no more" -- it "deliberately
 * does not open the Chroma store, connect to the checkpointer database, or
 * call the model provider." A never-ingested Chroma directory is a writable
 * location and reads `true` here; that is correct, because a writable
 * location is all this checked. `readyLabel`/`unreadyLabel` are passed per
 * call site rather than hardcoded, because "storage location writable" and
 * "configured" back different fields and neither would be honest for both.
 */
/**
 * What `checks.checkpoint_ready` means, which depends on the backend.
 *
 * The two are not the same claim and this component cannot say so with one
 * label. On SQLite the backend measures a directory, so "storage location
 * writable" is accurate. On Postgres it measures only that a DSN is
 * configured -- the endpoint deliberately opens no connection -- so the same
 * label would assert something about a database nobody checked, and a reader
 * seeing a green tick would take it for reachability.
 *
 * Hence "configured", which is the weaker and true claim, matching what the
 * model-key row already says about a key whose validity is equally unverified.
 */
const CHECKPOINT_LABELS: Record<
  HealthResponse["checkpoint_backend"],
  { readyLabel: string; unreadyLabel: string }
> = {
  sqlite: {
    readyLabel: "storage location writable",
    unreadyLabel: "storage location not writable",
  },
  postgres: {
    readyLabel: "configured (reachability not checked)",
    unreadyLabel: "not configured",
  },
};

function Check({
  ok,
  label,
  readyLabel,
  unreadyLabel,
}: {
  ok: boolean;
  label: string;
  readyLabel: string;
  unreadyLabel: string;
}) {
  return (
    <li className="flex items-center gap-2 text-xs">
      {ok ? (
        <CheckCircle className="size-3.5 text-risk-low" aria-hidden />
      ) : (
        <ShieldAlert className="size-3.5 text-risk-high" aria-hidden />
      )}
      <span className={ok ? "text-ink-muted" : "text-risk-high"}>
        {label}: {ok ? readyLabel : unreadyLabel}
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
            <Check
              ok={health.checks.chroma_dir}
              label="Knowledge base"
              readyLabel="storage location writable"
              unreadyLabel="storage location not writable"
            />
            <Check
              ok={health.checks.checkpoint_ready}
              label="Checkpoints"
              {...CHECKPOINT_LABELS[health.checkpoint_backend]}
            />
            {health.auth_required && !isAuthConfigured ? (
              /*
               * A gated backend behind an ungated build. Reachable because the
               * two keys live in different places and deploy separately -- the
               * secret in the backend's environment, the publishable one in
               * the frontend's -- so nothing stops one being set without the
               * other.
               *
               * Shown because the symptom is otherwise unreadable: every
               * request answers 401 "Sign in to use this service." while the
               * app presents no way to sign in. Reusing `Check` rather than
               * inventing a banner keeps this in the one place an operator
               * already looks to ask whether the deployment is configured.
               */
              <Check
                ok={false}
                label="Access gate"
                readyLabel=""
                unreadyLabel="backend requires sign-in, this build has no Clerk key"
              />
            ) : null}
            <Check
              ok={health.checks.llm_configured}
              label="Model key"
              readyLabel="configured"
              unreadyLabel="missing"
            />
          </ul>
        )}
      </section>
    </nav>
  );
}
