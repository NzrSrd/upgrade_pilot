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
import { WorkflowTimeline } from "./components/WorkflowTimeline";
import { viewFor } from "./derive/view";
import { useHealth } from "./hooks/useHealth";
import { useRunPolling } from "./hooks/useRunPolling";
import { useSessionRuns } from "./hooks/useSessionRuns";

export default function App() {
  const [threadId, setThreadId] = useState<string | null>(null);
  const [traceOpen, setTraceOpen] = useState(false);
  const { snapshot, error, reconnecting, restart } = useRunPolling(threadId);
  const { health } = useHealth();
  const { runs, remember } = useSessionRuns();

  const status: ViewStatus = threadId === null ? "idle" : (snapshot?.status ?? "queued");
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
          onNewRun={() => setThreadId(null)}
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
         * verbatim: `view === "error"` with `snapshot === null` is exactly
         * the condition under which `ErrorView` renders its own echo of this
         * same `error` as `pollError` (its "no snapshot to describe" branch).
         * `ErrorView` is the better owner there -- it is specifically about
         * the error, this banner is generic chrome above whichever view is
         * showing -- so this is the one case ceded to it, not every case
         * where `view === "error"`: if a snapshot already exists (e.g. this
         * poll failed while the previous one still holds an `orphaned`
         * snapshot on screen), this `error` is not necessarily anything
         * `ErrorView` shows on its own (it renders the *snapshot's* own
         * `errors`, not this live poll error, once a snapshot exists), so
         * suppressing the banner there would silently drop information
         * rather than deduplicate it.
         */}
        {error !== null && !(view === "error" && snapshot === null) && (
          <p className="rounded-md border border-risk-high/50 bg-risk-high/10 px-3 py-2 text-sm text-risk-high">
            {error.message}
          </p>
        )}
        {view === "configuration" && (
          <ConfigurationForm
            onStarted={(run) => {
              remember(run);
              setThreadId(run.threadId);
            }}
          />
        )}
        {view === "activity" && <ActivityTimeline snapshot={snapshot} />}
        {view === "human-review" && snapshot?.pending_decision != null && (
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
        )}
        {view === "report" && snapshot !== null && <ReportView snapshot={snapshot} />}
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
