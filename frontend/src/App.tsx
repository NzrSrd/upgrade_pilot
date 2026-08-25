import { useState } from "react";

import type { ViewStatus } from "./api/types";
import { AppShell } from "./components/AppShell";
import { ConfigurationForm } from "./components/ConfigurationForm";
import { LeftSidebar } from "./components/LeftSidebar";
import { TopBar } from "./components/TopBar";
import { WorkflowTimeline } from "./components/WorkflowTimeline";
import { EmptyState, Panel } from "./components/ui";
import { viewFor } from "./derive/view";
import { useHealth } from "./hooks/useHealth";
import { useRunPolling } from "./hooks/useRunPolling";
import { useSessionRuns } from "./hooks/useSessionRuns";

export default function App() {
  const [threadId, setThreadId] = useState<string | null>(null);
  const { snapshot, error, reconnecting } = useRunPolling(threadId);
  const { health } = useHealth();
  const { runs, remember } = useSessionRuns();

  const status: ViewStatus = threadId === null ? "idle" : (snapshot?.status ?? "queued");
  const view = viewFor(status);
  const summary = runs.find((run) => run.threadId === threadId) ?? null;

  return (
    <AppShell
      topBar={
        <TopBar
          status={status}
          reconnecting={reconnecting}
          summary={summary}
          onOpenTrace={() => undefined}
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
      metrics={<Panel title="Telemetry"><EmptyState>Task 10.</EmptyState></Panel>}
      drawer={null}
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
        {view !== "configuration" && (
          <Panel title={view}>
            <EmptyState>This view arrives in a later task.</EmptyState>
          </Panel>
        )}
      </div>
    </AppShell>
  );
}
