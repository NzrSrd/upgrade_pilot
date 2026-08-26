/**
 * Affected files and the cited lines inside them.
 *
 * **This is existing code, not a proposed patch**, and the tab says so where a
 * reader cannot miss it. `MigrationStep` carries no patch field and
 * `validate_plan` has no check that a patch parses or applies, so a diff view
 * here would render LLM-authored code with nothing verifying it — the
 * strongest available form of what rule 1 forbids. Cited usage sites are
 * cheap, fully resolvable, and true.
 */

import type { FinalReport } from "../../api/types";
import { EmptyState, Mono, Panel } from "../ui";

export function CodeTab({ report }: { report: FinalReport }) {
  // `affected_files` is optional in the generated type only because every
  // Pydantic field carries a default -- `finalize` always populates it (as
  // `[]` when there is nothing). Resolved once here (ruling T10b).
  const affectedFiles = report.affected_files ?? [];

  if (affectedFiles.length === 0) {
    return (
      <Panel title="Code">
        <EmptyState>No affected files were found in this repository.</EmptyState>
      </Panel>
    );
  }

  return (
    <div className="space-y-4">
      <p className="text-xs text-ink-faint">
        Existing code at the cited usage sites, read from the analyzed commit. These are not
        proposed changes — no patch is generated, so none is shown.
      </p>

      {affectedFiles.map((file) => (
        <Panel
          key={file.path}
          title={file.path}
          action={
            <span className="flex items-center gap-2 text-[11px] text-ink-faint">
              {/* `is_test` carries a default (`false`); an absent value and
                  an explicit `false` mean the same thing (ruling T10b). */}
              {(file.is_test ?? false) && (
                <span className="rounded border border-edge px-1.5 py-0.5">test file</span>
              )}
              {/* Fix round 1, findings 2 and 3. `commit_count` is optional
                  AND nullable -- loose, not strict (ruling N1) -- but this
                  is not merely a truthiness guard: `models/repo.py:148-162`
                  documents three states that must stay visually distinct.
                  `null` means git history was not available at all --
                  churn is UNKNOWN, and rendering nothing here would let "we
                  did not look" print as "this file is stable", which is the
                  exact failure the docstring names. `0` means history WAS
                  read and the file was not touched -- a real, known signal,
                  never collapsed into the unknown case. And the count is
                  bounded to a history window (`UP_CLONE_DEPTH`, not itself
                  in this payload, so no bound value is invented here) --
                  "in the scanned history" names that scope so the number
                  does not read as a lifetime total. */}
              <span>
                {file.commit_count == null
                  ? "commit history unknown"
                  : `${file.commit_count} commit${file.commit_count === 1 ? "" : "s"} in the scanned history`}
              </span>
              <span>
                {file.usage_sites.length} site{file.usage_sites.length === 1 ? "" : "s"}
              </span>
            </span>
          }
        >
          <ul className="space-y-2">
            {file.usage_sites.map((site) => (
              <li key={`${site.line}:${site.column}:${site.symbol}`}>
                <div className="flex flex-wrap items-baseline gap-2 text-sm">
                  <Mono>
                    {site.line}:{site.column}
                  </Mono>
                  <span className="font-medium">{site.symbol}</span>
                  <span className="text-xs text-ink-muted">{site.kind.replace(/_/g, " ")}</span>
                  {/* Fix round 1, finding 4. `Confidence` (`low|medium|high`)
                      is structurally identical to `RiskLevel`, so
                      `LevelBadge` typechecked here -- and mapped a
                      high-confidence site, the most trustworthy evidence in
                      the report, to `risk-high`, the token DESIGN.md
                      reserves for blocking issues. Confidence is a
                      certainty axis, not a severity finding, so this is
                      neutral ink/edge chrome (matching how `OverviewTab`
                      renders its own confidence figure), never the
                      severity scale. */}
                  <span className="flex items-center gap-1 text-[11px]">
                    <span className="text-ink-faint">confidence</span>
                    <span className="rounded border border-edge px-1.5 py-0.5 font-semibold text-ink-muted uppercase">
                      {site.confidence}
                    </span>
                  </span>
                </div>
                {/* `snippet` is optional AND nullable -- loose, not strict:
                    the strict form (`!== null`) leaves an absent value
                    (`undefined`) truthy and renders an empty `<pre>`
                    (ruling N1). */}
                {site.snippet != null && (
                  <pre className="mt-1 overflow-x-auto rounded bg-surface-sunken p-2 font-mono text-[12px] text-ink-muted">
                    {site.snippet}
                  </pre>
                )}
              </li>
            ))}
          </ul>
        </Panel>
      ))}
    </div>
  );
}
