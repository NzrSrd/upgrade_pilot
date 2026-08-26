/**
 * Named aliases into the generated schema, and the two status sets that are
 * needed at runtime.
 *
 * Components import from here and never from `./schema` directly, so a rename
 * in the backend's models shows up as one broken line in this file rather than
 * thirty across the tree. Nothing here is hand-written structure: every alias
 * resolves to a Pydantic model, which is what makes drift impossible rather
 * than merely unlikely (spec §10).
 */

import type { components } from "./schema";

type S = components["schemas"];

export type RunSnapshot = S["RunSnapshot"];
export type RunStatus = S["RunStatus"];
export type UsageView = S["UsageView"];
export type TraceEvent = S["TraceEvent"];
export type InterruptPayload = S["InterruptPayload"];
export type DecisionOption = S["DecisionOption"];
export type RepoEvidence = S["RepoEvidence"];
export type DocEvidence = S["DocEvidence"];
export type ConstraintEvidence = S["ConstraintEvidence"];
/** The union `InterruptPayload.evidence`, `DecisionOption.supporting_evidence` and `MigrationStep.rationale_evidence` all share. */
export type EvidenceRef = RepoEvidence | DocEvidence | ConstraintEvidence;
export type HumanDecision = S["HumanDecision"];
export type DecisionApplication = S["DecisionApplication"];
export type RiskAnalysis = S["RiskAnalysis"];
export type RiskFactor = S["RiskFactor"];
export type ConfidenceCeiling = S["ConfidenceCeiling"];
export type MigrationPlan = S["MigrationPlan"];
export type MigrationStep = S["MigrationStep"];
export type UnaddressedFile = S["UnaddressedFile"];
export type ValidationReport = S["ValidationReport"];
export type ValidationOutcome = S["ValidationOutcome"];
export type FinalReport = S["FinalReport"];
export type AffectedFile = S["AffectedFile"];
export type UsageSite = S["UsageSite"];
export type BreakingChange = S["BreakingChange"];
export type SourceRef = S["SourceRef"];
export type RagContext = S["RagContext"];
export type ApiError = S["ApiError"];
export type ErrorResponse = S["ErrorResponse"];
export type StartRunRequest = S["StartRunRequest"];
export type StartResponse = S["StartResponse"];
export type ResumeRequest = S["ResumeRequest"];
export type DecisionInput = S["DecisionInput"];
export type UserConstraints = S["UserConstraints"];
export type HealthResponse = S["HealthResponse"];
export type RiskLevel = S["RiskLevel"];
export type EffortLevel = S["EffortLevel"];
export type Severity = S["Severity"];
export type ErrorCode = S["ErrorCode"];

/** Every status the backend derives. `idle` is not among them — see below. */
export const ALL_STATUSES: ReadonlySet<RunStatus> = new Set([
  "queued",
  "running",
  "awaiting_human",
  "completed",
  "completed_with_warnings",
  "failed",
  "orphaned",
]);

/**
 * Statuses where polling stops because nothing further will change on its own.
 *
 * `orphaned` is here and `awaiting_human` is not, and both are deliberate. An
 * orphaned run's process is gone, so continuing to poll is asking a question
 * whose answer cannot change until someone resumes it. A run awaiting a human
 * *can* change without this client doing anything — another client may answer
 * it — and the transition out of the decision panel is the single thing the
 * user is watching for.
 */
export const TERMINAL_STATUSES: ReadonlySet<RunStatus> = new Set([
  "completed",
  "completed_with_warnings",
  "failed",
  "orphaned",
]);

/**
 * The frontend's own extra state: no run has been started, so there is no
 * status to read. The backend enum has seven members and no `idle` — status is
 * derived from a checkpoint, and a run that does not exist has no checkpoint.
 */
export type ViewStatus = RunStatus | "idle";
