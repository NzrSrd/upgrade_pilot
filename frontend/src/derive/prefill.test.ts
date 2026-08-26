import { describe, expect, it } from "vitest";

import { aReport } from "../test/fixtures";
import { prefillFrom } from "./prefill";

describe("prefillFrom", () => {
  it("carries a local path back into the local source", () => {
    // The case this exists for: a mistyped local path is the most correctable
    // error there is, and correcting it should not cost the dependency, both
    // versions and four constraints as well.
    const prefill = prefillFrom(
      aReport({
        repo_ref: { kind: "local", path: "/Users/me/Code/payments-service" },
      }),
    );

    expect(prefill.source).toBe("local");
    expect(prefill.path).toBe("/Users/me/Code/payments-service");
    expect(prefill.url).toBe("");
  });

  it("carries a remote url back into the remote source", () => {
    const prefill = prefillFrom(
      aReport({ repo_ref: { kind: "remote", url: "https://example.com/repo.git" } }),
    );

    expect(prefill.source).toBe("remote");
    expect(prefill.url).toBe("https://example.com/repo.git");
    expect(prefill.path).toBe("");
  });

  it("carries the dependency and both versions", () => {
    const prefill = prefillFrom(
      aReport({
        dependency: {
          name: "pydantic",
          canonical_name: "pydantic",
          current_version: "1.10.13",
          target_version: "2.9.2",
          import_root: "pydantic",
        },
      }),
    );

    expect(prefill.name).toBe("pydantic");
    expect(prefill.from).toBe("1.10.13");
    expect(prefill.to).toBe("2.9.2");
  });

  it("carries all four constraints, deadline included", () => {
    // `deadline` is the one a form without it silently weakens:
    // `constraint_pressure` is derived partly from it, so dropping it here
    // would produce a "retry" that measures a different run.
    const prefill = prefillFrom(
      aReport({
        constraints: {
          zero_downtime: true,
          minimize_effort: true,
          deadline: "2026-09-15",
          risk_tolerance: "low",
        },
      }),
    );

    expect(prefill.zeroDowntime).toBe(true);
    expect(prefill.minimizeEffort).toBe(true);
    expect(prefill.deadline).toBe("2026-09-15");
    expect(prefill.riskTolerance).toBe("low");
  });

  it("renders an absent deadline as an empty date field, not as the string null", () => {
    // `deadline` is nullable on `UserConstraints` and the input it feeds is a
    // `type="date"`, whose empty value is "".
    const prefill = prefillFrom(aReport({ constraints: { zero_downtime: false, minimize_effort: false, deadline: null, risk_tolerance: "medium" } }));

    expect(prefill.deadline).toBe("");
  });
});
