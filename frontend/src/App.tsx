import { useState } from "react";

import type { ViewStatus } from "./api/types";
import { ActivityTimeline } from "./components/ActivityTimeline";
import { AgentTraceDrawer } from "./components/AgentTraceDrawer";
import { AppShell } from "./components/AppShell";
import { ConfigurationForm } from "./components/ConfigurationForm";
import { HumanReviewPanel } from "./components/HumanReviewPanel";
import { LeftSidebar } from "./components/LeftSidebar";
import { RunMetrics } from "./components/RunMetrics";
import { TopBar } from "./components/TopBar";
import { WorkflowTimeline } from "./components/WorkflowTimeline";
import { EmptyState, Panel } from "./components/ui";
import { viewFor } from "./derive/view";
import { useHealth } from "./hooks/useHealth";
import { useRunPolling } from "./hooks/useRunPolling";
import { useSessionRuns } from "./hooks/useSessionRuns";

export default function App() {
  const [threadId, setThreadId] = useState<string | null>(null);
  const [traceOpen, setTraceOpen] = useState(false);
  const { snapshot, error, reconnecting } = useRunPolling(threadId);
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
        {error !== null && (
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
          <HumanReviewPanel
            threadId={snapshot.thread_id}
            decision={snapshot.pending_decision}
            answered={answeredCount}
            onSubmitted={() => undefined}
          />
        )}
        {view !== "configuration" && view !== "activity" && view !== "human-review" && (
          <Panel title={view}>
            <EmptyState>This view arrives in a later task.</EmptyState>
          </Panel>
        )}
      </div>
    </AppShell>
  );
}
