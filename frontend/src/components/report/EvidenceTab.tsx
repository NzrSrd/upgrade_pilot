/**
 * Repository evidence first, documents second.
 *
 * The ordering is a claim about weight, not layout: a file and a line is a
 * fact about this repository, while a retrieved document is context that may
 * or may not apply to it. `DESIGN.md`: evidence is supporting information, not
 * a replacement for repository analysis.
 *
 * Two props, not one (ruling P2a): `FinalReport` carries no
 * `retrieved_sources` and `RagContext` carries no sources, so this tab needs
 * `snapshot` for `retrieved_sources` (and the `breaking_changes` that feed
 * `selectedSourceIds` -- see `EvidencePanel`'s own docstring for why
 * "selected" is read from there rather than from a trace event) alongside
 * `report` for `affected_files`/`rag_context`.
 */

import type { FinalReport, RunSnapshot } from "../../api/types";
import { EvidencePanel, selectedSourceIds } from "../EvidencePanel";
import { EmptyState, Field, Mono, Panel } from "../ui";

export function EvidenceTab({
  report,
  snapshot,
}: {
  report: FinalReport;
  snapshot: RunSnapshot;
}) {
  // `affected_files` and `rag_context` are optional in the generated type
  // only because every Pydantic field carries a default -- `finalize`
  // always populates the first (as `[]` when there is nothing) and sets the
  // second to `None` rather than omitting it. `retrieved_sources` and
  // `breaking_changes` on `RunSnapshot` carry the same shape of default.
  // Resolved once here (ruling T10b) so every comparison below is a plain,
  // correct strict check rather than one that treats an absent field
  // differently from an explicit null/empty one (ruling N1).
  const affectedFiles = report.affected_files ?? [];
  const ragContext = report.rag_context ?? null;
  const retrievedSources = snapshot.retrieved_sources ?? [];
  const breakingChanges = snapshot.breaking_changes ?? [];
  const selected = selectedSourceIds(breakingChanges);

  return (
    <div className="space-y-4">
      <Panel title="Repository evidence">
        {affectedFiles.length === 0 ? (
          <EmptyState>No usage sites were found.</EmptyState>
        ) : (
          <ul className="space-y-1">
            {affectedFiles.flatMap((file) =>
              file.usage_sites.map((site) => (
                <li key={`${site.file}:${site.line}:${site.column}`} className="text-sm">
                  <Mono>
                    {site.file}:{site.line}
                  </Mono>{" "}
                  <span className="text-ink-muted">
                    {site.symbol} · {site.kind.replace(/_/g, " ")}
                  </span>
                </li>
              )),
            )}
          </ul>
        )}
      </Panel>

      <Panel title="Retrieved documents">
        <EvidencePanel sources={retrievedSources} selectedIds={selected} />
      </Panel>

      {ragContext !== null && (
        <Panel title="Retrieval">
          <dl className="grid grid-cols-2 gap-4 sm:grid-cols-5">
            <Field label="Rounds" value={String(ragContext.iterations)} />
            {/* Minor fix (honesty ones): `sources_considered` is
                `len(candidates)` (`graph/rag/nodes.py`), a count of chunks --
                the backend's own prose calls them "chunk(s)"
                (`graph/nodes/evidence.py`). Two panels below "Retrieved
                documents", a bare number reads as a document count. */}
            <Field
              label="Considered"
              value={`${ragContext.sources_considered} chunk${ragContext.sources_considered === 1 ? "" : "s"}`}
            />
            <Field
              label="Retrieval stopped because"
              value={ragContext.stop_reason.replace(/_/g, " ")}
            />
            <Field label="Evidence found" value={ragContext.evidence_available ? "yes" : "no"} />
            {/* Ruling F6: `sufficient` is a required field the brief never
                renders, and it is a different claim from `evidence_available`
                -- whether anything was found is not the same as whether it
                was enough to answer on. F6's addendum warns this can sit
                beside a `stop_reason` of "sufficient" or "not_necessary" and
                read as restating or contradicting it if mislabelled, so this
                is labelled on its own terms ("enough ... to answer on"),
                distinct from both "stopped because" (why the loop halted)
                and "evidence found" (whether anything came back), and states
                the word plainly rather than relying on colour. No narrative
                about *why* it fell short is invented here -- only what the
                field means. */}
            <Field
              label="Enough evidence to answer on"
              value={ragContext.sufficient ? "yes" : "no"}
            />
          </dl>
          {(ragContext.unknowns ?? []).length > 0 && (
            <div className="mt-3">
              <p className="text-[11px] tracking-wide text-ink-faint uppercase">
                What retrieval could not establish
              </p>
              {/* Minor fix (honesty ones): `RagContext.unknowns`
                  (`models/knowledge.py`) is "symbols the repository uses
                  that no retrieved document documents", every confidence
                  tier, "not just high confidence" -- it carries no severity,
                  so `text-risk-medium` graded something the backend did not.
                  Neutral chrome. These are symbol names, not prose, so
                  `Mono` (`DESIGN.md` requires monospace for symbols) rather
                  than a bullet of sentence text. */}
              <ul className="mt-1 space-y-0.5">
                {(ragContext.unknowns ?? []).map((unknown) => (
                  <li key={unknown} className="text-xs text-ink-muted">
                    — <Mono>{unknown}</Mono>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </Panel>
      )}
    </div>
  );
}
