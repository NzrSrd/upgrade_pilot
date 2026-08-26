import { describe, expect, it } from "vitest";

import { describeEvidenceRef, evidenceRefKey } from "./evidence";

describe("describeEvidenceRef", () => {
  it("describes a repo ref as file:line", () => {
    expect(describeEvidenceRef({ kind: "repo", file: "src/models.py", line: 42 })).toBe(
      "src/models.py:42",
    );
  });

  it("describes a doc ref with its similarity when relevance is known", () => {
    expect(
      describeEvidenceRef({ kind: "doc", source_id: "s-2", chunk_id: "s-2#1", relevance: 0.91 }),
    ).toBe("s-2 — similarity 0.91");
  });

  it("falls back to the bare source id when relevance is absent", () => {
    // `relevance` is optional and nullable -- loose (`!= null`), not strict,
    // so a missing value reads as "no similarity known" rather than
    // "similarity undefined" (ruling N1).
    expect(
      describeEvidenceRef({ kind: "doc", source_id: "s-2", chunk_id: "s-2#1", relevance: null }),
    ).toBe("s-2");
  });

  it("describes a constraint ref as field = value", () => {
    expect(describeEvidenceRef({ kind: "constraint", field: "deadline", value: "2026-09-01" })).toBe(
      "deadline = 2026-09-01",
    );
  });
});

describe("evidenceRefKey", () => {
  it("keys a repo ref by file and line, not by its rendered text", () => {
    const key = evidenceRefKey({ kind: "repo", file: "src/models.py", line: 42 });

    expect(key).toBe("repo:src/models.py:42");
  });

  it("gives two refs that render identically two different keys", () => {
    // Two doc chunks at the same similarity render the same describeEvidenceRef
    // string; keying by that string would collide.
    const a = evidenceRefKey({ kind: "doc", source_id: "s-1", chunk_id: "s-1#0", relevance: 0.9 });
    const b = evidenceRefKey({ kind: "doc", source_id: "s-2", chunk_id: "s-2#0", relevance: 0.9 });

    expect(a).not.toBe(b);
  });

  it("keys a constraint ref by field and value", () => {
    expect(evidenceRefKey({ kind: "constraint", field: "deadline", value: "2026-09-01" })).toBe(
      "constraint:deadline:2026-09-01",
    );
  });
});
