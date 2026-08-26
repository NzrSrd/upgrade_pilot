import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { aFactor, aRiskAnalysis } from "../../test/fixtures";
import { RiskFactorsTab } from "./RiskFactorsTab";

describe("RiskFactorsTab", () => {
  it("lists each factor with its level, weight and detail", () => {
    render(<RiskFactorsTab analysis={aRiskAnalysis()} />);

    expect(screen.getByText("Breaking change exposure")).toBeInTheDocument();
    expect(screen.getByText("high")).toBeInTheDocument();
    expect(screen.getByText(/0\.25/)).toBeInTheDocument();
    expect(screen.getByText(/four breaking changes touch symbols/i)).toBeInTheDocument();
  });

  it("discloses the evidence a factor cites", async () => {
    // Every factor carries `evidence` with min_length=1, and per-factor
    // disclosure is what makes the verdict inspectable rather than asserted.
    const user = userEvent.setup();
    render(<RiskFactorsTab analysis={aRiskAnalysis()} />);

    await user.click(screen.getByRole("button", { name: /evidence/i }));

    expect(screen.getByText(/src\/app\/models\.py/)).toBeInTheDocument();
    expect(screen.getByText(/:12/)).toBeInTheDocument();
  });

  it("shows every factor, not only the alarming ones", () => {
    // Ruling F5: `id` and `category` are the same string on every real
    // factor (`factors.py` builds `id=threshold.category.value`), so a
    // fixture override must keep them equal rather than inventing a
    // contradictory identity the backend can never produce.
    render(
      <RiskFactorsTab
        analysis={aRiskAnalysis({
          factors: [
            aFactor({ id: "blast_radius", category: "blast_radius", name: "Blast radius", level: "low" }),
            aFactor({
              id: "test_coverage_of_affected",
              category: "test_coverage_of_affected",
              name: "Test coverage of affected",
              level: "medium",
            }),
          ],
        })}
      />,
    );

    expect(screen.getByText("Blast radius")).toBeInTheDocument();
    expect(screen.getByText("Test coverage of affected")).toBeInTheDocument();
  });

  it("says the levels came from a threshold table, not the model", () => {
    // Rule 19. The claim a reader most needs about this table is who computed
    // it.
    render(<RiskFactorsTab analysis={aRiskAnalysis()} />);

    expect(screen.getByText(/threshold table/i)).toBeInTheDocument();
  });

  it("says so when there is no assessment", () => {
    render(<RiskFactorsTab analysis={null} />);

    expect(screen.getByText(/no risk assessment/i)).toBeInTheDocument();
  });
});
