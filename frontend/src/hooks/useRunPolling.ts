/**
 * The only source of run state.
 *
 * One hook, one in-flight request, one complete snapshot per tick. Because a
 * `RunSnapshot` describes the whole run rather than a delta, nothing here
 * accumulates and nothing downstream merges — which is the concrete payoff
 * ADR-001:68 banked when it chose polling over SSE, and the reason this hook
 * can be the whole of the frontend's state management.
 *
 * Two distinctions do the real work:
 *
 *   - **A refusal is not a network blip.** An `ApiFailure` (404 on an unknown
 *     thread, say) stops the loop and reports itself. Retrying it for ever
 *     would bury the one message the user needs behind a spinner.
 *   - **The next tick is scheduled after the last one settles**, not on a
 *     fixed interval. A slow backend would otherwise stack requests until one
 *     of them answered.
 *
 * A third distinction, added in fix round 1: **a stopped loop is not a dead
 * one.** `POLLING_STOPS_ON` includes `orphaned`, correctly — nothing advances
 * an abandoned run on its own, so ticking it once a second is pointless. But
 * `orphaned` is resumable, and a resume needs the loop back. `restart` is
 * that reentry point: it tears down whatever loop is currently running (there
 * is never more than one) and starts a fresh one against the same
 * `threadId`, which `onResumed` calls once the resume request the server
 * already accepted needs the UI to start watching again.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiFailure, getStatus } from "../api/client";
import type { ApiError, RunSnapshot } from "../api/types";
import { POLLING_STOPS_ON } from "../api/types";

export const POLL_MS = 1000;

/**
 * Exported so the test asserts against these numbers rather than a copy of
 * them — a copy is a second place to change when the cadence changes.
 */
export const BACKOFF_MS: readonly number[] = [1000, 2000, 4000, 8000, 15000];

export type PollState = {
  snapshot: RunSnapshot | null;
  error: ApiError | null;
  reconnecting: boolean;
};

/** `PollState` plus the one control this hook exposes. */
export type RunPolling = PollState & {
  /**
   * Re-enters the poll loop for the current `threadId`. Safe to call whether
   * polling is currently live (a fresh loop replaces the running one; there
   * is never a moment with two) or already stopped (the normal case: a
   * status in `POLLING_STOPS_ON` stopped it, and this is the explicit resume
   * that starts it again). A no-op while `threadId` is `null` — there is
   * nothing to poll.
   */
  restart: () => void;
};

const INITIAL: PollState = { snapshot: null, error: null, reconnecting: false };

export function useRunPolling(threadId: string | null): RunPolling {
  const [state, setState] = useState<PollState>(INITIAL);
  // Holds the teardown for whichever loop is currently live, so a fresh
  // start can always call it first. That single call is what keeps
  // `restart` from ever running two loops at once, whether the previous one
  // was still ticking or had already stopped on its own.
  const stopCurrentLoopRef = useRef<() => void>(() => {});
  // Holds the loop starter itself. Reassigned on every effect run so
  // `restart` always starts against the `threadId` current *renders* have
  // reconciled to, not a stale closure from a previous one.
  const startLoopRef = useRef<() => void>(() => {});

  useEffect(() => {
    if (threadId === null) {
      setState(INITIAL);
      startLoopRef.current = () => {};
      return;
    }

    // Narrowed to a local `const`: TypeScript cannot carry the `threadId !==
    // null` guard above into these nested functions, because `threadId` is a
    // captured function parameter and control-flow narrowing does not survive
    // capture by a closure that could (as far as the checker knows) run after
    // a reassignment.
    const id = threadId;

    function startLoop(): void {
      // Idempotent by construction: whatever loop is currently live (there
      // is at most one) is torn down before this one begins, so a `restart`
      // called mid-poll cannot leave two loops running.
      stopCurrentLoopRef.current();

      const controller = new AbortController();
      let timer: ReturnType<typeof setTimeout> | undefined;
      // Both are needed, for narrower reasons than it might look: `abort()`
      // cancels a request already on the wire, while `stopped` is the flag
      // the success path in `tick` actually checks once a response comes
      // back. That path tests `stopped`, not `signal.aborted`, so the abort
      // alone would not stop it from setting state after cleanup.
      let stopped = false;
      let failures = 0;

      function schedule(ms: number): void {
        timer = setTimeout(() => {
          void tick();
        }, ms);
      }

      async function tick(): Promise<void> {
        try {
          const snapshot = await getStatus(id, controller.signal);
          if (stopped) return;

          failures = 0;
          setState({ snapshot, error: null, reconnecting: false });

          if (POLLING_STOPS_ON.has(snapshot.status)) return;
          schedule(POLL_MS);
        } catch (error) {
          if (stopped || controller.signal.aborted) return;

          if (error instanceof ApiFailure) {
            // The server answered, and its answer was no. Stop.
            setState((previous) => ({ ...previous, error: error.error, reconnecting: false }));
            return;
          }

          failures += 1;
          setState((previous) => ({ ...previous, reconnecting: true }));
          schedule(BACKOFF_MS[Math.min(failures - 1, BACKOFF_MS.length - 1)]);
        }
      }

      stopCurrentLoopRef.current = () => {
        stopped = true;
        controller.abort();
        if (timer !== undefined) clearTimeout(timer);
      };

      void tick();
    }

    startLoopRef.current = startLoop;

    // A new thread starts from nothing, so the previous run's report cannot
    // linger on screen for the first second of this one.
    setState(INITIAL);
    startLoop();

    return () => {
      stopCurrentLoopRef.current();
      startLoopRef.current = () => {};
    };
  }, [threadId]);

  // Stable across renders, unlike the closure it calls through to: the ref
  // it reads is reassigned on every effect run, but the callback identity
  // itself does not need to change for that, so a consumer that passes
  // `restart` down as a prop is not forced to re-render on its account.
  const restart = useCallback(() => startLoopRef.current(), []);

  return { ...state, restart };
}
