import { describe, expect, it } from "vitest";

import { aSnapshot } from "../test/fixtures";
import { STEPS, stepStates } from "./steps";

const stateOf = (steps: ReturnType<typeof stepStates>, node: string) =>
  steps.find((step) => step.node === node)?.state;

describe("stepStates", () => {
  it("always reports all eight steps, in workflow order", () => {
    expect(STEPS.map((step) => step.node)).toEqual([
      "analyze_repo",
      "inspect_dependency",
      "agentic_rag",
      "assess_risk",
      "human_review",
      "generate_plan",
      "validate_plan",
      "finalize",
    ]);
    expect(stepStates(null)).toHaveLength(8);
  });

  it("reports every step pending before a run exists", () => {
    expect(stepStates(null).every((step) => step.state === "pending")).toBe(true);
  });

  it("marks finished steps completed and the current one running", () => {
    const steps = stepStates(
      aSnapshot({
        status: "running",
        completed_steps: ["analyze_repo", "inspect_dependency"],
        current_step: "agentic_rag",
      }),
    );

    expect(stateOf(steps, "analyze_repo")).toBe("completed");
    expect(stateOf(steps, "inspect_dependency")).toBe("completed");
    expect(stateOf(steps, "agentic_rag")).toBe("running");
    expect(stateOf(steps, "assess_risk")).toBe("pending");
  });

  it("marks human review as awaiting, and leaves later steps incomplete", () => {
    // The guarantee this encodes: the workflow can never look finished while
    // it is waiting for an answer.
    const steps = stepStates(
      aSnapshot({
        status: "awaiting_human",
        completed_steps: ["analyze_repo", "inspect_dependency", "agentic_rag", "assess_risk"],
        current_step: "human_review",
      }),
    );

    expect(stateOf(steps, "human_review")).toBe("awaiting");
    expect(stateOf(steps, "generate_plan")).toBe("pending");
    expect(stateOf(steps, "validate_plan")).toBe("pending");
    expect(stateOf(steps, "finalize")).toBe("pending");
  });

  it("marks human review skipped when constraints settled the question", () => {
    // Spec 8.2: when the constraints already decide, no interrupt fires and
    // the trace records "resolved by constraints". Without a skipped state a
    // correct run looks like it lost a step.
    const steps = stepStates(
      aSnapshot({
        status: "running",
        completed_steps: [
          "analyze_repo",
          "inspect_dependency",
          "agentic_rag",
          "assess_risk",
          "generate_plan",
        ],
        current_step: "validate_plan",
      }),
    );

    expect(stateOf(steps, "human_review")).toBe("skipped");
    expect(stateOf(steps, "generate_plan")).toBe("completed");
  });

  it("marks human review skipped on a completed run that was never asked", () => {
    const steps = stepStates(
      aSnapshot({
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
      }),
    );

    expect(stateOf(steps, "human_review")).toBe("skipped");
    expect(stateOf(steps, "finalize")).toBe("completed");
  });

  it("prefers awaiting over completed on a second interrupt", () => {
    // `human_decisions` is an append channel so interrupts can fire in
    // sequence, and `completed_steps` records a node once. A second question
    // must not render as a step already behind us.
    const steps = stepStates(
      aSnapshot({
        status: "awaiting_human",
        completed_steps: [
          "analyze_repo",
          "inspect_dependency",
          "agentic_rag",
          "assess_risk",
          "human_review",
        ],
        current_step: "human_review",
      }),
    );

    expect(stateOf(steps, "human_review")).toBe("awaiting");
  });

  it("marks the step a failed run stopped on as failed", () => {
    const steps = stepStates(
      aSnapshot({
        status: "failed",
        completed_steps: ["analyze_repo"],
        current_step: "inspect_dependency",
      }),
    );

    expect(stateOf(steps, "analyze_repo")).toBe("completed");
    expect(stateOf(steps, "inspect_dependency")).toBe("failed");
    expect(stateOf(steps, "agentic_rag")).toBe("pending");
  });

  it("does not mark anything running on an orphaned run", () => {
    // Nothing is running: the process is gone. Showing a spinner on the step
    // it died in is the exact misreport `orphaned` exists to prevent.
    const steps = stepStates(
      aSnapshot({
        status: "orphaned",
        completed_steps: ["analyze_repo"],
        current_step: "inspect_dependency",
      }),
    );

    expect(steps.some((step) => step.state === "running")).toBe(false);
    expect(stateOf(steps, "inspect_dependency")).toBe("pending");
  });

  it("never skips a step that is not human review", () => {
    // Only `human_review` is skippable. A gap anywhere else is a defect, and
    // rendering it as "skipped" would present a broken run as a normal one.
    const steps = stepStates(
      aSnapshot({
        status: "running",
        completed_steps: ["analyze_repo", "agentic_rag"],
        current_step: "assess_risk",
      }),
    );

    expect(stateOf(steps, "inspect_dependency")).toBe("pending");
    expect(steps.filter((step) => step.state === "skipped")).toHaveLength(0);
  });
});
