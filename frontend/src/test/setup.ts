import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { afterAll, afterEach, beforeAll } from "vitest";

import { server } from "./server";

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
