/**
 * The `failed` and `orphaned` views, plus the defensive case of no snapshot
 * at all (a poll that never returned one, e.g. an unknown thread id).
 *
 * `orphaned` is the reason the derived-status ladder exists at all
 * (ADR-001:410): a checkpoint that outlived its process, which a spinner
 * cannot represent and which the design pack gave no view. The wording matters
 * as much as the button — the user needs to know the work already done
 * survived, and that resuming *continues* rather than restarts, because
 * offering "start again" would discard a live checkpoint and bill for the same
 * work twice.
 *
 * The resume carries no decision. Spec §9.1: an abandoned run is not waiting
 * for an answer, and asking the client to invent one would be asking for a lie.
 */

import { AlertTriangle, RotateCcw } from "lucide-react";
import { useState } from "react";

import { ApiFailure, resumeRun } from "../api/client";
import type { ApiError, RunSnapshot } from "../api/types";
import { Mono, Panel } from "./ui";

export function ErrorView({
  snapshot,
  pollError,
  onRetry,
  onResumed,
}: {
  snapshot: RunSnapshot | null;
  pollError: ApiError | null;
  onRetry: () => void;
  onResumed: () => void;
}) {
  const [resuming, setResuming] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  // `completed_steps` and `errors` are optional in the generated type only
  // because every Pydantic field carries a default -- `snapshot_response`
  // always populates them (as `[]` when there is nothing). Resolved once
  // here (ruling T10b) rather than at each use below, following Task 12's
  // pattern (`ReportView.tsx:46`, `OverviewTab.tsx:31-34`).
  const completedSteps = snapshot?.completed_steps ?? [];
  const done = completedSteps.length;

  // `snapshot` and `pollError` are props typed `T | null` (not optional JSON
  // fields), so both comparisons stay strict (ruling N1). When a snapshot
  // exists at all, its own recorded errors are what get shown; a poll error
  // only stands in when there is no snapshot to describe -- e.g. a 404 on an
  // unknown thread, where there is no run to report errors *from*.
  const errors =
    snapshot !== null ? snapshot.errors ?? [] : pollError !== null ? [pollError] : [];

  const orphaned = snapshot?.status === "orphaned";
  // Only two statuses route to this view (`derive/view.ts`): `failed` and
  // `orphaned`. A `null` snapshot is a third, distinct situation this
  // component must also handle defensively -- the poll itself never
  // produced a run to describe, which is not the same claim as "the run
  // failed."
  const failed = snapshot !== null && !orphaned;

  async function resume() {
    if (snapshot === null || resuming) return;
    setResuming(true);
    setProblem(null);
    try {
      // No decision: this run is not waiting for an answer, it is waiting for
      // a process.
      await resumeRun({ thread_id: snapshot.thread_id, decision: null });
      onResumed();
    } catch (error) {
      setProblem(
        error instanceof ApiFailure ? error.error.message : "The backend is unreachable.",
      );
      setResuming(false);
    }
  }

  return (
    <div className="space-y-4">
      <Panel
        title={
          orphaned
            ? "Interrupted by a restart"
            : failed
              ? "This run failed"
              : "Could not load this run"
        }
      >
        <div className="flex items-start gap-3">
          <AlertTriangle
            className={`mt-0.5 size-5 shrink-0 ${orphaned ? "text-risk-medium" : "text-risk-high"}`}
            aria-hidden
          />
          <div className="min-w-0 space-y-2">
            {orphaned && (
              <>
                <p className="text-sm">
                  The process running this migration is gone, but its checkpoint survived.{" "}
                  <span className="font-medium">{done} of 8 steps</span> are already recorded.
                </p>
                <p className="text-sm text-ink-muted">
                  Resuming continues from where it stopped — it does not re-run the work already
                  done, and it does not charge for it again.
                </p>
              </>
            )}
            {failed && (
              <p className="text-sm">
                The run stopped before producing a report. What it established up to that point is
                in the agent trace.
              </p>
            )}
            {/* Neither `orphaned` nor `failed`: there is no snapshot at all, so
                there is nothing this component knows to say beyond the poll
                error itself, rendered below. Inventing a reason here would be
                exactly the "unmeasured cause stated as fact" this view has to
                avoid. */}

            {errors.length > 0 && (
              <ul className="space-y-1.5">
                {errors.map((error, index) => (
                  <li key={`${error.code}-${index}`} className="text-sm">
                    <span className="text-risk-high">{error.message}</span>
                    <span className="mt-0.5 block text-[11px] text-ink-faint">
                      <Mono>{error.code}</Mono>
                      {/* `node` is an optional JSON field (`string | null` with
                          no `default`, i.e. it can be absent as well as
                          explicitly null) -- loose per ruling N1, so an
                          absent node renders the same as an explicit one. */}
                      {error.node != null && (
                        <>
                          {" · "}
                          <Mono>{error.node}</Mono>
                        </>
                      )}
                      {error.retryable === true && " · retryable"}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>

        {problem !== null && (
          <p
            role="alert"
            className="mt-3 rounded-md border border-risk-high/50 bg-risk-high/10 px-3 py-2 text-sm text-risk-high"
          >
            {problem}
          </p>
        )}

        <div className="mt-4 flex gap-2">
          {orphaned && (
            <button
              type="button"
              onClick={resume}
              disabled={resuming}
              className="flex items-center gap-1.5 rounded-md border border-edge-strong bg-surface-raised px-3 py-2 text-sm font-medium disabled:opacity-50"
            >
              <RotateCcw className="size-4" aria-hidden />
              {resuming ? "Resuming…" : "Resume from checkpoint"}
            </button>
          )}
          <button
            type="button"
            onClick={onRetry}
            className="rounded-md border border-edge px-3 py-2 text-sm text-ink-muted hover:text-ink"
          >
            Configure a new run
          </button>
        </div>
      </Panel>
    </div>
  );
}
