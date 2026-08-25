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
 */

import { useEffect, useState } from "react";

import { ApiFailure, getStatus } from "../api/client";
import type { ApiError, RunSnapshot } from "../api/types";
import { TERMINAL_STATUSES } from "../api/types";

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

const INITIAL: PollState = { snapshot: null, error: null, reconnecting: false };

export function useRunPolling(threadId: string | null): PollState {
  const [state, setState] = useState<PollState>(INITIAL);

  useEffect(() => {
    if (threadId === null) {
      setState(INITIAL);
      return;
    }

    // A new thread starts from nothing, so the previous run's report cannot
    // linger on screen for the first second of this one.
    setState(INITIAL);

    // Narrowed to a local `const`: TypeScript cannot carry the `threadId !==
    // null` guard above into these nested functions, because `threadId` is a
    // captured function parameter and control-flow narrowing does not survive
    // capture by a closure that could (as far as the checker knows) run after
    // a reassignment.
    const id = threadId;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
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

        if (TERMINAL_STATUSES.has(snapshot.status)) return;
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

    void tick();

    return () => {
      stopped = true;
      controller.abort();
      if (timer !== undefined) clearTimeout(timer);
    };
  }, [threadId]);

  return state;
}
