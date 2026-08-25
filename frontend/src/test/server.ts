/**
 * One MSW server for the whole suite, started once in `setup.ts`.
 *
 * `onUnhandledRequest: "error"` on purpose: a request nobody stubbed is a test
 * that is quietly exercising the network, and the failure mode is a suite that
 * passes on one machine and hangs on another.
 */

import { setupServer } from "msw/node";

export const server = setupServer();
