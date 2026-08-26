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

  it("does not render usage-site confidence on the severity colour scale", () => {
    // Fix round 1, finding 4. `Confidence` (`low|medium|high`) is
    // structurally identical to `RiskLevel`, so `LevelBadge` typechecked --
    // and mapped a HIGH-confidence site, the most trustworthy evidence in
    // the report, to `risk-high`, the token DESIGN.md reserves for blocking
    // issues. Confidence is a certainty axis, not a severity finding, so no
    // element here may carry a `risk-*` class.
    const { container } = render(<CodeTab report={aReport({ affected_files: [file] })} />);

    expect(screen.getByText("high")).toBeInTheDocument();
    expect(screen.getByText("medium")).toBeInTheDocument();
    expect(container.querySelectorAll('[class*="risk-"]')).toHaveLength(0);
  });

  it("says plainly when commit history is unknown, distinct from a real zero count", () => {
    // Fix round 1, finding 2. `models/repo.py:148-162`: `None` means git
    // history was not available at all -- churn is UNKNOWN -- while `0`
    // means history WAS read and this file was not touched. Collapsing the
    // former into rendering nothing "would let 'we did not look' print as
    // 'this file is stable'".
    render(<CodeTab report={aReport({ affected_files: [{ ...file, commit_count: null }] })} />);

    expect(screen.getByText(/commit history unknown/i)).toBeInTheDocument();
    expect(screen.queryByText(/^0 /)).not.toBeInTheDocument();
  });

  it("shows a real zero-commit count as a zero, not as unknown", () => {
    render(<CodeTab report={aReport({ affected_files: [{ ...file, commit_count: 0 }] })} />);

    expect(screen.getByText(/0 commits/i)).toBeInTheDocument();
    expect(screen.queryByText(/unknown/i)).not.toBeInTheDocument();
  });

  it("names the commit count's scope instead of reading as a total", () => {
    // Fix round 1, finding 3. The docstring's first line: "Commits touching
    // this file within the history window, or None." The window is bounded
    // (`UP_CLONE_DEPTH`), which is not in the API payload -- so the scope is
    // named without inventing the bound's value.
    render(<CodeTab report={aReport({ affected_files: [{ ...file, commit_count: 12 }] })} />);

    expect(screen.getByText(/12 commits? in the scanned history/i)).toBeInTheDocument();
  });

  it("does not claim an analyzed commit when commit_sha is null", () => {
    // Minor fix (honesty ones). `FinalReport.commit_sha` is nullable, and
    // `null` is the ordinary case for a workspace with no `.git`
    // (`OverviewTab.tsx`'s "Commit" row renders "—" for the same field).
    // There is no analyzed commit to name in that case.
    render(<CodeTab report={aReport({ affected_files: [file], commit_sha: null })} />);

    expect(screen.queryByText(/read from the analyzed commit/i)).not.toBeInTheDocument();
    expect(screen.getByText(/analyzed workspace/i)).toBeInTheDocument();
  });

  it("names the analyzed commit when one exists", () => {
    render(
      <CodeTab
        report={aReport({ affected_files: [file], commit_sha: "a".repeat(40) })}
      />,
    );

    expect(screen.getByText(/read from the analyzed commit/i)).toBeInTheDocument();
  });
});
