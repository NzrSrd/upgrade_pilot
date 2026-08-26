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
export type RepoAnalysis = S["RepoAnalysis"];
export type DetectedVersion = S["DetectedVersion"];
export type DependencyRole = S["DependencyRole"];
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
 * Statuses where the poll loop stops scheduling its next tick on its own.
 *
 * This describes the poll loop's behaviour, not the run's finality — that
 * distinction is the entire reason for the name. `completed`,
 * `completed_with_warnings` and `failed` really are terminal: no code path
 * changes them again. `orphaned` is not — it is the one stopped-but-resumable
 * state, which is why it has a resume affordance at all. It belongs in this
 * set because no process is currently advancing the run, so ticking a fixed
 * question every second is pointless until someone acts — not because the run
 * itself cannot change. An explicit resume (`useRunPolling`'s `restart`)
 * re-enters the loop; removing `orphaned` from this set instead would poll a
 * genuinely abandoned run once a second forever, which is what put it here in
 * the first place.
 *
 * `awaiting_human` is deliberately absent for the opposite reason: a run
 * awaiting a human *can* change without this client doing anything — another
 * client may answer it — and the transition out of the decision panel is the
 * single thing the user is watching for.
 */
export const POLLING_STOPS_ON: ReadonlySet<RunStatus> = new Set([
  "completed",
  "completed_with_warnings",
  "failed",
  "orphaned",
]);

/**
 * The frontend's own extra states, neither of which the backend enum has a
 * member for, and for the same reason: each describes what the *client*
 * knows rather than a status derived from a checkpoint.
 *
 * `idle` — no run has been started, so there is no status to read at all.
 *
 * `unavailable` — a status poll came back and refused (fix round 4):
 * `getStatus` resolved to a definite answer, and the answer was that this
 * run cannot be read (an unknown thread id, most often). This is not a
 * backend status — the backend never emits it, because from the backend's
 * side there is no run object to report a status *for* — so it cannot live
 * in `RunStatus`, only here, exactly where `idle` already lives for the
 * identical reason.
 *
 * Both are added as real members rather than handled by an override at a
 * call site (fix round 3's original, now-corrected approach) precisely to
 * put `viewFor`'s missing `default` to work: adding a member for a genuine
 * frontend view state *uses* the exhaustiveness check, forcing every surface
 * that switches on status — `viewFor`, `TopBar`'s `WORDING` — to say what it
 * does with the new state, rather than leaving one caller patched and the
 * rest silently unaware. Adding a `case` to `viewFor` for a status the
 * *backend* invented would erode that guarantee; adding one for a status
 * the *frontend* genuinely has is what the guarantee is for.
 */
export type ViewStatus = RunStatus | "idle" | "unavailable";
