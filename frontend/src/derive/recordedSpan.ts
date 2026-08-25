/**
 * How long a run's trace covers -- not how long the run has been open.
 *
 * "Elapsed time" was the wrong name for this, because this client cannot
 * observe when the server actually began, and a checkpointed run can be
 * resumed hours or days after it paused. Wall-clock since the browser first
 * saw the run would then be a number that looks authoritative and is not.
 * What *is* observable is the span between the first and last recorded
 * `TraceEvent.at`, so that is what this reports -- labelled "recorded span"
 * everywhere it appears, never "elapsed time".
 *
 * `end` prefers `FinalReport.completed_at` when the report exists, since
 * that is the authoritative close of the run; otherwise it falls back to
 * the last trace event's timestamp, which is the best available proxy while
 * a run is still in progress.
 */

import type { RunSnapshot } from "../api/types";

/**
 * A compact human duration: "3.2s" under a minute, "1m 24s" at or above one,
 * "1h 24m" at or above an hour. Sub-second precision is not useful here and
 * would read as false confidence in a client-side clock.
 */
function formatDuration(ms: number): string {
  const totalSeconds = Math.max(0, ms) / 1000;

  if (totalSeconds < 60) {
    return `${totalSeconds.toFixed(1)}s`;
  }

  const wholeSeconds = Math.round(totalSeconds);
  const hours = Math.floor(wholeSeconds / 3600);
  const minutes = Math.floor((wholeSeconds % 3600) / 60);
  const seconds = wholeSeconds % 60;

  if (hours > 0) {
    return `${hours}h ${minutes}m`;
  }
  return `${minutes}m ${seconds}s`;
}

/**
 * `null` when there is nothing recorded yet -- an empty trace has no first
 * event to measure from, and "0s" would assert a run has taken no time
 * rather than saying, honestly, that it has recorded nothing.
 */
export function recordedSpan(snapshot: RunSnapshot): string | null {
  const trace = snapshot.trace ?? [];
  if (trace.length === 0) return null;

  const start = new Date(trace[0].at).getTime();
  const endIso = snapshot.final_report?.completed_at ?? trace[trace.length - 1].at;
  const end = new Date(endIso).getTime();

  return formatDuration(end - start);
}
