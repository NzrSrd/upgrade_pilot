import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { TopBar } from "./TopBar";

const summary = { threadId: "t-1", dependency: "pydantic", from: "1.10.13", to: "2.9.2" };

describe("TopBar", () => {
  it("announces the status in a live region", () => {
    // Spec 10: the transition into Human Review must be announced, not merely
    // rendered. `polite` rather than `assertive` so it does not interrupt a
    // user mid-sentence.
    const { container } = render(
      <TopBar status="awaiting_human" reconnecting={false} summary={summary} onOpenTrace={() => {}} />,
    );

    const live = container.querySelector("[aria-live]");
    expect(live).toHaveAttribute("aria-live", "polite");
    expect(live).toHaveTextContent(/waiting for your decision/i);
  });

  it("words each status for a person rather than echoing the enum", () => {
    const { rerender } = render(
      <TopBar status="idle" reconnecting={false} summary={null} onOpenTrace={() => {}} />,
    );
    expect(screen.getByText(/no run started/i)).toBeInTheDocument();

    rerender(<TopBar status="queued" reconnecting={false} summary={summary} onOpenTrace={() => {}} />);
    expect(screen.getByText(/queued/i)).toBeInTheDocument();

    rerender(
      <TopBar status="completed_with_warnings" reconnecting={false} summary={summary} onOpenTrace={() => {}} />,
    );
    expect(screen.getByText(/completed with warnings/i)).toBeInTheDocument();

    rerender(<TopBar status="orphaned" reconnecting={false} summary={summary} onOpenTrace={() => {}} />);
    expect(screen.getByText(/interrupted by a restart/i)).toBeInTheDocument();
  });

  it("says the activity is polled, not streamed", () => {
    // ADR-001 A3 defers SSE. A "streaming" label would describe a transport
    // this system does not have.
    render(<TopBar status="running" reconnecting={false} summary={summary} onOpenTrace={() => {}} />);

    expect(screen.getByText(/1s poll/i)).toBeInTheDocument();
    expect(screen.queryByText(/streaming/i)).not.toBeInTheDocument();
  });

  it("shows a reconnecting notice without discarding the last known status", () => {
    render(<TopBar status="running" reconnecting summary={summary} onOpenTrace={() => {}} />);

    expect(screen.getByText(/reconnecting/i)).toBeInTheDocument();
    expect(screen.getByText(/running/i)).toBeInTheDocument();
  });

  it("names the run being worked on", () => {
    render(<TopBar status="running" reconnecting={false} summary={summary} onOpenTrace={() => {}} />);

    expect(screen.getByText("pydantic")).toBeInTheDocument();
    expect(screen.getByText(/1\.10\.13/)).toBeInTheDocument();
    expect(screen.getByText(/2\.9\.2/)).toBeInTheDocument();
  });

  it("offers the agent trace", () => {
    render(<TopBar status="running" reconnecting={false} summary={summary} onOpenTrace={() => {}} />);

    expect(screen.getByRole("button", { name: /agent trace/i })).toBeInTheDocument();
  });

  it("says the run could not be loaded for its own \"unavailable\" state, not queued", () => {
    // Fix round 4: before this state existed, a poll that already refused
    // fell back to `queued` here, so this pill -- the app's `aria-live`
    // region -- announced "Queued" for a run the panel below was, at the
    // same moment, reporting as unreadable.
    render(<TopBar status="unavailable" reconnecting={false} summary={summary} onOpenTrace={() => {}} />);

    expect(screen.getByText(/could not load this run/i)).toBeInTheDocument();
    expect(screen.queryByText(/^queued$/i)).not.toBeInTheDocument();
  });

  it("shows no connection indicator for \"unavailable\": polling has already stopped", () => {
    // `useRunPolling`'s loop does not schedule another tick after an
    // `ApiFailure` -- the same way it does not for any other status that
    // stops polling -- so "Live · 1s poll" or "Reconnecting…" beside this
    // pill would claim an ongoing poll that no longer exists.
    render(<TopBar status="unavailable" reconnecting={false} summary={summary} onOpenTrace={() => {}} />);

    expect(screen.queryByText(/1s poll/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/reconnecting/i)).not.toBeInTheDocument();
  });

  it("shows no connection indicator for \"unavailable\" even mid-reconnect from a prior status", () => {
    // Guards the same claim across a transition, not just a fresh render:
    // `reconnecting` is a leftover flag from the *previous* status, and
    // `unavailable` must not inherit its indicator.
    const { rerender } = render(
      <TopBar status="running" reconnecting summary={summary} onOpenTrace={() => {}} />,
    );
    expect(screen.getByText(/reconnecting/i)).toBeInTheDocument();

    rerender(<TopBar status="unavailable" reconnecting summary={summary} onOpenTrace={() => {}} />);
    expect(screen.queryByText(/reconnecting/i)).not.toBeInTheDocument();
  });
});
