/**
 * The eight workflow steps and their six states.
 *
 * `human_review` is the only skippable step, and `skipped` exists for it
 * alone. Spec 8.2: when the user's constraints already settle the choice, no
 * interrupt fires. Rendering that as a pending or missing step would make a
 * correct run look like it lost one — while rendering a *genuine* gap
 * elsewhere as "skipped" would make a broken run look normal. So the rule is
 * narrow on purpose.
 */

import type { RunSnapshot } from "../api/types";

export const STEPS = [
  { node: "analyze_repo", label: "Repository Analysis" },
  { node: "inspect_dependency", label: "Dependency Analysis" },
  { node: "agentic_rag", label: "Evidence Retrieval" },
  { node: "assess_risk", label: "Risk Assessment" },
  { node: "human_review", label: "Human Review" },
  { node: "generate_plan", label: "Migration Plan" },
  { node: "validate_plan", label: "Validation" },
  { node: "finalize", label: "Report" },
] as const;

const SKIPPABLE = "human_review";

export type StepState = "pending" | "running" | "completed" | "skipped" | "awaiting" | "failed";

export type Step = { node: string; label: string; state: StepState };

export function stepStates(snapshot: RunSnapshot | null): Step[] {
  if (snapshot === null) {
    return STEPS.map((step) => ({ ...step, state: "pending" as StepState }));
  }

  const completed = new Set(snapshot.completed_steps);
  const laterCompleted = (index: number) =>
    STEPS.slice(index + 1).some((step) => completed.has(step.node));

  return STEPS.map((step, index): Step => {
    // Awaiting outranks completed: `completed_steps` records a node once, so a
    // second interrupt on `human_review` would otherwise render as a step
    // already behind us.
    if (step.node === SKIPPABLE && snapshot.status === "awaiting_human") {
      return { ...step, state: "awaiting" };
    }
    if (completed.has(step.node)) {
      return { ...step, state: "completed" };
    }
    if (snapshot.status === "failed" && step.node === snapshot.current_step) {
      return { ...step, state: "failed" };
    }
    if (step.node === SKIPPABLE && laterCompleted(index)) {
      return { ...step, state: "skipped" };
    }
    // Only a live run has something running. An orphaned run's process is
    // gone, so a spinner on the step it died in is the exact misreport that
    // status exists to prevent.
    const live = snapshot.status === "running" || snapshot.status === "queued";
    if (live && step.node === snapshot.current_step) {
      return { ...step, state: "running" };
    }
    return { ...step, state: "pending" };
  });
}
