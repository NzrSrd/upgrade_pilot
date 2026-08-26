import { useState } from "react";

import type { ViewStatus } from "./api/types";
import { ActivityTimeline } from "./components/ActivityTimeline";
import { AgentTraceDrawer } from "./components/AgentTraceDrawer";
import { AppShell } from "./components/AppShell";
import { ConfigurationForm } from "./components/ConfigurationForm";
import { ErrorView } from "./components/ErrorView";
import { HumanReviewPanel } from "./components/HumanReviewPanel";
import { LeftSidebar } from "./components/LeftSidebar";
import { ReportView } from "./components/report/ReportView";
import { RunMetrics } from "./components/RunMetrics";
import { TopBar } from "./components/TopBar";
import { Panel } from "./components/ui";
import { WorkflowTimeline } from "./components/WorkflowTimeline";
import { prefillFrom } from "./derive/prefill";
import type { FormPrefill } from "./derive/prefill";
import { viewFor } from "./derive/view";
import { useHealth } from "./hooks/useHealth";
import { useRunPolling } from "./hooks/useRunPolling";
import { useSessionRuns } from "./hooks/useSessionRuns";

export default function App() {
  const [threadId, setThreadId] = useState<string | null>(null);
  // The inputs a finished run was started with, held so the configuration form
  // can be seeded from them. Lives here rather than in the form because the
  // form unmounts the moment a run starts, and here because `App` already owns
  // the only thing that decides which view is on screen.
  const [prefill, setPrefill] = useState<{ fromThread: string; values: FormPrefill } | null>(null);
  const [traceOpen, setTraceOpen] = useState(false);
  const { snapshot, error, reconnecting, restart } = useRunPolling(threadId);
  const { health } = useHealth();
  const { runs, remember } = useSessionRuns();

  // Fix round 4, superseding round 3's call-site override entirely (not
  // layered on it): `unavailable` is a real member of `ViewStatus`, added
  // for the same reason `idle` already is -- it describes what this client
  // knows, not a status the backend derives. A poll that has already come
  // back refused, with no snapshot ever loaded, is not "queued": there is
  // nothing to derive `queued` *from*, and reporting it as `queued` would
  // render an activity timeline (and, in `TopBar`, announce a status pill)
  // implying a run in progress that may not exist. Before the first poll
  // returns, `error` is still `null`, so this still resolves to `queued` --
  // "we have not heard back yet" is honestly different from "we asked and
  // were told no", and only the second is `unavailable`.
  const status: ViewStatus =
    threadId === null
      ? "idle"
      : error !== null && snapshot === null
        ? "unavailable"
        : (snapshot?.status ?? "queued");
  const view = viewFor(status);
  const summary = runs.find((run) => run.threadId === threadId) ?? null;
  // `RunSnapshot` declares fourteen of its seventeen fields optional in the
  // generated types (every Pydantic field has a default) even though the API
  // always populates them. Resolved once here rather than scattered through
  // the JSX below.
  const answeredCount = (snapshot?.human_decisions ?? []).length;

  return (
    <AppShell
      topBar={
        <TopBar
          status={status}
          reconnecting={reconnecting}
          summary={summary}
          onOpenTrace={() => setTraceOpen(true)}
        />
      }
      sidebar={
        <LeftSidebar
          runs={runs}
          current={threadId}
          summary={summary}
          health={health}
          onNewRun={() => {
            // Clearing is not optional: a new run means a blank form, and the
            // prefill outliving the retry that set it would make this button
            // silently reopen the last correction.
            setPrefill(null);
            setThreadId(null);
          }}
          onSelectRun={setThreadId}
        />
      }
      metrics={<RunMetrics snapshot={snapshot} />}
      drawer={
        <AgentTraceDrawer
          trace={snapshot?.trace ?? []}
          open={traceOpen}
          onClose={() => setTraceOpen(false)}
        />
      }
    >
      <div className="space-y-5">
        {threadId !== null && <WorkflowTimeline snapshot={snapshot} />}
        {/*
         * Suppressed only in the one case where it would repeat `ErrorView`
         * verbatim: `status === "unavailable"` (fix round 4) is exactly the
         * condition under which `ErrorView` renders its own echo of this
         * same `error` as `pollError` (its "no snapshot to describe" branch,
         * fix round 1). `ErrorView` is the better owner there -- it is
         * specifically about the error, this banner is generic chrome above
         * whichever view is showing -- so this is the one case ceded to it,
         * not every case where `view === "error"`: if a snapshot already
         * exists (e.g. this poll failed while the previous one still holds
         * an `orphaned` snapshot on screen, `status` stays `"orphaned"`, not
         * `"unavailable"`), this `error` is not necessarily anything
         * `ErrorView` shows on its own (it renders the *snapshot's* own
         * `errors`, not this live poll error, once a snapshot exists), so
         * suppressing the banner there would silently drop information
         * rather than deduplicate it.
         *
         * `status === "unavailable"` used to be unreachable under round 3's
         * call-site override (superseded here) and is reachable now that it
         * is a real `ViewStatus` member -- re-verified by an `App`-level
         * test that reaches it through a real poll failure, not only
         * through a direct `ErrorView` render.
         */}
        {error !== null && status !== "unavailable" && (
          <p className="rounded-md border border-risk-high/50 bg-risk-high/10 px-3 py-2 text-sm text-risk-high">
            {error.message}
          </p>
        )}
        {view === "configuration" && (
          <ConfigurationForm
            // Keyed on the run the correction came from. The fields read
            // `prefill` in their `useState` initialisers, so a form that
            // stayed mounted across a change would keep the old values --
            // today it cannot, because a retry is only reachable from the
            // report view and the form unmounts to get there, but a key that
            // tracks the actual source costs nothing and does not depend on
            // that remaining true.
            key={prefill?.fromThread ?? "blank"}
            prefill={prefill?.values}
            onStarted={(run) => {
              remember(run);
              setThreadId(run.threadId);
            }}
          />
        )}
        {view === "activity" && <ActivityTimeline snapshot={snapshot} />}
        {view === "human-review" &&
          (snapshot?.pending_decision != null ? (
            // Keyed by question id: guard two deliberately never clears
            // `submitting` after a successful answer, and without a key React
            // reuses this component instance -- and that leftover state -- for
            // the next question at the same tree position. `human_decisions` is
            // an append channel precisely so interrupts fire in sequence; a
            // stale `submitting=true` from question 1 would permanently block
            // question 2's button. The key makes a new question mount fresh.
            <HumanReviewPanel
              key={snapshot.pending_decision.question_id}
              threadId={snapshot.thread_id}
              decision={snapshot.pending_decision}
              answered={answeredCount}
              onSubmitted={() => undefined}
            />
          ) : (
            // `graph/inspect.py`'s `pending_payload` returns `None` on
            // purpose when the interrupt's value is not an
            // `InterruptPayload` -- "guessing at its shape would put an
            // unvalidated object in front of the person answering." That
            // state is real and reachable (`is_awaiting_human` only checks
            // that an interrupt exists, not what it carries), and the
            // `TopBar` pill and `WorkflowTimeline` both already say a
            // decision is pending, so this workspace must say something
            // rather than render nothing under them. It does not guess why
            // the question is missing -- only that it is.
            <Panel title="Waiting for your decision">
              <p className="text-sm text-ink-muted">
                This run is waiting for a decision, but the question has not been received.
              </p>
            </Panel>
          ))}
        {view === "report" && snapshot !== null && (
          <ReportView
            snapshot={snapshot}
            onRetry={(report) => {
              setPrefill({ fromThread: report.thread_id, values: prefillFrom(report) });
              setThreadId(null);
            }}
          />
        )}
        {view === "error" && (
          <ErrorView
            snapshot={snapshot}
            pollError={error}
            onRetry={() => setThreadId(null)}
            // A successful resume request reaches the backend, and the run
            // really continues -- but `orphaned` stops this poll loop (fix
            // round 1: `POLLING_STOPS_ON` in `api/types.ts`), and nothing
            // else about the resume changes `threadId`, which is the only
            // thing that would otherwise re-enter it. Without `restart`, the
            // UI would sit on this view forever with a resume that already
            // worked on the server.
            onResumed={restart}
          />
        )}
      </div>
    </AppShell>
  );
}
