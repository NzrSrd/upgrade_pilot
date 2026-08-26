import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { aDetectedVersion, aReport, aRepoAnalysis, aRiskAnalysis } from "../../test/fixtures";
import { OverviewTab } from "./OverviewTab";

describe("OverviewTab", () => {
  it("shows the verdict and the executive summary", () => {
    render(<OverviewTab report={aReport()} />);

    expect(screen.getByText(/four breaking changes reach code/i)).toBeInTheDocument();
    // Fix-round-1 finding 4 adds an "Aggregate risk" field beside "Overall
    // risk"; the default fixture has both at "high", so two badges now
    // legitimately render that word rather than one.
    expect(screen.getAllByText("high")).toHaveLength(2);
  });

  it("never shows a confidence figure without its ceilings", () => {
    // A confidence number alone is the least useful honest figure in the
    // product. Spec 8.1's ceilings each carry a reason a user can act on.
    render(
      <OverviewTab
        report={aReport({
          risk_analysis: aRiskAnalysis({
            confidence: 0.3,
            confidence_ceilings: [
              { reason: "No supporting evidence was retrieved.", ceiling: 0.3 },
            ],
          }),
        })}
      />,
    );

    expect(screen.getByText("30%")).toBeInTheDocument();
    expect(screen.getByText(/no supporting evidence was retrieved/i)).toBeInTheDocument();
    expect(screen.getByText(/capped at 30%/i)).toBeInTheDocument();
  });

  it("says a clamped verdict was clamped", () => {
    render(
      <OverviewTab
        report={aReport({
          risk_analysis: aRiskAnalysis({
            aggregate_risk: "low",
            overall_risk: "high",
            clamp_floor: "high",
          }),
        })}
      />,
    );

    expect(screen.getByText(/raised from low/i)).toBeInTheDocument();
  });

  it("does not fabricate a raise when the floor is below the aggregate", () => {
    // Critical fix-round-1 finding: `overall_risk` is exactly
    // `max(aggregate_risk, clamp_floor)` (backend/src/upgradepilot/models/risk.py:107),
    // so a floor BELOW the aggregate raises nothing even though it is
    // present and differs from the aggregate. The old condition
    // (`clampFloor !== null && clampFloor !== aggregate_risk`) rendered
    // "Raised from high to high" here. The floor is still disclosed as a
    // value -- disclosure and the raise claim are separate conditions.
    render(
      <OverviewTab
        report={aReport({
          risk_analysis: aRiskAnalysis({
            aggregate_risk: "high",
            overall_risk: "high",
            clamp_floor: "low",
          }),
        })}
      />,
    );

    expect(screen.queryByText(/raised from/i)).not.toBeInTheDocument();
    expect(screen.getByText(/clamp floor/i)).toBeInTheDocument();
  });

  it("does not mention a clamp when there was none", () => {
    render(<OverviewTab report={aReport()} />);

    expect(screen.queryByText(/raised from/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/clamp floor/i)).not.toBeInTheDocument();
  });

  it("shows a version discrepancy as both values, side by side", () => {
    render(<OverviewTab report={aReport({ version_discrepancy: ["1.9.0", "1.10.13"] })} />);

    expect(screen.getByText(/you stated/i)).toBeInTheDocument();
    expect(screen.getByText("1.9.0")).toBeInTheDocument();
    expect(screen.getByText(/manifests declare/i)).toBeInTheDocument();
    expect(screen.getByText("1.10.13")).toBeInTheDocument();
  });

  it("shows no complexity score, grade, or duration", () => {
    // READINESS 2.1-2.3: no field backs any of them, and the factor table
    // answers what they were gesturing at.
    render(<OverviewTab report={aReport()} />);

    expect(screen.queryByText(/complexity/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/grade/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/duration/i)).not.toBeInTheDocument();
  });

  it("counts what it can count and names what it counted", () => {
    render(
      <OverviewTab
        report={aReport({
          affected_files: [
            {
              path: "src/app/models.py",
              // Ruling F1: `symbols` is a required `@computed_field`, kept
              // consistent with `usage_sites` below rather than omitted.
              symbols: ["BaseModel"],
              usage_sites: [
                { file: "src/app/models.py", line: 12, column: 0, symbol: "BaseModel", kind: "import", confidence: "high", snippet: null },
              ],
              is_test: false,
              commit_count: null,
              last_modified: null,
            },
          ],
        })}
      />,
    );

    expect(screen.getByText(/affected files/i)).toBeInTheDocument();
    expect(screen.getByText(/usage sites/i)).toBeInTheDocument();
  });

  it("flags a transitive-only pin as one the user does not control", () => {
    // Fix-round-1 finding 6. DESIGN.md's framing is actionability, not
    // confidence -- worded so it is never mistaken for the unrelated
    // `TRANSITIVE_ONLY` confidence ceiling that can also appear.
    render(
      <OverviewTab
        report={aReport({
          repo_analysis: aRepoAnalysis({
            detected_version: aDetectedVersion({ role: "transitive_only" }),
          }),
        })}
      />,
    );

    expect(screen.getByText(/do not control this pin/i)).toBeInTheDocument();
  });

  it("says nothing about control for a directly-declared pin", () => {
    render(
      <OverviewTab
        report={aReport({
          repo_analysis: aRepoAnalysis({ detected_version: aDetectedVersion({ role: "direct" }) }),
        })}
      />,
    );

    expect(screen.queryByText(/do not control this pin/i)).not.toBeInTheDocument();
  });
});
