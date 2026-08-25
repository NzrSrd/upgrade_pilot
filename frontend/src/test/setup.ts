import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { afterAll, afterEach, beforeAll, vi } from "vitest";

import { server } from "./server";

/**
 * `@testing-library/dom`'s `waitFor` drives fake timers itself, but only
 * once it has detected them, and detection is gated behind a global `jest`
 * (`helpers.js: typeof jest !== "undefined"`) before it even inspects
 * `setTimeout` for the sinon-style `.clock` marker Vitest's fake timers
 * already carry. Vitest never defines that global on its own, so without
 * this shim `waitFor` silently takes its "real timers" branch — a
 * `setInterval` poll that is itself faked and so never fires — and every
 * `waitFor` under `vi.useFakeTimers()` hangs until the real wall-clock test
 * timeout. The shim supplies only the one method `waitFor` calls.
 */
if (typeof (globalThis as { jest?: unknown }).jest === "undefined") {
  (globalThis as { jest?: { advanceTimersByTime: (ms: number) => void } }).jest = {
    advanceTimersByTime: (ms: number) => vi.advanceTimersByTime(ms),
  };
}

beforeAll(() => {
  server.listen({ onUnhandledRequest: "error" });
});

// RTL does not unmount between tests when `globals` is false, and a component
// left mounted keeps its polling timers running into the next test.
afterEach(() => {
  cleanup();
  server.resetHandlers();
});

afterAll(() => {
  server.close();
});
