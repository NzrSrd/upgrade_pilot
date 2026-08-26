import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { aRiskAnalysis, aSnapshot } from "../test/fixtures";
import { ActivityTimeline } from "./ActivityTimeline";

describe("ActivityTimeline", () => {
  it("says nothing has started for a fresh queued run", () => {
    render(<ActivityTimeline snapshot={aSnapshot({ status: "queued", completed_steps: [] })} />);

    expect(screen.getByText(/nothing has started yet/i)).toBeInTheDocument();
  });

  it("says nothing has started when there is no snapshot at all", () => {
    render(<ActivityTimeline snapshot={null} />);

    expect(screen.getByText(/nothing has started yet/i)).toBeInTheDocument();
  });

  it("distinguishes a resumed orphan's queued state from a fresh one", () => {
    // Finding I1/I2. After a resume the registry reports `queued`
    // (`api/registry.py` sets `waiting=True` before the task starts) even
    // though the checkpoint already recorded work. `queued` alone cannot
    // tell the two apart; `completed_steps` can. Before this fix, a
    // resumed run read "nothing has started yet" directly beneath
    // `WorkflowTimeline`'s own five completed steps.
    render(
      <ActivityTimeline
        snapshot={aSnapshot({
          status: "queued",
          completed_steps: ["analyze_repo", "inspect_dependency", "agentic_rag", "assess_risk", "human_review"],
        })}
      />,
    );

    expect(screen.queryByText(/nothing has started yet/i)).not.toBeInTheDocument();
    expect(screen.getByText(/5 of 8 steps are already recorded/i)).toBeInTheDocument();
  });

  it("renders every confidence ceiling beside the figure, never a bare percentage", () => {
    // Finding I5. `docs/ui/DESIGN.md`: "Confidence renders with its reason
    // or not at all." `RiskAnalysis.confidence_ceilings` was already in
    // hand at this call site and unrendered.
    render(
      <ActivityTimeline
        snapshot={aSnapshot({
          status: "running",
          risk_analysis: aRiskAnalysis({
            confidence: 0.3,
            confidence_ceilings: [
              { reason: "no supporting evidence was retrieved", ceiling: 0.3 },
            ],
          }),
        })}
      />,
    );

    expect(screen.getByText("30%")).toBeInTheDocument();
    expect(screen.getByText(/no supporting evidence was retrieved/i)).toBeInTheDocument();
  });

  it("renders a bare confidence figure honestly when nothing capped it", () => {
    // An empty `confidence_ceilings` is itself a real, un-capped state --
    // mirrors `OverviewTab.tsx`'s `VerdictDetail` rendering of the same
    // field, which also renders the figure alone when the list is empty.
    render(
      <ActivityTimeline
        snapshot={aSnapshot({
          status: "running",
          risk_analysis: aRiskAnalysis({ confidence: 0.9, confidence_ceilings: [] }),
        })}
      />,
    );

    expect(screen.getByText("90%")).toBeInTheDocument();
  });
});
