import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { aBreakingChange, aReport, aSnapshot, aSourceRef } from "../../test/fixtures";
import { EvidenceTab } from "./EvidenceTab";

const file = {
  path: "src/app/models.py",
  // Ruling F1: `symbols` is a required `@computed_field`, kept consistent
  // with `usage_sites` below rather than omitted.
  symbols: ["BaseModel"],
  usage_sites: [
    {
      file: "src/app/models.py",
      line: 12,
      column: 0,
      symbol: "BaseModel",
      kind: "import" as const,
      confidence: "high" as const,
      snippet: null,
    },
  ],
  is_test: false,
  commit_count: null,
  last_modified: null,
};

// Every required `RagContext` property (ruling F6: the schema requires
// `evidence_available`, `iterations`, `sources_considered`, `stop_reason`
// AND `sufficient` -- an earlier controller note recording only the first
// of these was incomplete).
const sufficientRag = {
  evidence_available: true,
  iterations: 2,
  sources_considered: 5,
  stop_reason: "sufficient" as const,
  sufficient: true,
};

const insufficientRag = {
  evidence_available: true,
  iterations: 3,
  sources_considered: 4,
  stop_reason: "iteration_limit" as const,
  sufficient: false,
};

describe("EvidenceTab", () => {
  it("renders repository evidence from the report and retrieved documents from the snapshot", () => {
    // Proves the two-prop signature (ruling P2a) actually reads both: a
    // one-prop version could not source `retrieved_sources` at all.
    const source = aSourceRef({ source_id: "src-1", title: "Migrating to Pydantic V2" });
    render(
      <EvidenceTab
        report={aReport({ affected_files: [file], rag_context: sufficientRag })}
        snapshot={aSnapshot({ retrieved_sources: [source] })}
      />,
    );

    expect(screen.getByText(/src\/app\/models\.py:12/)).toBeInTheDocument();
    // The symbol and kind share one text node ("BaseModel · import"), so a
    // substring match, not an exact one.
    expect(screen.getByText(/BaseModel/)).toBeInTheDocument();
    expect(screen.getByText("Migrating to Pydantic V2")).toBeInTheDocument();
  });

  it("distinguishes a selected source from a merely-retrieved one", () => {
    const used = aSourceRef({ source_id: "src-selected", chunk_id: "c-1", title: "Selected doc" });
    const unused = aSourceRef({ source_id: "src-unused", chunk_id: "c-2", title: "Unused doc" });
    render(
      <EvidenceTab
        report={aReport()}
        snapshot={aSnapshot({
          retrieved_sources: [used, unused],
          breaking_changes: [aBreakingChange({ source: used })],
        })}
      />,
    );

    expect(screen.getByText(/selected by the agent/i)).toBeInTheDocument();
    expect(screen.getByText(/retrieved, not used/i)).toBeInTheDocument();
  });

  it("shows an honest empty state when no documents were retrieved", () => {
    render(<EvidenceTab report={aReport()} snapshot={aSnapshot({ retrieved_sources: [] })} />);

    expect(screen.getByText(/no documents retrieved yet/i)).toBeInTheDocument();
  });

  it("renders every required retrieval-stats field, including sufficiency", () => {
    render(<EvidenceTab report={aReport({ rag_context: sufficientRag })} snapshot={aSnapshot()} />);

    expect(screen.getByText("2")).toBeInTheDocument(); // iterations
    expect(screen.getByText("5 chunks")).toBeInTheDocument(); // sources_considered, with its unit (minor fix)
    expect(screen.getByText(/sufficient/i)).toBeInTheDocument(); // stop_reason
    expect(screen.getByText(/evidence found/i)).toBeInTheDocument();
    expect(screen.getByText(/enough evidence to answer on/i)).toBeInTheDocument();
  });

  it("does not grade an unretrieved symbol with a severity colour, and sets it in monospace", () => {
    // Minor fix (honesty ones). `RagContext.unknowns` (`models/knowledge.py`)
    // carries every confidence tier, not just the high-confidence ones the
    // gate blocks on, and no severity of its own -- `text-risk-medium`
    // graded a fact the backend did not. These are symbol names, which
    // `DESIGN.md` requires in monospace.
    const { container } = render(
      <EvidenceTab
        report={aReport({ rag_context: { ...sufficientRag, unknowns: ["User.legacy_validate"] } })}
        snapshot={aSnapshot()}
      />,
    );

    expect(screen.getByText("User.legacy_validate")).toBeInTheDocument();
    expect(screen.getByText("User.legacy_validate").tagName).toBe("SPAN");
    expect(container.querySelectorAll('[class*="risk-"]')).toHaveLength(0);
  });

  it("visibly reports an insufficient retrieval as such, not by colour alone", () => {
    render(
      <EvidenceTab report={aReport({ rag_context: insufficientRag })} snapshot={aSnapshot()} />,
    );

    // The label distinguishes "sufficient" from the "iteration_limit" stop
    // reason (ruling F6 addendum): both render, and the sufficiency answer
    // is spelled out in words -- "no" is present as its own text node next
    // to the label, not left to colour alone.
    expect(screen.getByText(/iteration limit/i)).toBeInTheDocument();
    expect(screen.getByText(/enough evidence to answer on/i)).toBeInTheDocument();
    expect(screen.getByText("no")).toBeInTheDocument();
    // `evidence_available` is true here even though `sufficient` is false --
    // the two are different claims, and only one of them is "no".
    expect(screen.getByText("yes")).toBeInTheDocument();
  });
  it("renders every usage site on an import line, and keys them uniquely", () => {
    // `from pydantic import BaseModel, root_validator` is two usage sites at
    // one position: `_emit_import_sites` emits per alias entry, all carrying
    // the import statement's line and column. Keyed on position alone, React
    // saw duplicate keys -- "children may be duplicated and/or omitted" is
    // its own description of what that permits, and this list is evidence.
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    const importLine = "from pydantic import BaseModel, root_validator";
    const shared = {
      file: "src/payments/ledger.py",
      line: 5,
      column: 0,
      kind: "import" as const,
      confidence: "low" as const,
      snippet: importLine,
    };
    const twoSitesOneLine = {
      ...file,
      path: "src/payments/ledger.py",
      symbols: ["BaseModel", "root_validator"],
      usage_sites: [
        { ...shared, symbol: "BaseModel" },
        { ...shared, symbol: "root_validator" },
      ],
    };

    render(
      <EvidenceTab
        report={aReport({ affected_files: [twoSitesOneLine], rag_context: sufficientRag })}
        snapshot={aSnapshot({ status: "completed" })}
      />,
    );

    expect(screen.getByText(/BaseModel/)).toBeInTheDocument();
    expect(screen.getByText(/root_validator/)).toBeInTheDocument();

    const keyWarnings = consoleError.mock.calls.filter((call) =>
      call.some((arg) => typeof arg === "string" && arg.includes("same key")),
    );
    expect(keyWarnings).toEqual([]);
    consoleError.mockRestore();
  });
});
