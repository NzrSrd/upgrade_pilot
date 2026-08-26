import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";
import { POLL_MS } from "./hooks/useRunPolling";
import { anInterrupt, aSnapshot } from "./test/fixtures";
import { server } from "./test/server";

const HEALTH = "http://localhost/api/health";
const START = "http://localhost/api/agent/start";
const STATUS = "http://localhost/api/agent/status/t-1";
const RESUME = "http://localhost/api/agent/resume";

// Fake timers, not real ones: `useRunPolling` schedules its next request
// exactly `POLL_MS` after the last one settles, and this test needs that
// second poll to land at a precise, chosen moment -- after the first answer
// is submitted, not racing against it. Real timers made this test flaky: the
// background 1s poll could fire mid-interaction (between selecting an option
// and clicking submit), silently swapping in question 2's fresh, unselected
// button underneath the click. `delay: null` on `userEvent.setup` keeps its
// internal pacing from depending on the timers this file fakes.
beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

/**
 * Reproduces the composition defect, not the component in isolation:
 * `HumanReviewPanel` is mounted by `App` with no `key` unless one is given,
 * and guard two (`submitting` never clears on success) only matters *because*
 * `human_decisions` is an append channel that fires interrupts in sequence.
 * Neither decision alone is wrong; together, without a key, the second
 * question inherits the first one's "already submitted" state and becomes
 * permanently unanswerable.
 */
describe("App — sequential interrupts", () => {
  it("lets the user answer a second question after answering the first", async () => {
    const user = userEvent.setup({ delay: null });

    server.use(
      http.get(HEALTH, () =>
        HttpResponse.json({
          status: "ok",
          version: "test",
          checks: { checkpoint_dir: true, chroma_dir: true, llm_configured: true },
        }),
      ),
      http.post(START, () =>
        HttpResponse.json(
          { thread_id: "t-1", status: "queued", poll_url: "/api/agent/status/t-1" },
          { status: 202 },
        ),
      ),
      http.post(RESUME, () =>
        HttpResponse.json(
          { thread_id: "t-1", status: "running", poll_url: "/api/agent/status/t-1" },
          { status: 202 },
        ),
      ),
    );

    const decisionA = anInterrupt({ question_id: "q-1" });
    const decisionB = anInterrupt({
      question_id: "q-2",
      question: "Should the plan accept the residual risk on the auth module?",
    });
    let statusCalls = 0;
    server.use(
      http.get(STATUS, () => {
        statusCalls += 1;
        if (statusCalls === 1) {
          return HttpResponse.json(
            aSnapshot({ status: "awaiting_human", pending_decision: decisionA, human_decisions: [] }),
          );
        }
        // The second and every later poll: the graph resumed past question 1
        // and now waits on question 2.
        return HttpResponse.json(
          aSnapshot({
            status: "awaiting_human",
            pending_decision: decisionB,
            human_decisions: [
              {
                question_id: "q-1",
                selected_option_id: "staged_rollout",
                rationale: null,
                decided_at: "2026-08-25T12:00:00Z",
              },
            ],
          }),
        );
      }),
    );

    render(<App />);

    await user.type(screen.getByLabelText(/repository url/i), "https://example.com/repo.git");
    await user.type(screen.getByLabelText(/^dependency$/i), "pydantic");
    await user.type(screen.getByLabelText(/current version/i), "1.10.13");
    await user.type(screen.getByLabelText(/target version/i), "2.9.2");
    await user.click(screen.getByRole("button", { name: /start migration audit/i }));

    // Question 1 arrives from the first poll (fired immediately, no timer).
    await waitFor(() =>
      expect(screen.getByRole("radio", { name: /staged rollout/i })).toBeInTheDocument(),
    );

    await user.click(screen.getByRole("radio", { name: /staged rollout/i }));
    await user.click(screen.getByRole("button", { name: /submit decision/i }));

    // Guard two: correctly and permanently disabled for the question it just
    // answered. A *successful* (202) resume leaves `settled` false -- only a
    // 409 sets that -- so the button reads "Submitting..." forever by
    // design, never "Submitted"; the accessible name still contains "submit"
    // either way, which is what every other test in this suite matches on.
    // The virtual clock has not moved, so this is still the same question-1
    // instance, undisturbed by any background poll.
    await waitFor(() => expect(screen.getByRole("button", { name: /submit/i })).toBeDisabled());

    // Now advance exactly one poll interval. Without a `key` on
    // `HumanReviewPanel` in `App`, React would reuse this exact component
    // instance and its `submitting` state for question 2, and the button
    // below would stay stuck disabled forever -- for a question the user was
    // never asked to answer.
    await vi.advanceTimersByTimeAsync(POLL_MS);

    await waitFor(() =>
      expect(screen.getByText(/residual risk on the auth module/i)).toBeInTheDocument(),
    );

    // No preselection carried over from question 1 -- this is a fresh
    // instance, not a reused one.
    const secondRadio = screen.getByRole("radio", { name: /staged rollout/i });
    expect(secondRadio).not.toBeChecked();

    // Guard one still functions normally on its own terms: nothing selected
    // yet, so correctly disabled.
    expect(screen.getByRole("button", { name: /submit decision/i })).toBeDisabled();

    // The decisive assertion. Under the bug, this button would stay disabled
    // forever no matter what is clicked, because the reused instance's
    // `submitting` flag from question 1 is permanently `true` and `blocked`
    // never clears. With the key in place, this is a fresh instance: `submit
    // decision` on question 2 must be answerable.
    await user.click(secondRadio);
    expect(screen.getByRole("button", { name: /submit decision/i })).toBeEnabled();
  });
});
