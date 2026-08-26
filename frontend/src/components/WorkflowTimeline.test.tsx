import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { anApiError, aRiskAnalysis, aSnapshot } from "../test/fixtures";
import { WorkflowTimeline } from "./WorkflowTimeline";

describe("WorkflowTimeline", () => {
  it("shows all eight steps by their user-facing labels", () => {
    render(<WorkflowTimeline snapshot={null} />);

    for (const label of [
      "Repository Analysis",
      "Dependency Analysis",
      "Evidence Retrieval",
      "Risk Assessment",
      "Human Review",
      "Migration Plan",
      "Validation",
      "Report",
    ]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
  });

  it("names each step's state in text, not only in colour", () => {
    // DESIGN.md §Accessibility. A screen reader and a colour-blind user both
    // need the word.
    render(
      <WorkflowTimeline
        snapshot={aSnapshot({
          status: "running",
          completed_steps: ["analyze_repo"],
          current_step: "inspect_dependency",
        })}
      />,
    );

    expect(screen.getByRole("listitem", { name: /Repository Analysis: completed/i })).toBeInTheDocument();
    expect(screen.getByRole("listitem", { name: /Dependency Analysis: running/i })).toBeInTheDocument();
    expect(screen.getByRole("listitem", { name: /Validation: pending/i })).toBeInTheDocument();
  });

  it("shows human review as waiting and later steps as still incomplete", () => {
    // The guarantee: the workflow can never look finished while it waits.
    render(
      <WorkflowTimeline
        snapshot={aSnapshot({
          status: "awaiting_human",
          completed_steps: ["analyze_repo", "inspect_dependency", "agentic_rag", "assess_risk"],
          current_step: "human_review",
        })}
      />,
    );

    expect(screen.getByRole("listitem", { name: /Human Review: waiting for you/i })).toBeInTheDocument();
    expect(screen.getByRole("listitem", { name: /Migration Plan: pending/i })).toBeInTheDocument();
    expect(screen.getByRole("listitem", { name: /Report: pending/i })).toBeInTheDocument();
  });

  it("says why a skipped step was skipped", () => {
    // Spec 8.2. Without the reason, "skipped" reads as an omission rather than
    // a decision the user's constraints already made.
    render(
      <WorkflowTimeline
        snapshot={aSnapshot({
          status: "completed",
          completed_steps: [
            "analyze_repo",
            "inspect_dependency",
            "agentic_rag",
            "assess_risk",
            "generate_plan",
            "validate_plan",
            "finalize",
          ],
          current_step: null,
          risk_analysis: aRiskAnalysis(),
        })}
      />,
    );

    expect(
      screen.getByRole("listitem", { name: /Human Review: skipped, resolved by constraints/i }),
    ).toBeInTheDocument();
  });

  it("marks the step a failed run stopped on", () => {
    render(
      <WorkflowTimeline
        snapshot={aSnapshot({
          status: "failed",
          completed_steps: ["analyze_repo"],
          current_step: "inspect_dependency",
        })}
      />,
    );

    expect(screen.getByRole("listitem", { name: /Dependency Analysis: failed/i })).toBeInTheDocument();
  });
  it("does not credit the constraints for a step the run never reached", () => {
    // The degraded run: `analyze_repo` failed, so there was never a question
    // for the constraints to settle. Saying they settled one would present a
    // broken run as a normal one -- the exact thing the narrow `skipped` rule
    // in `derive/steps.ts` exists to prevent.
    render(
      <WorkflowTimeline
        snapshot={aSnapshot({
          status: "completed_with_warnings",
          completed_steps: [
            "analyze_repo",
            "inspect_dependency",
            "agentic_rag",
            "assess_risk",
            "generate_plan",
            "validate_plan",
            "finalize",
          ],
          current_step: null,
          errors: [anApiError()],
        })}
      />,
    );

    expect(screen.getByRole("listitem", { name: /Human Review: not reached/i })).toBeInTheDocument();
    expect(screen.queryByRole("listitem", { name: /resolved by constraints/i })).toBeNull();
  });

  it("marks a step that errored and produced nothing, even on a run that finished", () => {
    render(
      <WorkflowTimeline
        snapshot={aSnapshot({
          status: "completed_with_warnings",
          completed_steps: [
            "analyze_repo",
            "inspect_dependency",
            "agentic_rag",
            "assess_risk",
            "generate_plan",
            "validate_plan",
            "finalize",
          ],
          current_step: null,
          errors: [anApiError()],
        })}
      />,
    );

    expect(screen.getByRole("listitem", { name: /Repository Analysis: failed/i })).toBeInTheDocument();
  });
});
