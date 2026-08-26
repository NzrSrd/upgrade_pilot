import { describe, expect, it } from "vitest";

import { describeEvidenceRef, evidenceRefKey, usageSiteKey } from "./evidence";

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

describe("usageSiteKey", () => {
  const site = {
    file: "src/payments/ledger.py",
    line: 5,
    column: 0,
    symbol: "BaseModel",
    kind: "import" as const,
    confidence: "low" as const,
    snippet: "from pydantic import BaseModel, root_validator",
  };

  it("distinguishes two sites that share a position", () => {
    // `_emit_import_sites` emits one site per alias entry, all carrying the
    // import statement's own line and column -- so `from pydantic import
    // BaseModel, root_validator` produces two sites at an identical 5:0.
    // Position alone can never identify an import site, which is why keying
    // on it collided in `EvidenceTab`.
    expect(usageSiteKey(site)).not.toBe(usageSiteKey({ ...site, symbol: "root_validator" }));
  });

  it("distinguishes the same symbol recorded under two kinds", () => {
    expect(usageSiteKey(site)).not.toBe(usageSiteKey({ ...site, kind: "model_definition" }));
  });

  it("is stable for the same site, and independent of what it quotes", () => {
    // `confidence` and `snippet` are not identity: the snippet is a quote of
    // the line the other fields already point at.
    expect(usageSiteKey(site)).toBe(usageSiteKey({ ...site, snippet: null, confidence: "high" }));
  });

  it("distinguishes the same symbol in two files and on two lines", () => {
    expect(usageSiteKey(site)).not.toBe(usageSiteKey({ ...site, file: "src/payments/models.py" }));
    expect(usageSiteKey(site)).not.toBe(usageSiteKey({ ...site, line: 6 }));
    expect(usageSiteKey(site)).not.toBe(usageSiteKey({ ...site, column: 4 }));
  });
});
