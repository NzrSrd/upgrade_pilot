import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// RTL does not unmount between tests on its own when `globals` is false, and a
// component left mounted keeps its polling timers running into the next test.
afterEach(cleanup);
