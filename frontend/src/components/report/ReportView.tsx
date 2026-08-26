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

import { useRef, useState } from "react";
import type { KeyboardEvent } from "react";

import type { RunSnapshot } from "../../api/types";
import { STEPS } from "../../derive/steps";
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

/** `id`s that tie a tab button to the panel it discloses, for `aria-controls`/`aria-labelledby`. */
function tabId(id: ReportTab): string {
  return `report-tab-${id}`;
}
function panelId(id: ReportTab): string {
  return `report-panel-${id}`;
}

/**
 * The errors a run recorded, on the report that was produced despite them.
 *
 * This exists because `snapshot.errors` had exactly one reader — `ErrorView`,
 * which renders for `failed` and `orphaned` only. A run that finished *with
 * warnings* therefore had no route to its own errors at all: the graph
 * continues after a node fails so the report can say what was established
 * (`graph/nodes/base.py`), and the report then said nothing about why the
 * rest was missing. A repository path that did not exist and a dependency
 * absent from every manifest both arrived here as a page of zeroes.
 *
 * `AppError.message` is the user-facing half by rule 27, and `detail` never
 * leaves the server. The step label comes from `STEPS`, so a user reads the
 * same name here as on the timeline rather than a node id.
 *
 * It does not say what is missing from the report as a result. Some of these
 * are fatal to a whole stage and some cost only a sentence — `assess_risk`
 * keeps every factor and level when its narrative call fails — and the honest
 * thing is to report the error and let the tabs below show what survived.
 */
function RecordedErrors({ snapshot }: { snapshot: RunSnapshot }) {
  const errors = snapshot.errors ?? [];
  if (errors.length === 0) {
    return null;
  }

  return (
    <div
      role="alert"
      className="rounded-md border border-risk-high/50 bg-risk-high/10 px-3 py-2 text-sm text-risk-high"
    >
      <p className="font-medium">
        {errors.length === 1 ? "1 error was recorded" : `${errors.length} errors were recorded`}{" "}
        during this run.
      </p>
      <ul className="mt-1.5 space-y-1">
        {errors.map((error, index) => (
          <li key={`${error.node ?? "run"}:${error.code}:${index}`}>
            <span className="text-ink-muted">{labelFor(error.node)} — </span>
            {error.message}
          </li>
        ))}
      </ul>
    </div>
  );
}

/** A node id as the user's own step name. Unattributed errors are the run's. */
function labelFor(node: string | null | undefined): string {
  if (node === null || node === undefined) {
    return "This run";
  }
  return STEPS.find((step) => step.node === node)?.label ?? node;
}

export function ReportView({ snapshot }: { snapshot: RunSnapshot }) {
  const [tab, setTab] = useState<ReportTab>("overview");
  // Roving tabindex (fix-round-1 finding 3): only the selected tab is a
  // stop on the page's Tab order; arrow keys move both the selection and
  // DOM focus among the others, per the ARIA APG tabs pattern. Refs are
  // used rather than an effect so focus moves synchronously with the
  // keypress that caused it, not on every render (which would steal focus
  // on mount).
  const tabButtonRefs = useRef<(HTMLButtonElement | null)[]>([]);
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
      <RecordedErrors snapshot={snapshot} />

      {report.completed_with_warnings && (
        // Fix-round-1 finding 2: DESIGN.md's token table assigns "failed
        // validation checks" to `risk-high` explicitly, not `risk-medium`.
        <p
          role="alert"
          className="rounded-md border border-risk-high/50 bg-risk-high/10 px-3 py-2 text-sm text-risk-high"
        >
          Validation did not pass. The failed checks are listed under Plan — the plan below is
          reported with them rather than without them.
        </p>
      )}

      <div role="tablist" aria-label="Report sections" className="flex gap-1 border-b border-edge">
        {TABS.map((each, index) => (
          <button
            key={each.id}
            ref={(el) => {
              tabButtonRefs.current[index] = el;
            }}
            type="button"
            role="tab"
            id={tabId(each.id)}
            aria-selected={tab === each.id}
            aria-controls={panelId(each.id)}
            tabIndex={tab === each.id ? 0 : -1}
            onClick={() => setTab(each.id)}
            onKeyDown={(event: KeyboardEvent<HTMLButtonElement>) => {
              let nextIndex: number | null = null;
              switch (event.key) {
                case "ArrowRight":
                  nextIndex = (index + 1) % TABS.length;
                  break;
                case "ArrowLeft":
                  nextIndex = (index - 1 + TABS.length) % TABS.length;
                  break;
                case "Home":
                  nextIndex = 0;
                  break;
                case "End":
                  nextIndex = TABS.length - 1;
                  break;
                default:
                  return;
              }
              event.preventDefault();
              setTab(TABS[nextIndex].id);
              tabButtonRefs.current[nextIndex]?.focus();
            }}
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

      <div role="tabpanel" id={panelId(tab)} aria-labelledby={tabId(tab)} tabIndex={0}>
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
    </div>
  );
}
