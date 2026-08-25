/**
 * The only module in the frontend that calls `fetch`.
 *
 * Components render and hooks schedule; neither talks to the network. That is
 * the frontend's form of CLAUDE.md rule 16, and it is what makes the whole
 * transport testable from one place.
 */

import type {
  ApiError,
  ErrorResponse,
  HealthResponse,
  ResumeRequest,
  RunSnapshot,
  StartResponse,
  StartRunRequest,
} from "./types";

/**
 * A refused request, carrying the server's own error body.
 *
 * The backend declares `ErrorResponse` on every route precisely so the client
 * never has to guess (`api/routes/agent.py:50`). Keeping `httpStatus` beside
 * the body matters for one caller in particular: the decision panel branches
 * on 409, which is the only real guarantee against a duplicate resume.
 */
export class ApiFailure extends Error {
  readonly httpStatus: number;
  readonly error: ApiError;

  // Not a constructor parameter property: `erasableSyntaxOnly` forbids that
  // shorthand because it is TS-specific non-erasable syntax. The fields
  // below carry the exact same public shape.
  constructor(httpStatus: number, error: ApiError) {
    super(error.message);
    this.name = "ApiFailure";
    this.httpStatus = httpStatus;
    this.error = error;
  }
}

/**
 * The fallback when a non-2xx body is not the declared shape — a proxy
 * answering with HTML, say. Without it the client throws a JSON parse error,
 * which tells the user nothing about what happened.
 */
function unreadable(_httpStatus: number): ApiError {
  return {
    code: "internal",
    message: "The server returned a response this client could not read.",
    retryable: true,
    node: null,
  };
}

async function request<T>(path: string, init: RequestInit, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, { ...init, signal });

  if (!response.ok) {
    let body: ErrorResponse | null = null;
    try {
      body = (await response.json()) as ErrorResponse;
    } catch {
      body = null;
    }
    // Not `except: pass` — the caught parse failure becomes a typed error the
    // caller must handle, which is CLAUDE.md rule 20 in its frontend form.
    throw new ApiFailure(response.status, body?.error ?? unreadable(response.status));
  }

  return (await response.json()) as T;
}

const json = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "content-type": "application/json" },
  body: JSON.stringify(body),
});

export function getStatus(threadId: string, signal?: AbortSignal): Promise<RunSnapshot> {
  // Encoded, not concatenated. The id is a uuid today; a path built by
  // concatenation is a request-forgery shape waiting for the day it is not.
  return request<RunSnapshot>(`/api/agent/status/${encodeURIComponent(threadId)}`, {}, signal);
}

export function startRun(body: StartRunRequest, signal?: AbortSignal): Promise<StartResponse> {
  return request<StartResponse>("/api/agent/start", json(body), signal);
}

export function resumeRun(body: ResumeRequest, signal?: AbortSignal): Promise<StartResponse> {
  return request<StartResponse>("/api/agent/resume", json(body), signal);
}

export function getHealth(signal?: AbortSignal): Promise<HealthResponse> {
  return request<HealthResponse>("/api/health", {}, signal);
}
