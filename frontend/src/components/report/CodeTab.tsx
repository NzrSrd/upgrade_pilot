/**
 * Stub -- replaced in Task 13, which builds the real Code tab (each
 * `AffectedFile.path` with its cited `UsageSite`s -- line, column, symbol,
 * kind, confidence and snippet). Exists only so `ReportView`'s five-tab
 * shell has something to mount in this task.
 *
 * Existing cited code, never a generated patch: `MigrationStep` carries no
 * patch field and `validate_plan` has no check that one parses or applies.
 */

import type { FinalReport } from "../../api/types";

export function CodeTab(_props: { report: FinalReport }) {
  return null;
}
