/**
 * The report, and the only tab bar in the application.
 *
 * Tabs live here and nowhere else. Workflow view selection stays derived from
 * status, so there is no navigation that walks past an unanswered decision or
 * reaches a report that does not exist yet — which is what a navigable
 * workflow tab bar would have permitted.
 *
 * Five tabs, and the two the design pack asked for are absent for stated
 * reasons. There is no unified-diff Changes tab: `MigrationStep` carries no
 * patch and `validate_plan` has no check that a patch parses or applies, so
 * the tab would display LLM-authored code with nothing verifying it. `Code`
 * shows the cited *existing* code instead. And there is no PR Draft tab —
 * writing to GitHub is sub-project 2, and a PR body behind a button that
 * cannot create anything offers a capability the product does not have.
 */

import { useState } from "react";

import type { RunSnapshot } from "../../api/types";
import { EmptyState, Panel } from "../ui";
import { CodeTab } from "./CodeTab";
import { EvidenceTab } from "./EvidenceTab";
import { OverviewTab } from "./OverviewTab";
import { PlanTab } from "./PlanTab";
import { RiskFactorsTab } from "./RiskFactorsTab";

export type ReportTab = "overview" | "risk" | "evidence" | "plan" | "code";

const TABS: { id: ReportTab; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "risk", label: "Risk Factors" },
  { id: "evidence", label: "Evidence" },
  { id: "plan", label: "Plan" },
  { id: "code", label: "Code" },
];

export function ReportView({ snapshot }: { snapshot: RunSnapshot }) {
  const [tab, setTab] = useState<ReportTab>("overview");
  // `final_report` is optional in the generated type only because every
  // Pydantic field carries a default -- it is genuinely `None` until the run
  // finishes, and `snapshot_response` never sends `undefined` in its place.
  // Collapsed to `null` once here (ruling T10b) so the check below is a
  // plain, correct `=== null` rather than one that treats an absent field
  // differently from an explicitly-null one (ruling N1).
  const report = snapshot.final_report ?? null;

  if (report === null) {
    return (
      <Panel title="Report">
        <EmptyState>
          No report was produced for this run. The activity trace shows how far it got.
        </EmptyState>
      </Panel>
    );
  }

  return (
    <div className="space-y-4">
      {report.completed_with_warnings && (
        <p
          role="alert"
          className="rounded-md border border-risk-medium/50 bg-risk-medium/10 px-3 py-2 text-sm text-risk-medium"
        >
          Validation did not pass. The failed checks are listed under Plan — the plan below is
          reported with them rather than without them.
        </p>
      )}

      <div role="tablist" aria-label="Report sections" className="flex gap-1 border-b border-edge">
        {TABS.map((each) => (
          <button
            key={each.id}
            type="button"
            role="tab"
            aria-selected={tab === each.id}
            onClick={() => setTab(each.id)}
            className={`-mb-px border-b-2 px-3 py-2 text-sm ${
              tab === each.id
                ? "border-ink text-ink"
                : "border-transparent text-ink-muted hover:text-ink"
            }`}
          >
            {each.label}
          </button>
        ))}
      </div>

      {tab === "overview" && <OverviewTab report={report} />}
      {/* `risk_analysis` is optional in the generated type for the same
          reason `final_report` is above; normalised to `null` at this call
          site so `RiskFactorsTab`'s own `analysis === null` check stays
          strict and correct (ruling N1/T10b). */}
      {tab === "risk" && <RiskFactorsTab analysis={report.risk_analysis ?? null} />}
      {tab === "evidence" && <EvidenceTab report={report} snapshot={snapshot} />}
      {tab === "plan" && <PlanTab report={report} />}
      {tab === "code" && <CodeTab report={report} />}
    </div>
  );
}
