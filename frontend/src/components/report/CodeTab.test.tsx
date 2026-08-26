import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { aReport } from "../../test/fixtures";
import { CodeTab } from "./CodeTab";

const file = {
  path: "src/app/models.py",
  // Ruling F1: `symbols` is a required `@computed_field` ("the distinct
  // symbols used in this file, sorted"), computed precisely so it cannot
  // drift from `usage_sites` -- kept consistent with the two symbols below
  // rather than omitted.
  symbols: ["BaseModel", "Optional"],
  usage_sites: [
    { file: "src/app/models.py", line: 12, column: 0, symbol: "BaseModel", kind: "import" as const, confidence: "high" as const, snippet: "from pydantic import BaseModel" },
    { file: "src/app/models.py", line: 31, column: 4, symbol: "Optional", kind: "optional_field" as const, confidence: "medium" as const, snippet: null },
  ],
  is_test: false,
  commit_count: 7,
  last_modified: null,
};

describe("CodeTab", () => {
  it("lists each cited usage site with its line, column, symbol and kind", () => {
    render(<CodeTab report={aReport({ affected_files: [file] })} />);

    expect(screen.getByText("src/app/models.py")).toBeInTheDocument();
    expect(screen.getByText(/12:0/)).toBeInTheDocument();
    expect(screen.getByText("BaseModel")).toBeInTheDocument();
    expect(screen.getByText(/optional field/i)).toBeInTheDocument();
  });

  it("shows the confidence of each site", () => {
    render(<CodeTab report={aReport({ affected_files: [file] })} />);

    expect(screen.getByText("high")).toBeInTheDocument();
    expect(screen.getByText("medium")).toBeInTheDocument();
  });

  it("shows the captured snippet where there is one", () => {
    render(<CodeTab report={aReport({ affected_files: [file] })} />);

    expect(screen.getByText(/from pydantic import BaseModel/)).toBeInTheDocument();
  });

  it("says this is existing code, not a proposed patch", () => {
    // The distinction the whole tab turns on. A reader who thinks these are
    // generated changes is reading unverified output as fact.
    render(<CodeTab report={aReport({ affected_files: [file] })} />);

    expect(screen.getByText(/existing code/i)).toBeInTheDocument();
  });

  it("marks test files, because they weigh differently", () => {
    render(<CodeTab report={aReport({ affected_files: [{ ...file, is_test: true }] })} />);

    expect(screen.getByText(/test file/i)).toBeInTheDocument();
  });

  it("says so when nothing was affected", () => {
    render(<CodeTab report={aReport({ affected_files: [] })} />);

    expect(screen.getByText(/no affected files/i)).toBeInTheDocument();
  });
});
