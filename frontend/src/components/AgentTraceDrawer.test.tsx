import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { TraceEvent } from "../api/types";
import { AgentTraceDrawer } from "./AgentTraceDrawer";

const event = (overrides: Partial<TraceEvent>): TraceEvent => ({
  event_id: "e-1",
  kind: "node_started",
  node: "assess_risk",
  at: "2026-08-25T12:00:00Z",
  summary: "assess_risk started",
  detail: null,
  ...overrides,
});

describe("AgentTraceDrawer", () => {
  it("renders nothing when closed", () => {
    render(<AgentTraceDrawer trace={[event({})]} open={false} onClose={() => {}} />);

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("lists observable events with their node and kind", () => {
    render(
      <AgentTraceDrawer
        trace={[event({ kind: "query_issued", summary: "pydantic validator migration" })]}
        open
        onClose={() => {}}
      />,
    );

    expect(screen.getByRole("dialog", { name: /agent trace/i })).toBeInTheDocument();
    expect(screen.getByText("query issued")).toBeInTheDocument();
    expect(screen.getByText(/pydantic validator migration/)).toBeInTheDocument();
  });

  it("says what it does not show", () => {
    // CLAUDE.md rule 26. Saying so is the difference between a drawer that
    // omits prompts and a drawer a user assumes is complete.
    render(<AgentTraceDrawer trace={[event({})]} open onClose={() => {}} />);

    expect(screen.getByText(/observable events only/i)).toBeInTheDocument();
    expect(screen.getByText(/no prompts/i)).toBeInTheDocument();
  });

  it("closes on the button and on Escape", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(<AgentTraceDrawer trace={[event({})]} open onClose={onClose} />);

    await user.click(screen.getByRole("button", { name: /close/i }));
    expect(onClose).toHaveBeenCalledTimes(1);

    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it("reports an empty trace as empty rather than as nothing", () => {
    render(<AgentTraceDrawer trace={[]} open onClose={() => {}} />);

    expect(screen.getByText(/no events recorded/i)).toBeInTheDocument();
  });
});
