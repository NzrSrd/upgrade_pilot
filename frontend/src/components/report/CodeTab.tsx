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
import { EmptyState, LevelBadge, Mono, Panel } from "../ui";

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
              {/* `commit_count` is optional AND nullable -- loose, not
                  strict: the strict form (`!== null`) leaves an absent value
                  (`undefined`) truthy and prints "undefined commits"
                  (ruling N1). */}
              {file.commit_count != null && <span>{file.commit_count} commits</span>}
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
                  <LevelBadge level={site.confidence}>{site.confidence}</LevelBadge>
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
