/**
 * Three regions plus a drawer.
 *
 * `metrics` is a sibling of `children` rather than something a view renders,
 * so it stays mounted and updating while the workspace changes underneath it —
 * token and cost tracking is a graded capability, not a panel one view owns.
 */

import type { ReactNode } from "react";

export function AppShell({
  topBar,
  sidebar,
  metrics,
  drawer,
  children,
}: {
  topBar: ReactNode;
  sidebar: ReactNode;
  metrics: ReactNode;
  drawer: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="flex h-screen flex-col bg-surface text-ink">
      {topBar}
      <div className="flex min-h-0 flex-1">
        {sidebar}
        <main className="min-w-0 flex-1 overflow-y-auto p-5">{children}</main>
        <aside className="hidden w-72 shrink-0 overflow-y-auto border-l border-edge bg-surface-sunken p-3 xl:block">
          {metrics}
        </aside>
      </div>
      {drawer}
    </div>
  );
}
