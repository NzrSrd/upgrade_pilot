import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { aReport, aSnapshot } from "../../test/fixtures";
import { ReportView } from "./ReportView";

describe("ReportView", () => {
  it("offers exactly the five tabs the data supports", () => {
    render(<ReportView snapshot={aSnapshot({ status: "completed", final_report: aReport() })} />);

    expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual([
      "Overview",
      "Risk Factors",
      "Evidence",
      "Plan",
      "Code",
    ]);
  });

  it("offers no PR draft tab", () => {
    // Writing to GitHub is sub-project 2. A PR body behind a button that
    // cannot create anything offers a capability the product does not have.
    render(<ReportView snapshot={aSnapshot({ status: "completed", final_report: aReport() })} />);

    expect(screen.queryByRole("tab", { name: /pull request|pr draft/i })).not.toBeInTheDocument();
  });

  it("opens on the overview", () => {
    render(<ReportView snapshot={aSnapshot({ status: "completed", final_report: aReport() })} />);

    expect(screen.getByRole("tab", { name: "Overview" })).toHaveAttribute("aria-selected", "true");
  });

  it("switches tabs on click", async () => {
    const user = userEvent.setup();
    render(<ReportView snapshot={aSnapshot({ status: "completed", final_report: aReport() })} />);

    await user.click(screen.getByRole("tab", { name: "Risk Factors" }));

    expect(screen.getByRole("tab", { name: "Risk Factors" })).toHaveAttribute("aria-selected", "true");
  });

  it("banners a run whose validation did not pass", () => {
    // Spec 8.4 ends "never silently passes", so the report never silently
    // omits it either.
    render(
      <ReportView
        snapshot={aSnapshot({
          status: "completed_with_warnings",
          final_report: aReport({ completed_with_warnings: true }),
        })}
      />,
    );

    expect(screen.getByRole("alert")).toHaveTextContent(/validation did not pass/i);
  });

  it("says so rather than rendering an empty report when there is none", () => {
    render(<ReportView snapshot={aSnapshot({ status: "completed", final_report: null })} />);

    expect(screen.getByText(/no report was produced/i)).toBeInTheDocument();
  });
});
