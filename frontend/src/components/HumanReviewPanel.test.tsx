import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import { anInterrupt } from "../test/fixtures";
import { server } from "../test/server";
import { HumanReviewPanel } from "./HumanReviewPanel";

const RESUME = "http://localhost/api/agent/resume";

const accepted = () =>
  HttpResponse.json(
    { thread_id: "t-1", status: "running", poll_url: "/api/agent/status/t-1" },
    { status: 202 },
  );

const conflict = () =>
  HttpResponse.json(
    { error: { code: "thread_not_awaiting_input", message: "That run is not waiting for an answer.", retryable: false, node: null } },
    { status: 409 },
  );

function panel(props: Partial<Parameters<typeof HumanReviewPanel>[0]> = {}) {
  return (
    <HumanReviewPanel
      threadId="t-1"
      decision={anInterrupt()}
      answered={0}
      onSubmitted={() => {}}
      {...props}
    />
  );
}

describe("HumanReviewPanel", () => {
  it("asks the question and says why it is being asked", () => {
    render(panel());

    expect(screen.getByText(/which migration strategy/i)).toBeInTheDocument();
    expect(screen.getByText(/pull in opposite directions/i)).toBeInTheDocument();
  });

  it("shows what happens if the user walks away", () => {
    // `consequences_if_unanswered` is carried on every payload and had no home
    // in the design pack. It is more useful than showing the user their own
    // constraints back to them.
    render(panel());

    expect(screen.getByText(/stops here and produces no plan/i)).toBeInTheDocument();
  });

  it("renders each option's trade-offs from its own fields", () => {
    render(panel());

    expect(screen.getByRole("radio", { name: /staged rollout/i })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /direct migration/i })).toBeInTheDocument();
    expect(screen.getByText(/two code paths coexist/i)).toBeInTheDocument();
    expect(screen.getByText(/short outage during deploy/i)).toBeInTheDocument();
  });

  it("marks the recommendation without preselecting it", () => {
    // A preselected recommendation is a decision the agent made, submitted
    // under a human's name. Spec 8.2 asks a question; it does not answer one.
    render(panel());

    expect(screen.getByText(/recommended/i)).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /staged rollout/i })).not.toBeChecked();
  });

  it("uses a radio group, not clickable divs", () => {
    render(panel());

    expect(screen.getByRole("radiogroup")).toBeInTheDocument();
    expect(screen.getAllByRole("radio")).toHaveLength(2);
  });

  it("keeps submit disabled until an option is chosen — guard one", () => {
    render(panel());

    expect(screen.getByRole("button", { name: /submit/i })).toBeDisabled();
  });

  it("disables submit the moment it is pressed — guard two", async () => {
    const user = userEvent.setup();
    let calls = 0;
    server.use(
      http.post(RESUME, async () => {
        calls += 1;
        await new Promise((resolve) => setTimeout(resolve, 50));
        return accepted();
      }),
    );
    render(panel());

    await user.click(screen.getByRole("radio", { name: /staged rollout/i }));
    const button = screen.getByRole("button", { name: /submit/i });
    await user.click(button);

    expect(button).toBeDisabled();
    await waitFor(() => expect(calls).toBe(1));
  });

  it("stays disabled after a successful submit, so a second answer is impossible", async () => {
    const user = userEvent.setup();
    const onSubmitted = vi.fn();
    server.use(http.post(RESUME, accepted));
    render(panel({ onSubmitted }));

    await user.click(screen.getByRole("radio", { name: /staged rollout/i }));
    await user.click(screen.getByRole("button", { name: /submit/i }));

    await waitFor(() => expect(onSubmitted).toHaveBeenCalledTimes(1));
    expect(screen.getByRole("button", { name: /submit|submitted/i })).toBeDisabled();
  });

  it("renders the server's 409 as already answered — guard three", async () => {
    // The only real guarantee. This is the case where the first two guards were
    // bypassed: two tabs, a replayed request, a resume from elsewhere.
    const user = userEvent.setup();
    server.use(http.post(RESUME, conflict));
    render(panel());

    await user.click(screen.getByRole("radio", { name: /staged rollout/i }));
    await user.click(screen.getByRole("button", { name: /submit/i }));

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(/already been answered/i),
    );
    expect(screen.getByRole("button", { name: /submit|submitted/i })).toBeDisabled();
  });

  it("re-enables after a retryable failure so the answer is not lost", async () => {
    // A 503 is not a duplicate. Leaving the panel dead would strand the user
    // with a question they cannot answer.
    const user = userEvent.setup();
    server.use(
      http.post(RESUME, () =>
        HttpResponse.json(
          { error: { code: "llm_unavailable", message: "The model provider is unavailable.", retryable: true, node: null } },
          { status: 502 },
        ),
      ),
    );
    render(panel());

    await user.click(screen.getByRole("radio", { name: /staged rollout/i }));
    await user.click(screen.getByRole("button", { name: /submit/i }));

    await waitFor(() => expect(screen.getByRole("button", { name: /submit/i })).toBeEnabled());
    expect(screen.getByRole("alert")).toHaveTextContent(/model provider is unavailable/i);
  });

  it("sends the question id and the chosen option", async () => {
    const user = userEvent.setup();
    let body: { thread_id: string; decision: { question_id: string; selected_option_id: string } } | null = null;
    server.use(
      http.post(RESUME, async ({ request }) => {
        body = (await request.json()) as typeof body;
        return accepted();
      }),
    );
    render(panel());

    await user.click(screen.getByRole("radio", { name: /direct migration/i }));
    await user.click(screen.getByRole("button", { name: /submit/i }));

    await waitFor(() => expect(body).not.toBeNull());
    expect(body!.thread_id).toBe("t-1");
    expect(body!.decision.question_id).toBe("q-1");
    expect(body!.decision.selected_option_id).toBe("direct_migration");
  });

  it("shows a rejected previous answer with what the server said", () => {
    // `validation_error` is set when the graph refused an answer, and the
    // question comes back carrying the reason. Without it the user re-submits
    // the same thing.
    render(
      panel({
        decision: anInterrupt({
          validation_error: "Choose one of: compatibility_layer, staged_rollout, direct_migration",
        }),
      }),
    );

    expect(screen.getByRole("alert")).toHaveTextContent(/choose one of/i);
  });

  it("says which question this is when more than one has been asked", () => {
    // `human_decisions` is an append channel so interrupts fire in sequence. A
    // second question must not read as a bug.
    render(panel({ answered: 1 }));

    expect(screen.getByText(/question 2/i)).toBeInTheDocument();
  });

  it("names the decision kind so the framing is not always strategy", () => {
    render(panel({ decision: anInterrupt({ kind: "risk_acceptance" }) }));

    expect(screen.getByText(/risk acceptance/i)).toBeInTheDocument();
  });
});
