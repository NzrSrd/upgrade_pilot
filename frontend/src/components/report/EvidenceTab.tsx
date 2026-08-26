/**
 * Stub -- replaced in Task 13, which builds the real Evidence tab (repository
 * evidence ranked first, retrieved documents with their selection state, and
 * the RAG stop reason). Exists only so `ReportView`'s five-tab shell has
 * something to mount in this task.
 *
 * Two props, not one (ruling P2a): `FinalReport` carries no
 * `retrieved_sources` and `RagContext` carries no sources, so the real tab
 * needs `snapshot` for `retrieved_sources`/`trace` alongside `report` for
 * `affected_files`/`rag_context`.
 */

import type { FinalReport, RunSnapshot } from "../../api/types";

export function EvidenceTab(_props: { report: FinalReport; snapshot: RunSnapshot }) {
  return null;
}
