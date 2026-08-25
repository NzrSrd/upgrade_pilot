/**
 * Status to view. Spec §10's table, and nothing else decides this.
 *
 * One route, and the view is derived rather than navigated. That is what makes
 * "the workflow can never look finished while waiting" enforceable: there is
 * no code path by which a user reaches the report while a decision is
 * outstanding, because reaching it would require a status that says otherwise.
 */

import type { ViewStatus } from "../api/types";

export type View = "configuration" | "activity" | "human-review" | "report" | "error";

export function viewFor(status: ViewStatus): View {
  switch (status) {
    case "idle":
      return "configuration";
    case "queued":
    case "running":
      return "activity";
    case "awaiting_human":
      return "human-review";
    case "completed":
    case "completed_with_warnings":
      return "report";
    case "failed":
    case "orphaned":
      return "error";
  }
}
