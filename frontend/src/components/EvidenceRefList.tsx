/**
 * The single renderer for an `EvidenceRef[]` (ruling T11b) -- shared by
 * `RiskFactorsTab`, `HumanReviewPanel` and (task 13) `PlanTab`, so the
 * product has exactly one way to render a citation rather than several free
 * to disagree.
 *
 * Lives here rather than under `report/` (ruling F8): `HumanReviewPanel` is
 * not a report component -- the human-review panel is the *primary*
 * interaction, not a subordinate of the report -- so a shared primitive with
 * importers on both sides belongs beside `ui.tsx`, not inside a sibling
 * tab's file.
 *
 * The formatting itself (`describeEvidenceRef`) lives in `derive/` because it
 * is a pure function of the ref; this component's own job is the list
 * chrome and the one thing the formatter cannot show -- a repo ref's
 * snippet, when it carries one.
 */

import type { EvidenceRef } from "../api/types";
import { describeEvidenceRef, evidenceRefKey } from "../derive/evidence";
import { Mono } from "./ui";

export function EvidenceRefList({ refs }: { refs: readonly EvidenceRef[] }) {
  return (
    <ul className="mt-2 space-y-1 border-l border-edge pl-3">
      {refs.map((ref) => (
        <li key={evidenceRefKey(ref)} className="text-xs text-ink-muted">
          <Mono>{describeEvidenceRef(ref)}</Mono>
          {/* `snippet` is optional and nullable on `RepoEvidence`; loose
              (`!= null`), not strict, or the common case of no snippet sent
              would render an empty `<pre>` (ruling N1). */}
          {"file" in ref && ref.snippet != null && (
            <pre className="mt-1 overflow-x-auto rounded bg-surface-sunken p-2 font-mono text-[12px] text-ink-muted">
              {ref.snippet}
            </pre>
          )}
        </li>
      ))}
    </ul>
  );
}
