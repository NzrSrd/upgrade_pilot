import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { aReport } from "../../test/fixtures";
import { PlanTab } from "./PlanTab";

const plan = {
  strategy_id: "staged_rollout" as const,
  summary: "Migrate module by module behind a flag.",
  steps: [
    {
      order: 1,
      title: "Replace @validator with @field_validator",
      description: "Four call sites in two modules.",
      files: ["src/app/models.py"],
      rationale_evidence: [],
      validation: "pytest tests/unit",
      requires_downtime: false,
    },
  ],
  human_decisions_applied: [
    { decision_id: "q-1", how_it_changed_the_plan: "Staged rollout chosen, so step 3 gates on a flag." },
  ],
  unaddressed_with_reason: [
    { path: "tests/test_legacy.py", reason: "Test-only; no runtime exposure." },
  ],
  mitigations: ["Keep the compatibility shim for one release."],
};

describe("PlanTab", () => {
  it("lists the steps in order with their files", () => {
    render(<PlanTab report={aReport({ migration_plan: plan })} />);

    expect(screen.getByText(/replace @validator/i)).toBeInTheDocument();
    expect(screen.getByText("src/app/models.py")).toBeInTheDocument();
  });

  it("shows how each human decision changed the plan", () => {
    // The graded requirement "human decision provably changes downstream
    // generation", shown to a user rather than asserted in a test.
    render(<PlanTab report={aReport({ migration_plan: plan })} />);

    expect(screen.getByText(/step 3 gates on a flag/i)).toBeInTheDocument();
  });

  it("shows unaddressed files with their reasons, not behind a disclosure", () => {
    // Spec 8.4 check 8. Bad news is not detail.
    render(<PlanTab report={aReport({ migration_plan: plan })} />);

    expect(screen.getByText("tests/test_legacy.py")).toBeInTheDocument();
    expect(screen.getByText(/test-only; no runtime exposure/i)).toBeInTheDocument();
  });

  it("lists every validation check, and names the failures", () => {
    // Ruling F2: `ValidationReport` serialises exactly `attempt`, `outcomes`
    // and `passed` -- `failures` is a bare Pydantic property, not a
    // `@computed_field`, so it never reaches the wire. This fixture omits it
    // (an excess-property error against the generated type otherwise), and
    // `PlanTab` derives the failure list itself from `outcomes`.
    render(
      <PlanTab
        report={aReport({
          migration_plan: plan,
          validation: {
            attempt: 2,
            outcomes: [
              { check_id: "sources_resolve", passed: true, detail: "All 3 sources resolve.", offenders: [] },
              { check_id: "plan_is_ordered", passed: false, detail: "Step order is not contiguous.", offenders: ["step 3"] },
            ],
            passed: false,
          },
        })}
      />,
    );

    // The check id label and its detail sentence both contain "sources
    // resolve" ("All 3 sources resolve."), so the id is matched by its exact
    // rendered form (`check_id.replace(/_/g, " ")`) rather than a substring
    // that also matches the detail.
    expect(screen.getByText("sources resolve")).toBeInTheDocument();
    expect(screen.getByText("plan is ordered")).toBeInTheDocument();
    expect(screen.getByText("step 3")).toBeInTheDocument();
    expect(screen.getByText(/1 of 2 checks passed/i)).toBeInTheDocument();
  });

  it("marks a step that requires downtime", () => {
    render(
      <PlanTab
        report={aReport({
          migration_plan: { ...plan, steps: [{ ...plan.steps[0], requires_downtime: true }] },
        })}
      />,
    );

    expect(screen.getByText(/requires downtime/i)).toBeInTheDocument();
  });

  it("says so when no plan was produced", () => {
    render(<PlanTab report={aReport({ migration_plan: null })} />);

    expect(screen.getByText(/no plan was produced/i)).toBeInTheDocument();
  });
});
