/**
 * One read of `/api/health` for the sidebar's integration status.
 *
 * Not polled: it reports store reachability and whether a model key is
 * configured, neither of which changes while the page is open. Phase 9 found
 * this endpoint reporting on the wrong settings object, so what it says is
 * worth showing rather than assuming.
 */

import { useEffect, useState } from "react";

import { ApiFailure, getHealth } from "../api/client";
import type { ApiError, HealthResponse } from "../api/types";

export function useHealth(): { health: HealthResponse | null; error: ApiError | null } {
  const [state, setState] = useState<{ health: HealthResponse | null; error: ApiError | null }>({
    health: null,
    error: null,
  });

  useEffect(() => {
    const controller = new AbortController();
    getHealth(controller.signal)
      .then((health) => setState({ health, error: null }))
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setState({
          health: null,
          error:
            error instanceof ApiFailure
              ? error.error
              : {
                  code: "internal",
                  message: "The backend is unreachable.",
                  retryable: true,
                  node: null,
                },
        });
      });
    return () => controller.abort();
  }, []);

  return state;
}
