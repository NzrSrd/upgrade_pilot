import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import { server } from "../test/server";
import { aSnapshot } from "../test/fixtures";
import { ApiFailure, getStatus, resumeRun, startRun } from "./client";

const BASE = "http://localhost";

describe("client", () => {
  it("returns a parsed snapshot on success", async () => {
    const snapshot = aSnapshot({ thread_id: "t-9", status: "awaiting_human" });
    server.use(http.get(`${BASE}/api/agent/status/t-9`, () => HttpResponse.json(snapshot)));

    await expect(getStatus("t-9")).resolves.toMatchObject({
      thread_id: "t-9",
      status: "awaiting_human",
    });
  });

  it("encodes the thread id into the path", async () => {
    // A thread id is a uuid today, but a path built by concatenation is a
    // request-forgery shape waiting for the day it is not.
    server.use(
      http.get(`${BASE}/api/agent/status/:threadId`, ({ params }) =>
        HttpResponse.json(aSnapshot({ thread_id: String(params.threadId) })),
      ),
    );

    const snapshot = await getStatus("a b/c");
    expect(snapshot.thread_id).toBe("a b/c");
  });

  it("throws an ApiFailure carrying the server's own error body", async () => {
    // The backend declares its error shape on every route so the client never
    // has to guess. Rendering `[object Object]` is the failure this prevents.
    server.use(
      http.get(`${BASE}/api/agent/status/nope`, () =>
        HttpResponse.json(
          { error: { code: "thread_not_found", message: "No run with that id exists.", retryable: false, node: null } },
          { status: 404 },
        ),
      ),
    );

    const failure = await getStatus("nope").catch((error: unknown) => error);

    expect(failure).toBeInstanceOf(ApiFailure);
    expect((failure as ApiFailure).httpStatus).toBe(404);
    expect((failure as ApiFailure).error.code).toBe("thread_not_found");
    expect((failure as ApiFailure).message).toBe("No run with that id exists.");
  });

  it("surfaces a 409 as an ApiFailure the caller can branch on", async () => {
    // The third and only real duplicate-submit guard.
    server.use(
      http.post(`${BASE}/api/agent/resume`, () =>
        HttpResponse.json(
          { error: { code: "thread_not_awaiting_input", message: "That run is not waiting for an answer.", retryable: false, node: null } },
          { status: 409 },
        ),
      ),
    );

    const failure = await resumeRun({ thread_id: "t-1", decision: null }).catch(
      (error: unknown) => error,
    );

    expect((failure as ApiFailure).httpStatus).toBe(409);
  });

  it("synthesises an error when the body is not the declared shape", async () => {
    // A proxy or gateway can answer with HTML. Without this the client throws
    // a JSON parse error, which tells the user nothing about what happened.
    server.use(
      http.post(`${BASE}/api/agent/start`, () =>
        HttpResponse.text("<html>502 Bad Gateway</html>", { status: 502 }),
      ),
    );

    const failure = await startRun({
      repo: { url: "https://example.invalid/r.git", path: null },
      dependency: { name: "pydantic", current_version: "1.10.13", target_version: "2.9.2" },
      constraints: { zero_downtime: false, minimize_effort: false, deadline: null, risk_tolerance: "medium" },
    }).catch((error: unknown) => error);

    expect(failure).toBeInstanceOf(ApiFailure);
    expect((failure as ApiFailure).httpStatus).toBe(502);
    expect((failure as ApiFailure).error.code).toBe("internal");
    expect((failure as ApiFailure).error.retryable).toBe(true);
  });

  it("lets an abort propagate rather than dressing it as a server error", async () => {
    // The polling hook aborts on unmount. An abort reported as a failure would
    // paint an error banner every time the user navigates away.
    server.use(http.get(`${BASE}/api/agent/status/t-1`, () => HttpResponse.json(aSnapshot())));
    const controller = new AbortController();
    controller.abort();

    const failure = await getStatus("t-1", controller.signal).catch((error: unknown) => error);

    expect(failure).not.toBeInstanceOf(ApiFailure);
    expect((failure as Error).name).toBe("AbortError");
  });

  it("posts a start request as JSON and returns the poll url", async () => {
    server.use(
      http.post(`${BASE}/api/agent/start`, async ({ request }) => {
        const body = (await request.json()) as { dependency: { name: string } };
        expect(request.headers.get("content-type")).toContain("application/json");
        expect(body.dependency.name).toBe("pydantic");
        return HttpResponse.json(
          { thread_id: "t-2", status: "queued", poll_url: "/api/agent/status/t-2" },
          { status: 202 },
        );
      }),
    );

    const response = await startRun({
      repo: { url: null, path: "/srv/repo" },
      dependency: { name: "pydantic", current_version: "1.10.13", target_version: "2.9.2" },
      constraints: { zero_downtime: true, minimize_effort: false, deadline: "2026-09-30", risk_tolerance: "low" },
    });

    expect(response).toEqual({ thread_id: "t-2", status: "queued", poll_url: "/api/agent/status/t-2" });
  });
});
