import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import { aSnapshot } from "../test/fixtures";
import { server } from "../test/server";
import { ErrorView } from "./ErrorView";

const RESUME = "http://localhost/api/agent/resume";

describe("ErrorView", () => {
  it("offers a resume, not a restart, for an orphaned run", () => {
    // The distinction is the whole point. Offering "start again" would discard
    // a checkpoint that survived and bill for the work a second time.
    render(
      <ErrorView
        snapshot={aSnapshot({ status: "orphaned", completed_steps: ["analyze_repo", "inspect_dependency"] })}
        pollError={null}
        onRetry={() => {}}
        onResumed={() => {}}
      />,
    );

    expect(screen.getByRole("button", { name: /resume/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /start again|restart/i })).not.toBeInTheDocument();
  });

  it("says what survived the interruption", () => {
    render(
      <ErrorView
        snapshot={aSnapshot({ status: "orphaned", completed_steps: ["analyze_repo", "inspect_dependency"] })}
        pollError={null}
        onRetry={() => {}}
        onResumed={() => {}}
      />,
    );

    expect(screen.getByText(/2 of 8 steps/i)).toBeInTheDocument();
    expect(screen.getByText(/continues from where it stopped/i)).toBeInTheDocument();
  });

  it("resumes without a decision, because an abandoned run is not waiting for one", async () => {
    // Spec 9.1: asking the client to invent a decision for an orphaned run
    // would be asking for a lie.
    const user = userEvent.setup();
    let body: { thread_id: string; decision: unknown } | null = null;
    server.use(
      http.post(RESUME, async ({ request }) => {
        body = (await request.json()) as typeof body;
        return HttpResponse.json(
          { thread_id: "t-1", status: "running", poll_url: "/api/agent/status/t-1" },
          { status: 202 },
        );
      }),
    );
    render(
      <ErrorView
        snapshot={aSnapshot({ status: "orphaned" })}
        pollError={null}
        onRetry={() => {}}
        onResumed={() => {}}
      />,
    );

    await user.click(screen.getByRole("button", { name: /resume/i }));

    await waitFor(() => expect(body).not.toBeNull());
    expect(body!.decision).toBeNull();
  });

  it("offers a new run for a failed one, and shows the recorded errors", () => {
    const onRetry = vi.fn();
    render(
      <ErrorView
        snapshot={aSnapshot({
          status: "failed",
          errors: [
            { code: "repo_unavailable", message: "The repository could not be cloned.", retryable: true, node: "analyze_repo" },
          ],
        })}
        pollError={null}
        onRetry={onRetry}
        onResumed={() => {}}
      />,
    );

    expect(screen.getByText(/repository could not be cloned/i)).toBeInTheDocument();
    expect(screen.getByText("analyze_repo")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /new run/i })).toBeInTheDocument();
  });

  it("shows a poll error when there is no snapshot to describe", () => {
    render(
      <ErrorView
        snapshot={null}
        pollError={{ code: "thread_not_found", message: "No run with that id exists.", retryable: false, node: null }}
        onRetry={() => {}}
        onResumed={() => {}}
      />,
    );

    expect(screen.getByText(/no run with that id exists/i)).toBeInTheDocument();
  });

  it("reports a failed resume rather than appearing to succeed", async () => {
    const user = userEvent.setup();
    server.use(
      http.post(RESUME, () =>
        HttpResponse.json(
          { error: { code: "thread_not_awaiting_input", message: "That run has already completed.", retryable: false, node: null } },
          { status: 409 },
        ),
      ),
    );
    render(
      <ErrorView
        snapshot={aSnapshot({ status: "orphaned" })}
        pollError={null}
        onRetry={() => {}}
        onResumed={() => {}}
      />,
    );

    await user.click(screen.getByRole("button", { name: /resume/i }));

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(/already completed/i),
    );
  });
});
