/**
 * Pure formatting for `EvidenceRef` -- the citation union shared by
 * `InterruptPayload.evidence`, `RiskFactor.evidence` and
 * `MigrationStep.rationale_evidence`.
 *
 * Moved here from `HumanReviewPanel` (Task 11) once `RiskFactorsTab`'s
 * `EvidenceRefList` needed the same formatting (Task 12, ruling T11b): two
 * formatters for one citation type would be free to disagree about how a
 * citation reads, on the exact surface where CLAUDE.md rule 1 lives. This is
 * a move, not a rewrite -- `describeEvidenceRef`'s behaviour is unchanged
 * from the `describeEvidence` it replaces.
 */

import type { EvidenceRef } from "../api/types";

/**
 * One line of text describing a ref, in the shape that fits its kind.
 *
 * Discriminated on a required, kind-unique field (`file`, then `chunk_id`)
 * rather than on `.kind` itself: `RepoEvidence["kind"]` and its siblings are
 * typed `"repo" | undefined` because the backend field carries a default, so
 * a `switch` on `.kind` alone cannot eliminate the other two shapes from the
 * type and would need a cast to reach `.file` or `.field`. `in` narrows
 * cleanly with no cast, on a field every real payload actually has.
 */
export function describeEvidenceRef(ref: EvidenceRef): string {
  if ("file" in ref) return `${ref.file}:${ref.line}`;
  if ("chunk_id" in ref) {
    return ref.relevance != null
      ? `${ref.source_id} — similarity ${ref.relevance.toFixed(2)}`
      : ref.source_id;
  }
  return `${ref.field} = ${ref.value}`;
}

/**
 * A React list key for a ref, by structural identity rather than by its
 * rendered text. Keying by `describeEvidenceRef`'s output (the original
 * `HumanReviewPanel` did this) collides the moment two distinct refs render
 * identically -- two doc chunks at the same similarity, say -- which is
 * silent rather than loud. `file`+`line`, `chunk_id`, and `field`+`value`
 * each already uniquely identify a ref within its own kind, so there is
 * always a stable identity here; this never falls back to index.
 */
export function evidenceRefKey(ref: EvidenceRef): string {
  if ("file" in ref) return `repo:${ref.file}:${ref.line}`;
  if ("chunk_id" in ref) return `doc:${ref.chunk_id}`;
  return `constraint:${ref.field}:${ref.value}`;
}
