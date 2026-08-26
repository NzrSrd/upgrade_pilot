import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, delay, http } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";
import { POLL_MS } from "./hooks/useRunPolling";
import { anApiError, anInterrupt, aReport, aSnapshot } from "./test/fixtures";
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

/**
 * The bug fix round 1 exists for: `orphaned` sits in the set that stops the
 * poll loop (correctly -- nothing advances the run on its own), but a
 * successful resume was previously invisible to the UI. The resume request
 * really reached the backend and the run really continued; only the poll
 * loop never started again, because `useRunPolling`'s effect depends solely
 * on `threadId`, which a resume does not change. This test is the one that
 * would have caught it -- confirmed failing against the pre-fix code before
 * `restart` existed (see task-14-report.md, "Fix round 1").
 */
describe("App — resuming an orphaned run", () => {
  it("leaves the orphan view once the backend confirms the resumed run is running again", async () => {
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

    // Orphaned on the first poll (the run's process died before this test
    // ever looked at it); running from the second poll onward, which is the
    // poll a working `restart` has to actually issue.
    let statusCalls = 0;
    server.use(
      http.get(STATUS, () => {
        statusCalls += 1;
        return HttpResponse.json(
          aSnapshot({
            status: statusCalls === 1 ? "orphaned" : "running",
            completed_steps: ["analyze_repo"],
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

    const resumeButton = await screen.findByRole("button", { name: /resume/i });
    await user.click(resumeButton);

    // Not the decisive check on its own: clicking sets `resuming`, which
    // relabels this same button to "Resuming…" (no longer matching
    // `/resume/i`) well before any network round trip completes. A test that
    // stopped at "the resume button is gone" would pass on that relabel
    // alone and never touch the actual bug -- the poll loop restarting.
    expect(screen.queryByRole("heading", { name: /interrupted by a restart/i })).toBeInTheDocument();

    // The decisive assertion: once the backend reports `running` again, the
    // orphan panel itself -- keyed off `snapshot.status`, not the button's
    // own label -- must be gone. Under the bug this never resolves: the poll
    // loop stopped for good on the first `orphaned` response and nothing
    // ever asks the backend again, so the panel (and its resume button)
    // would sit there forever even though the backend already moved on.
    await waitFor(
      () =>
        expect(
          screen.queryByRole("heading", { name: /interrupted by a restart/i }),
        ).not.toBeInTheDocument(),
      { timeout: 5 * POLL_MS },
    );
    expect(statusCalls).toBeGreaterThanOrEqual(2);
  });
});

/**
 * Fix round 2, finding 4: the generic top banner and `ErrorView`'s own echo
 * of a poll error could render the same text twice, when `snapshot === null`
 * -- exactly `ErrorView`'s own "no snapshot to describe" branch. The fix
 * cedes that one case to `ErrorView`, the more specific owner, by suppressing
 * the banner only when `view === "error"` *and* `snapshot === null`.
 *
 * That exact combination cannot be produced through `App`'s own status
 * derivation (`status = snapshot?.status ?? "queued"` routes a null
 * snapshot to `"activity"`, never `"error"`), so there is no honest
 * full-stack test for the literal duplicate -- see task-14-report.md,
 * "Fix round 2", for why. What *is* both reachable and worth guarding
 * against is the fix over-reaching: this test proves the banner still
 * shows a poll error that arises with a snapshot already on screen, which
 * is not redundant with anything `ErrorView` says on its own.
 */
describe("App — a poll error while a snapshot already exists", () => {
  it("still shows the banner when the error is not one ErrorView already displays", async () => {
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

    // First poll: orphaned. Second poll -- the one `restart` fires after a
    // successful resume -- fails outright, a different problem from
    // anything the still-`orphaned` snapshot's own (empty) `errors` says.
    let statusCalls = 0;
    server.use(
      http.get(STATUS, () => {
        statusCalls += 1;
        if (statusCalls === 1) {
          return HttpResponse.json(aSnapshot({ status: "orphaned", completed_steps: ["analyze_repo"] }));
        }
        return HttpResponse.json(
          { error: { code: "thread_not_found", message: "No run with that id exists.", retryable: false, node: null } },
          { status: 404 },
        );
      }),
    );

    render(<App />);

    await user.type(screen.getByLabelText(/repository url/i), "https://example.com/repo.git");
    await user.type(screen.getByLabelText(/^dependency$/i), "pydantic");
    await user.type(screen.getByLabelText(/current version/i), "1.10.13");
    await user.type(screen.getByLabelText(/target version/i), "2.9.2");
    await user.click(screen.getByRole("button", { name: /start migration audit/i }));

    const resumeButton = await screen.findByRole("button", { name: /resume/i });
    await user.click(resumeButton);

    // The restart's own poll comes back 404. The last real snapshot still
    // reads `orphaned` (a failed poll preserves the previous snapshot), so
    // the orphan panel is still showing -- but the banner's message is new
    // information, not an echo, and must not have been suppressed by the
    // fix for the actual duplicate case.
    await waitFor(() =>
      expect(screen.getByText(/no run with that id exists/i)).toBeInTheDocument(),
    );
    expect(
      screen.getByRole("heading", { name: /interrupted by a restart/i }),
    ).toBeInTheDocument();
  });
});

/**
 * `App.tsx`'s `status = snapshot?.status ?? "queued"` used to feed straight
 * into `viewFor`, so a poll that came back refused with no snapshot ever
 * loaded rendered `ActivityTimeline`'s own "Queued" panel -- a status the
 * backend never reported, for a run that may not exist at all.
 *
 * Round 3 first fixed this with an override at the `App` call site, kept
 * `viewFor` pure. Round 4 corrected that shape: `unavailable` is now a real
 * `ViewStatus` member (the same reasoning as `idle` -- a genuine frontend
 * view state, not a backend status), so `status` itself carries the truth
 * and every surface that switches on it -- `viewFor` *and* `TopBar`'s
 * `WORDING`, both exhaustive -- has to say what it does with the new state.
 * That is why these tests check the pill as well as the view: round 3 fixed
 * the panel below while `TopBar`'s pill kept announcing "Queued" -- to a
 * screen-reader user, *spoken* -- for a run the panel said could not be
 * loaded. Round 4 is what makes both checks below pass at once.
 *
 * This is also where round 1's `ErrorView` `snapshot === null` copy branch
 * and round 2's banner-ownership guard both stop being dead code -- both
 * are reached here through a real poll failure inside `App`, not only
 * through a direct `ErrorView` render.
 */
describe("App — a poll error before any snapshot ever loads", () => {
  it("renders the error view rather than an activity timeline that claims the run is queued", async () => {
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
    );

    // The very first status poll refuses outright: no snapshot is ever
    // loaded for this thread -- e.g. a stale run whose checkpoint the
    // backend no longer has. `thread_not_found` is a real code for exactly
    // that.
    server.use(
      http.get(STATUS, () =>
        HttpResponse.json(
          { error: { code: "thread_not_found", message: "No run with that id exists.", retryable: false, node: null } },
          { status: 404 },
        ),
      ),
    );

    render(<App />);

    await user.type(screen.getByLabelText(/repository url/i), "https://example.com/repo.git");
    await user.type(screen.getByLabelText(/^dependency$/i), "pydantic");
    await user.type(screen.getByLabelText(/current version/i), "1.10.13");
    await user.type(screen.getByLabelText(/target version/i), "2.9.2");
    await user.click(screen.getByRole("button", { name: /start migration audit/i }));

    // Decisive: `ErrorView`'s own title for exactly this case (round 1's
    // copy branch), reached for the first time through `App` rather than a
    // direct render.
    await waitFor(() =>
      expect(
        screen.getByRole("heading", { name: /could not load this run/i }),
      ).toBeInTheDocument(),
    );

    // The fabricated claim this fix removes. Under the bug, `status` fell
    // back to `"queued"` and `ActivityTimeline` rendered its own "Queued"
    // panel -- "waiting for a run slot" for a run that was, in fact,
    // refused outright.
    expect(screen.queryByRole("heading", { name: /^queued$/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/waiting for a run slot/i)).not.toBeInTheDocument();

    // Round 2's banner cedes ownership to `ErrorView` for exactly this
    // case: the message renders once, not twice, and at most one
    // `role="alert"` is live -- there is none here, since `ErrorView` only
    // raises one on a *failed resume*, and there is no snapshot to resume.
    expect(screen.getAllByText(/no run with that id exists/i)).toHaveLength(1);
    expect(screen.queryAllByRole("alert")).toHaveLength(0);

    // Round 4's decisive assertion: `TopBar`'s pill -- the app's
    // `aria-live` region -- must not announce "Queued" for a run the panel
    // says could not be loaded. Under round 3 alone, `status` (unlike
    // `view`) still fell back to `"queued"`, so this would have failed here
    // even though the panel-level assertions above already passed.
    const pill = document.querySelector("[aria-live]");
    expect(pill).toHaveTextContent(/could not load this run/i);
    expect(pill).not.toHaveTextContent(/queued/i);
  });

  it("still shows activity before the first poll returns -- silence is not yet a refusal", async () => {
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
    );

    // The status poll never returns within this test: there is no error
    // yet, only silence, and "we have not heard back yet" must not be
    // routed to the error view the way "we asked and were told no" is.
    server.use(
      http.get(STATUS, async () => {
        await delay("infinite");
        return HttpResponse.json(aSnapshot({ status: "queued" }));
      }),
    );

    render(<App />);

    await user.type(screen.getByLabelText(/repository url/i), "https://example.com/repo.git");
    await user.type(screen.getByLabelText(/^dependency$/i), "pydantic");
    await user.type(screen.getByLabelText(/current version/i), "1.10.13");
    await user.type(screen.getByLabelText(/target version/i), "2.9.2");
    await user.click(screen.getByRole("button", { name: /start migration audit/i }));

    await waitFor(() =>
      expect(screen.getByRole("heading", { name: /^queued$/i })).toBeInTheDocument(),
    );
    expect(
      screen.queryByRole("heading", { name: /could not load this run/i }),
    ).not.toBeInTheDocument();

    // The distinction the whole fix turns on, checked in the pill too:
    // silence is not yet a refusal, so the pill must still say "Queued",
    // not the new wording.
    const pill = document.querySelector("[aria-live]");
    expect(pill).toHaveTextContent(/queued/i);
    expect(pill).not.toHaveTextContent(/could not load this run/i);
  });
});

/**
 * `graph/inspect.py`'s `pending_payload` returns `None` on purpose when an
 * interrupt exists but its value is not an `InterruptPayload` --
 * `is_awaiting_human` only checks that *an* interrupt exists, so the two can
 * disagree. Before this fix, `App` rendered nothing at all in that state
 * while the `TopBar` pill kept announcing a decision was pending -- a screen
 * reader would hear "waiting for your decision" over an empty workspace.
 */
describe("App — awaiting_human with no payload", () => {
  it("says the question has not been received, instead of rendering nothing", async () => {
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
      http.get(STATUS, () =>
        HttpResponse.json(aSnapshot({ status: "awaiting_human", pending_decision: null })),
      ),
    );

    render(<App />);

    await user.type(screen.getByLabelText(/repository url/i), "https://example.com/repo.git");
    await user.type(screen.getByLabelText(/^dependency$/i), "pydantic");
    await user.type(screen.getByLabelText(/current version/i), "1.10.13");
    await user.type(screen.getByLabelText(/target version/i), "2.9.2");
    await user.click(screen.getByRole("button", { name: /start migration audit/i }));

    await waitFor(() =>
      expect(screen.getByText(/question has not been received/i)).toBeInTheDocument(),
    );

    // The pill says a decision is pending; the workspace must not be silent
    // underneath it.
    const pill = document.querySelector("[aria-live]");
    expect(pill).toHaveTextContent(/waiting for your decision/i);
  });
  it("carries a failed run's inputs into a corrected run, and clears them for a new one", async () => {
    // The whole feature, end to end through the composition: a mistyped path
    // is the only thing that should need retyping, and "New migration run"
    // must still mean a blank form afterwards -- App holds the prefill, so
    // without clearing it the sidebar's blank-form button is not blank.
    const user = userEvent.setup({ delay: null, advanceTimers: vi.advanceTimersByTime });
    server.use(
      http.get(HEALTH, () =>
        HttpResponse.json({ status: "ok", version: "0.1.0", checks: { chroma_dir: true, checkpoint_dir: true, llm_configured: true } }),
      ),
      http.post(START, () =>
        HttpResponse.json({ thread_id: "t-1", status: "running", poll_url: "/api/agent/status/t-1" }, { status: 202 }),
      ),
      http.get(STATUS, () =>
        HttpResponse.json(
          aSnapshot({
            status: "completed_with_warnings",
            errors: [anApiError()],
            final_report: aReport({
              completed_with_warnings: true,
              repo_ref: { kind: "local", path: "/User/Code/payments-service" },
              constraints: { zero_downtime: true, minimize_effort: false, deadline: null, risk_tolerance: "low" },
            }),
          }),
        ),
      ),
    );

    render(<App />);

    await user.click(screen.getByRole("radio", { name: /local/i }));
    await user.type(screen.getByLabelText(/local path/i), "/User/Code/payments-service");
    await user.type(screen.getByLabelText(/^dependency$/i), "pydantic");
    await user.type(screen.getByLabelText(/current version/i), "1.10.13");
    await user.type(screen.getByLabelText(/target version/i), "2.9.2");
    await user.click(screen.getByRole("button", { name: /start migration audit/i }));

    await waitFor(() =>
      expect(screen.getByRole("button", { name: /corrected run/i })).toBeInTheDocument(),
    );
    await user.click(screen.getByRole("button", { name: /corrected run/i }));

    // Back on the form, holding the run's own inputs -- including the
    // constraints, which the user would otherwise have to remember.
    expect(screen.getByLabelText(/local path/i)).toHaveValue("/User/Code/payments-service");
    expect(screen.getByLabelText(/^dependency$/i)).toHaveValue("pydantic");
    expect(screen.getByLabelText(/current version/i)).toHaveValue("1.10.13");
    expect(screen.getByLabelText(/zero downtime/i)).toBeChecked();
    expect(screen.getByLabelText(/risk tolerance/i)).toHaveValue("low");

    // And a new run means new: the prefill does not outlive the retry.
    await user.click(screen.getByRole("button", { name: /new migration run/i }));

    expect(screen.getByLabelText(/repository url/i)).toHaveValue("");
    expect(screen.getByLabelText(/^dependency$/i)).toHaveValue("");
    expect(screen.getByLabelText(/risk tolerance/i)).toHaveValue("medium");
  });
});
