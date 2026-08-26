import { describe, expect, it } from "vitest";

import { aFinalReport, aSnapshot, aTraceEvent } from "../test/fixtures";
import { recordedSpan } from "./recordedSpan";

describe("recordedSpan", () => {
  it("reports the interval between the first and last recorded trace event", () => {
    const snapshot = aSnapshot({
      trace: [
        aTraceEvent({ event_id: "e-1", at: "2026-08-25T12:00:00.000Z" }),
        aTraceEvent({ event_id: "e-2", at: "2026-08-25T12:01:24.000Z" }),
      ],
    });

    expect(recordedSpan(snapshot)).toBe("1m 24s");
  });

  it("does not render a zero for an empty trace", () => {
    // An empty trace has recorded nothing, which is a different claim from
    // "this run has taken no time" -- the distinction the coordinator asked
    // this figure to preserve.
    expect(recordedSpan(aSnapshot({ trace: [] }))).toBeNull();
  });

  it("prefers the final report's completed_at over the last trace event once the run is done", () => {
    const snapshot = aSnapshot({
      trace: [
        aTraceEvent({ event_id: "e-1", at: "2026-08-25T12:00:00.000Z" }),
        aTraceEvent({ event_id: "e-2", at: "2026-08-25T12:00:03.200Z" }),
      ],
      final_report: aFinalReport({ completed_at: "2026-08-25T12:05:00.000Z" }),
    });

    expect(recordedSpan(snapshot)).toBe("5m 0s");
  });
});
