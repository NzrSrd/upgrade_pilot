/**
 * A finished run's inputs, in the shape the configuration form holds them.
 *
 * Correcting one field should not cost the other eight. A mistyped local path
 * is the most correctable error the product produces -- `analyze_repo` refuses
 * it before a single model call -- and the only way back was "New migration
 * run" and retyping the dependency, both versions and four constraints from
 * memory.
 *
 * A pure function of the report, in `derive/` for the same reason the rest of
 * this directory is: a server shape mapped to what a component renders,
 * testable without mounting anything. The form owns its state; this only says
 * what that state starts as.
 *
 * `final_report` is the only place the three inputs reach a client
 * (`api/schemas.py`), which is why this takes a report rather than a snapshot
 * -- and why `failed` and `orphaned` runs cannot offer this today: they have
 * no report, and their inputs stay in the checkpoint.
 */

import type { FinalReport, RiskLevel } from "../api/types";

export type FormPrefill = {
  source: "remote" | "local";
  url: string;
  path: string;
  name: string;
  from: string;
  to: string;
  zeroDowntime: boolean;
  minimizeEffort: boolean;
  deadline: string;
  riskTolerance: RiskLevel;
};

export function prefillFrom(report: FinalReport): FormPrefill {
  const ref = report.repo_ref;
  // Narrowed on the property, not on `kind`: every Pydantic field has a
  // default, so the generated type makes the discriminator optional and it
  // does not narrow the union. `EvidenceRefList` reaches for `"file" in ref`
  // for the same reason.
  const local = "path" in ref;
  return {
    source: local ? "local" : "remote",
    // The field the run did not use stays empty rather than echoing the other
    // one: a URL sitting in the path input is a value the user never typed.
    url: local ? "" : ref.url,
    path: local ? ref.path : "",
    name: report.dependency.name,
    from: report.dependency.current_version,
    to: report.dependency.target_version,
    zeroDowntime: report.constraints.zero_downtime ?? false,
    minimizeEffort: report.constraints.minimize_effort ?? false,
    // `deadline` is nullable, and the input it feeds is a `type="date"` whose
    // empty value is the empty string -- `null` would render as "null".
    deadline: report.constraints.deadline ?? "",
    riskTolerance: report.constraints.risk_tolerance ?? "medium",
  };
}
