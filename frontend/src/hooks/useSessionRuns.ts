/**
 * The runs this browser tab started. Not a history feature.
 *
 * Listing past runs needs the Postgres run registry, which is sub-project 3.
 * What is honest today is what this tab did, held in `sessionStorage` and
 * labelled "this session" in the sidebar — and it is genuinely useful, because
 * `/api/agent/status/{thread_id}` still answers for any thread the SQLite
 * checkpointer holds, so clicking one reopens its report.
 */

import { useCallback, useState } from "react";

const KEY = "upgradepilot.runs";

export type SessionRun = {
  threadId: string;
  dependency: string;
  from: string;
  to: string;
};

function load(): SessionRun[] {
  try {
    const raw = sessionStorage.getItem(KEY);
    const parsed: unknown = raw === null ? [] : JSON.parse(raw);
    return Array.isArray(parsed) ? (parsed as SessionRun[]) : [];
  } catch {
    // `sessionStorage` is user-writable and outlives a deploy that changes
    // this shape. Throwing here would make the whole application unreachable
    // until the user knew to clear site data — so a bad value is discarded,
    // which is the one case where dropping data is the honest move.
    return [];
  }
}

export function useSessionRuns(): {
  runs: SessionRun[];
  remember: (run: SessionRun) => void;
} {
  const [runs, setRuns] = useState<SessionRun[]>(load);

  const remember = useCallback((run: SessionRun) => {
    setRuns((previous) => {
      const next = [run, ...previous.filter((each) => each.threadId !== run.threadId)];
      try {
        sessionStorage.setItem(KEY, JSON.stringify(next));
      } catch {
        // A full or disabled store must not stop the run the user just began.
      }
      return next;
    });
  }, []);

  return { runs, remember };
}
