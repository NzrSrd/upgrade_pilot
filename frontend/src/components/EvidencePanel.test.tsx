import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { aBreakingChange, aSourceRef } from "../test/fixtures";
import { EvidencePanel, selectedSourceIds } from "./EvidencePanel";

describe("selectedSourceIds", () => {
  it("returns the source_ids of the breaking changes it is given", () => {
    const changes = [
      aBreakingChange({ id: "bc-1", source: aSourceRef({ source_id: "src-a" }) }),
      aBreakingChange({ id: "bc-2", source: aSourceRef({ source_id: "src-b" }) }),
    ];

    expect(selectedSourceIds(changes)).toEqual(new Set(["src-a", "src-b"]));
  });

  it("returns an empty set for no breaking changes", () => {
    expect(selectedSourceIds([])).toEqual(new Set());
  });

  it("collapses two breaking changes that cite the same source into one id", () => {
    const changes = [
      aBreakingChange({ id: "bc-1", source: aSourceRef({ source_id: "src-a" }) }),
      aBreakingChange({ id: "bc-2", source: aSourceRef({ source_id: "src-a" }) }),
    ];

    const result = selectedSourceIds(changes);
    expect(result.size).toBe(1);
    expect(result).toEqual(new Set(["src-a"]));
  });
});

describe("EvidencePanel", () => {
  it("labels a selected source as selected and an unselected one as retrieved, not used", () => {
    // This is the assertion whose absence let a defect through: previously
    // `selectedSourceIds` could never match a real source id, so every
    // retrieved source rendered "retrieved, not used" regardless of truth.
    render(
      <EvidencePanel
        sources={[
          aSourceRef({ source_id: "used", chunk_id: "chunk-used", title: "Used doc" }),
          aSourceRef({ source_id: "unused", chunk_id: "chunk-unused", title: "Unused doc" }),
        ]}
        selectedIds={new Set(["used"])}
      />,
    );

    expect(screen.getByText("selected by the agent")).toBeInTheDocument();
    expect(screen.getByText("retrieved, not used")).toBeInTheDocument();
  });
});
