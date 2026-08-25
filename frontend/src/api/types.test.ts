import { describe, expect, it } from "vitest";

import { ALL_STATUSES, TERMINAL_STATUSES } from "./types";

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

  it("treats a run that will not change on its own as terminal", () => {
    // `orphaned` is terminal for polling: its process is gone, so no amount of
    // waiting moves it. It is not terminal for the *run*, which a resume
    // continues from the checkpoint.
    expect([...TERMINAL_STATUSES].sort()).toEqual([
      "completed",
      "completed_with_warnings",
      "failed",
      "orphaned",
    ]);
  });

  it("does not treat awaiting_human as terminal", () => {
    // A resume can arrive from another client, and the transition out of the
    // decision panel is exactly what the user is waiting to see.
    expect(TERMINAL_STATUSES.has("awaiting_human")).toBe(false);
  });
});
