import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { anApiError, aReport, aSnapshot } from "../../test/fixtures";
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

  it("moves the selection and focus with arrow keys, not just click", async () => {
    // Fix-round-1 finding 3: native buttons make the tabs minimally
    // operable (Tab + Enter/Space) but that is not the ARIA tabs pattern —
    // arrow keys must move the selection, and focus must follow it (roving
    // tabindex), which a handler that merely exists without observably
    // moving the selection would not prove.
    const user = userEvent.setup();
    render(<ReportView snapshot={aSnapshot({ status: "completed", final_report: aReport() })} />);

    screen.getByRole("tab", { name: "Overview" }).focus();

    await user.keyboard("{ArrowRight}");
    const risk = screen.getByRole("tab", { name: "Risk Factors" });
    expect(risk).toHaveAttribute("aria-selected", "true");
    expect(risk).toHaveFocus();

    await user.keyboard("{End}");
    const code = screen.getByRole("tab", { name: "Code" });
    expect(code).toHaveAttribute("aria-selected", "true");
    expect(code).toHaveFocus();

    await user.keyboard("{ArrowRight}");
    const overview = screen.getByRole("tab", { name: "Overview" });
    expect(overview).toHaveAttribute("aria-selected", "true"); // wraps past the last tab

    await user.keyboard("{ArrowLeft}");
    expect(screen.getByRole("tab", { name: "Code" })).toHaveAttribute("aria-selected", "true"); // wraps the other way

    await user.keyboard("{Home}");
    expect(screen.getByRole("tab", { name: "Overview" })).toHaveAttribute("aria-selected", "true");
  });

  it("gives the selected panel real tabpanel semantics, wired to its tab", () => {
    render(<ReportView snapshot={aSnapshot({ status: "completed", final_report: aReport() })} />);

    const tab = screen.getByRole("tab", { name: "Overview" });
    const panel = screen.getByRole("tabpanel");

    expect(tab).toHaveAttribute("aria-controls", panel.id);
    expect(panel).toHaveAttribute("aria-labelledby", tab.id);
  });
  it("shows the errors a run recorded, by the step that recorded them", () => {
    // The defect this exists for: `snapshot.errors` was read by `ErrorView`
    // alone, and `ErrorView` renders for `failed` and `orphaned` only. A run
    // that finished *with warnings* therefore had no route to its own error --
    // so a repository path that did not exist produced a report of zeroes and
    // no way to find out why. `AppError.message` is the user-facing half
    // (CLAUDE.md rule 27), and it is the most useful sentence in the run.
    render(
      <ReportView
        snapshot={aSnapshot({
          status: "completed_with_warnings",
          final_report: aReport({ completed_with_warnings: true }),
          errors: [anApiError()],
        })}
      />,
    );

    const alerts = screen.getAllByRole("alert");
    expect(alerts.map((alert) => alert.textContent).join(" ")).toContain(
      "That repository path does not exist.",
    );
    // Named by the step a user can see in the timeline, not by the node id.
    expect(alerts.map((alert) => alert.textContent).join(" ")).toContain("Repository Analysis");
  });

  it("distinguishes two runs that failed for different reasons", () => {
    // Both of these rendered as the same blank report. The dependency case is
    // not a bad path and the remedy is different, so the report has to say
    // which one happened.
    render(
      <ReportView
        snapshot={aSnapshot({
          status: "completed_with_warnings",
          final_report: aReport({ completed_with_warnings: true }),
          errors: [
            anApiError({
              code: "dependency_not_found",
              message:
                "'react' is not declared in any dependency manifest in this repository, so there is no current version to upgrade from.",
            }),
          ],
        })}
      />,
    );

    expect(screen.getAllByRole("alert").map((alert) => alert.textContent).join(" ")).toContain(
      "is not declared in any dependency manifest",
    );
  });

  it("banners nothing when a run recorded no error", () => {
    render(
      <ReportView snapshot={aSnapshot({ status: "completed", final_report: aReport() })} />,
    );

    expect(screen.queryByRole("alert")).toBeNull();
  });
  it("offers a corrected run when an error names something the user typed", () => {
    // `local_path_forbidden` is in `FIELD_FOR_CODE`, the map the form already
    // uses to decide which input an error belongs beside. That map is the
    // definition of "about something the user typed", so it decides this too
    // rather than a second list free to disagree with it.
    render(
      <ReportView
        snapshot={aSnapshot({
          status: "completed_with_warnings",
          final_report: aReport({ completed_with_warnings: true }),
          errors: [anApiError()],
        })}
        onRetry={() => {}}
      />,
    );

    expect(screen.getByRole("button", { name: /corrected run/i })).toBeInTheDocument();
  });

  it("offers nothing to correct when the error was not about an input", () => {
    // A knowledge base that was unreachable is not a typo. A prefilled form
    // would tell the user to fix something they got right.
    render(
      <ReportView
        snapshot={aSnapshot({
          status: "completed_with_warnings",
          final_report: aReport({ completed_with_warnings: true }),
          errors: [
            anApiError({
              code: "kb_unavailable",
              message: "The knowledge base could not be reached.",
              node: "agentic_rag",
            }),
          ],
        })}
        onRetry={() => {}}
      />,
    );

    expect(screen.queryByRole("button", { name: /corrected run/i })).toBeNull();
  });

  it("offers no corrected run on a clean report", () => {
    render(
      <ReportView
        snapshot={aSnapshot({ status: "completed", final_report: aReport() })}
        onRetry={() => {}}
      />,
    );

    expect(screen.queryByRole("button", { name: /corrected run/i })).toBeNull();
  });

  it("hands the report's own inputs back on click", async () => {
    const user = userEvent.setup();
    const onRetry = vi.fn();
    const report = aReport({
      completed_with_warnings: true,
      repo_ref: { kind: "local", path: "/User/Code/payments-service" },
    });
    render(
      <ReportView
        snapshot={aSnapshot({
          status: "completed_with_warnings",
          final_report: report,
          errors: [anApiError()],
        })}
        onRetry={onRetry}
      />,
    );

    await user.click(screen.getByRole("button", { name: /corrected run/i }));

    expect(onRetry).toHaveBeenCalledWith(report);
  });
});
