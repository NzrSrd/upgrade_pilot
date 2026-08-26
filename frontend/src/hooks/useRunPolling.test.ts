import { renderHook, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { RunStatus } from "../api/types";
import { aSnapshot } from "../test/fixtures";
import { server } from "../test/server";
import { BACKOFF_MS, POLL_MS, useRunPolling } from "./useRunPolling";

const STATUS = "http://localhost/api/agent/status/t-1";

/** Answer each poll from a queue, so a test can script a sequence. */
function scriptSnapshots(...statuses: string[]) {
  let call = 0;
  const seen = () => call;
  server.use(
    http.get(STATUS, () => {
      const status = statuses[Math.min(call, statuses.length - 1)];
      call += 1;
      return HttpResponse.json(aSnapshot({ status: status as RunStatus }));
    }),
  );
  return seen;
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("useRunPolling", () => {
  it("issues no request and holds no snapshot without a thread id", async () => {
    // `onUnhandledRequest: "error"` means a stray fetch fails the test, so the
    // absence of a handler here is the assertion.
    const { result } = renderHook(() => useRunPolling(null));

    await vi.advanceTimersByTimeAsync(5 * POLL_MS);

    expect(result.current.snapshot).toBeNull();
    expect(result.current.error).toBeNull();
  });

  it("fetches immediately rather than waiting out the first interval", async () => {
    // A user who just pressed Start should not watch a blank second.
    scriptSnapshots("running");
    const { result } = renderHook(() => useRunPolling("t-1"));

    await waitFor(() => expect(result.current.snapshot).not.toBeNull());
    expect(result.current.snapshot?.status).toBe("running");
  });

  it("polls once per second while the run is not terminal", async () => {
    const calls = scriptSnapshots("running");
    renderHook(() => useRunPolling("t-1"));

    await waitFor(() => expect(calls()).toBe(1));
    await vi.advanceTimersByTimeAsync(POLL_MS);
    expect(calls()).toBe(2);
    await vi.advanceTimersByTimeAsync(POLL_MS);
    expect(calls()).toBe(3);
  });

  it("keeps polling while a decision is outstanding", async () => {
    // `awaiting_human` is deliberately not terminal: a resume can arrive from
    // another client, and the transition out of the decision panel is exactly
    // what the user is watching for.
    const calls = scriptSnapshots("awaiting_human");
    renderHook(() => useRunPolling("t-1"));

    await waitFor(() => expect(calls()).toBe(1));
    await vi.advanceTimersByTimeAsync(3 * POLL_MS);
    expect(calls()).toBe(4);
  });

  it("stops when the run completes", async () => {
    const calls = scriptSnapshots("running", "completed");
    const { result } = renderHook(() => useRunPolling("t-1"));

    await waitFor(() => expect(calls()).toBe(1));
    await vi.advanceTimersByTimeAsync(POLL_MS);
    await waitFor(() => expect(result.current.snapshot?.status).toBe("completed"));

    await vi.advanceTimersByTimeAsync(10 * POLL_MS);
    expect(calls()).toBe(2);
  });

  it("stops on an orphaned run, whose process is gone", async () => {
    const calls = scriptSnapshots("orphaned");
    renderHook(() => useRunPolling("t-1"));

    await waitFor(() => expect(calls()).toBe(1));
    await vi.advanceTimersByTimeAsync(10 * POLL_MS);
    expect(calls()).toBe(1);
  });

  it("restarts polling on an orphaned run and observes the status change a resume produces", async () => {
    // Fix round 1: `orphaned` correctly stops the loop above -- nothing
    // advances an abandoned run on its own -- but it is the one
    // stopped-but-resumable status, and `restart` is the explicit reentry
    // this test exists to prove works.
    const calls = scriptSnapshots("orphaned", "running");
    const { result } = renderHook(() => useRunPolling("t-1"));

    await waitFor(() => expect(calls()).toBe(1));
    await vi.advanceTimersByTimeAsync(10 * POLL_MS);
    expect(calls()).toBe(1);
    expect(result.current.snapshot?.status).toBe("orphaned");

    result.current.restart();

    // Ticks immediately, exactly like the very first poll of a fresh
    // thread id -- no interval to wait out.
    await waitFor(() => expect(calls()).toBe(2));
    await waitFor(() => expect(result.current.snapshot?.status).toBe("running"));

    // And the ordinary cadence resumes from there, on a clean backoff.
    await vi.advanceTimersByTimeAsync(POLL_MS);
    expect(calls()).toBe(3);
  });

  it("does not start a second loop when restart is called while polling is already live", async () => {
    const calls = scriptSnapshots("running");
    const { result } = renderHook(() => useRunPolling("t-1"));

    await waitFor(() => expect(calls()).toBe(1));

    // Live, not stopped: `restart` must tear this loop down before starting
    // a fresh one, or the next interval would bring two ticks instead of
    // one.
    result.current.restart();
    await waitFor(() => expect(calls()).toBe(2));

    await vi.advanceTimersByTimeAsync(POLL_MS);
    expect(calls()).toBe(3);
    await vi.advanceTimersByTimeAsync(POLL_MS);
    expect(calls()).toBe(4);
  });

  it("does nothing when restart is called with no thread id", async () => {
    const { result } = renderHook(() => useRunPolling(null));

    expect(() => result.current.restart()).not.toThrow();
    await vi.advanceTimersByTimeAsync(5 * POLL_MS);
    expect(result.current.snapshot).toBeNull();
  });

  it("stops on a failed run", async () => {
    const calls = scriptSnapshots("failed");
    renderHook(() => useRunPolling("t-1"));

    await waitFor(() => expect(calls()).toBe(1));
    await vi.advanceTimersByTimeAsync(10 * POLL_MS);
    expect(calls()).toBe(1);
  });

  it("backs off on a network error and marks itself reconnecting", async () => {
    let calls = 0;
    server.use(
      http.get(STATUS, () => {
        calls += 1;
        return HttpResponse.error();
      }),
    );
    const { result } = renderHook(() => useRunPolling("t-1"));

    await waitFor(() => expect(result.current.reconnecting).toBe(true));
    expect(calls).toBe(1);

    // Still waiting out the first backoff, which is longer than a poll.
    await vi.advanceTimersByTimeAsync(BACKOFF_MS[0] - 1);
    expect(calls).toBe(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(calls).toBe(2);

    // Second failure waits longer than the first.
    await vi.advanceTimersByTimeAsync(BACKOFF_MS[1] - 1);
    expect(calls).toBe(2);
    await vi.advanceTimersByTimeAsync(1);
    expect(calls).toBe(3);
  });

  it("resets the backoff and clears reconnecting once a poll succeeds", async () => {
    let calls = 0;
    server.use(
      http.get(STATUS, () => {
        calls += 1;
        return calls === 1 ? HttpResponse.error() : HttpResponse.json(aSnapshot({ status: "running" }));
      }),
    );
    const { result } = renderHook(() => useRunPolling("t-1"));

    await waitFor(() => expect(result.current.reconnecting).toBe(true));
    await vi.advanceTimersByTimeAsync(BACKOFF_MS[0]);
    await waitFor(() => expect(result.current.reconnecting).toBe(false));

    // Back to the ordinary cadence, not still backed off.
    await vi.advanceTimersByTimeAsync(POLL_MS);
    expect(calls).toBe(3);
  });

  it("does not raise the backoff past its cap", async () => {
    let calls = 0;
    server.use(
      http.get(STATUS, () => {
        calls += 1;
        return HttpResponse.error();
      }),
    );
    renderHook(() => useRunPolling("t-1"));

    await waitFor(() => expect(calls).toBe(1));
    for (const delay of BACKOFF_MS) {
      await vi.advanceTimersByTimeAsync(delay);
    }
    const beforeCap = calls;
    // Two more waits at the capped delay, not an ever-growing one.
    await vi.advanceTimersByTimeAsync(BACKOFF_MS[BACKOFF_MS.length - 1]);
    await vi.advanceTimersByTimeAsync(BACKOFF_MS[BACKOFF_MS.length - 1]);
    expect(calls).toBe(beforeCap + 2);
  });

  it("stops and reports a refusal rather than retrying it", async () => {
    // A 404 is not a network blip. Retrying a thread that does not exist for
    // ever would hide the one message the user needs.
    let calls = 0;
    server.use(
      http.get(STATUS, () => {
        calls += 1;
        return HttpResponse.json(
          { error: { code: "thread_not_found", message: "No run with that id exists.", retryable: false, node: null } },
          { status: 404 },
        );
      }),
    );
    const { result } = renderHook(() => useRunPolling("t-1"));

    await waitFor(() => expect(result.current.error?.code).toBe("thread_not_found"));
    expect(result.current.reconnecting).toBe(false);

    await vi.advanceTimersByTimeAsync(20 * POLL_MS);
    expect(calls).toBe(1);
  });

  it("issues no further request after unmount", async () => {
    const calls = scriptSnapshots("running");
    const { unmount } = renderHook(() => useRunPolling("t-1"));

    await waitFor(() => expect(calls()).toBe(1));
    unmount();
    await vi.advanceTimersByTimeAsync(20 * POLL_MS);

    expect(calls()).toBe(1);
  });

  it("never has two requests in flight at once", async () => {
    // The next timer is scheduled after the previous request settles, not on a
    // fixed interval. A slow backend would otherwise stack requests until one
    // of them answered.
    let inFlight = 0;
    let overlapped = false;
    server.use(
      http.get(STATUS, async () => {
        inFlight += 1;
        overlapped ||= inFlight > 1;
        await new Promise((resolve) => setTimeout(resolve, 3 * POLL_MS));
        inFlight -= 1;
        return HttpResponse.json(aSnapshot({ status: "running" }));
      }),
    );
    renderHook(() => useRunPolling("t-1"));

    await vi.advanceTimersByTimeAsync(12 * POLL_MS);
    expect(overlapped).toBe(false);
  });

  it("drops the previous run's state when the thread id changes", async () => {
    server.use(
      http.get("http://localhost/api/agent/status/:threadId", ({ params }) =>
        HttpResponse.json(aSnapshot({ thread_id: String(params.threadId), status: "running" })),
      ),
    );
    const { result, rerender } = renderHook(({ id }: { id: string }) => useRunPolling(id), {
      initialProps: { id: "t-1" },
    });

    await waitFor(() => expect(result.current.snapshot?.thread_id).toBe("t-1"));
    rerender({ id: "t-2" });
    await waitFor(() => expect(result.current.snapshot?.thread_id).toBe("t-2"));
  });
});
