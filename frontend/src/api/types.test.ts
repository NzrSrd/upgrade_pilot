import { describe, expect, it } from "vitest";

import { ALL_STATUSES, POLLING_STOPS_ON } from "./types";

describe("status unions", () => {
  it("lists exactly the seven statuses the backend derives", () => {
    expect([...ALL_STATUSES].sort()).toEqual([
      "awaiting_human",
      "completed",
      "completed_with_warnings",
      "failed",
      "orphaned",
      "queued",
      "running",
    ]);
  });

  it("stops polling a run that will not change on its own", () => {
    // `orphaned` stops the poll loop: its process is gone, so no amount of
    // waiting moves it on its own. It is not terminal for the *run* itself,
    // which an explicit resume continues from the checkpoint -- that is why
    // this set is named for the poll loop's behaviour, not the run's finality.
    expect([...POLLING_STOPS_ON].sort()).toEqual([
      "completed",
      "completed_with_warnings",
      "failed",
      "orphaned",
    ]);
  });

  it("does not stop polling on awaiting_human", () => {
    // A resume can arrive from another client, and the transition out of the
    // decision panel is exactly what the user is waiting to see.
    expect(POLLING_STOPS_ON.has("awaiting_human")).toBe(false);
  });
});
