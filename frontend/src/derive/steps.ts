/**
 * The eight workflow steps and their six states.
 *
 * `human_review` is the only skippable step, and `skipped` exists for it
 * alone. Spec 8.2: when the user's constraints already settle the choice, no
 * interrupt fires. Rendering that as a pending or missing step would make a
 * correct run look like it lost one — while rendering a *genuine* gap
 * elsewhere as "skipped" would make a broken run look normal. So the rule is
 * narrow on purpose.
 *
 * Two things this module decides that nothing else may re-derive.
 *
 * **A step that recorded an error and produced nothing is `failed`, whatever
 * `completed_steps` says.** The graph continues after a node fails so the
 * report can say what *was* established (`graph/nodes/base.py`), which means
 * a failed node still reaches `completed_steps` and every later node still
 * runs. Reading that list alone painted the node that refused a bad
 * repository path green, and put eight checkmarks over a report of zeroes.
 *
 * **`skipped` carries the reason it was skipped, and the reason is checked.**
 * "Resolved by constraints" is a claim about a question that existed; a run
 * that never produced an assessment had no question to settle, and saying the
 * constraints settled one invents a reason the snapshot contradicts.
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

/** Why a `skipped` step was skipped. Only `skipped` carries one. */
export type SkipReason = "resolved-by-constraints" | "not-reached";

export type Step = { node: string; label: string; state: StepState; reason?: SkipReason };

/**
 * What each step leaves in the snapshot when it does its work.
 *
 * This table is what separates a step that *failed* from one that *degraded*,
 * and the distinction is not theoretical: `assess_risk` records an error when
 * the narrative call fails while keeping every factor and level it measured —
 * "the risk factors and levels are unaffected; only the written narrative is
 * missing" (`graph/nodes/judgment.py`). `agentic_rag` has the same shape for
 * a retrieval round that partly failed. An error alone therefore cannot mean
 * failure; an error with nothing to show for the step can.
 *
 * `inspect_dependency` and `human_review` map to the closest thing the
 * snapshot exposes. The detected version lives inside `final_report`'s repo
 * analysis rather than at the top level, so `inspect_dependency` has no
 * output of its own to check here and an error against it is taken at face
 * value — which is correct today, because `traced()` is its only error path.
 */
const PRODUCED: Record<string, (snapshot: RunSnapshot) => boolean> = {
  // Every field is optional in the generated type only because every Pydantic
  // field carries a default, and `snapshot_response` never sends `undefined`
  // in place of `null`. Collapsed the same way `ReportView` collapses
  // `final_report` (ruling T10b), so an absent field and an explicitly-null
  // one answer identically -- a bare `!== null` on an optional field would
  // read `undefined` as "produced".
  analyze_repo: (snapshot) => (snapshot.affected_files ?? []).length > 0,
  inspect_dependency: () => false,
  agentic_rag: (snapshot) => (snapshot.rag_context ?? null) !== null,
  assess_risk: (snapshot) => (snapshot.risk_analysis ?? null) !== null,
  human_review: (snapshot) => (snapshot.human_decisions ?? []).length > 0,
  generate_plan: (snapshot) => (snapshot.migration_plan ?? null) !== null,
  validate_plan: (snapshot) => (snapshot.validation ?? null) !== null,
  finalize: (snapshot) => (snapshot.final_report ?? null) !== null,
};

function erroredWithNothingToShow(snapshot: RunSnapshot, node: string): boolean {
  const errored = (snapshot.errors ?? []).some((error) => error.node === node);
  if (!errored) {
    return false;
  }
  const produced = PRODUCED[node];
  return produced === undefined ? true : !produced(snapshot);
}

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
    // Failed outranks completed, for the reason in the module docstring: the
    // graph puts a failed node in `completed_steps` on purpose, so the list
    // cannot be the last word on whether the step worked.
    if (erroredWithNothingToShow(snapshot, step.node)) {
      return { ...step, state: "failed" };
    }
    if (completed.has(step.node)) {
      return { ...step, state: "completed" };
    }
    if (snapshot.status === "failed" && step.node === snapshot.current_step) {
      return { ...step, state: "failed" };
    }
    if (step.node === SKIPPABLE && laterCompleted(index)) {
      // A question can only have been settled by the constraints if there was
      // an assessment to settle. Without one the step was not skipped by a
      // decision — the run simply never got far enough to ask.
      const reason: SkipReason =
        (snapshot.risk_analysis ?? null) !== null ? "resolved-by-constraints" : "not-reached";
      return { ...step, state: "skipped", reason };
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
