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
 * The fallback when a body is not the declared shape — a proxy answering
 * with HTML, say. Without it the client throws a raw JSON parse error, which
 * tells the user nothing about what happened.
 */
function unreadable(_httpStatus: number): ApiError {
  return {
    code: "internal",
    message: "The server returned a response this client could not read.",
    retryable: true,
    node: null,
  };
}

/**
 * `fetch` itself is deliberately outside any try/catch below, so an abort
 * that lands before a response arrives already propagates unwrapped as an
 * `AbortError` — `useRunPolling`'s cleanup depends on that identity to tell
 * a cancelled request from a real failure, not on inspecting a response
 * that, in that case, never arrives. An abort that lands *while a body is
 * being read* surfaces from `.json()` instead, inside these `try` blocks,
 * and must propagate exactly the same way: converting it into an
 * `ApiFailure` would make a request this client cancelled look like one the
 * server refused.
 */
function isAbort(error: unknown): boolean {
  // Not `error instanceof Error`: verified directly that under jsdom (this
  // project's test environment), `new DOMException("x", "AbortError")` --
  // what an aborted `fetch`/`.json()` actually throws -- is `instanceof
  // DOMException` but *not* `instanceof Error`, unlike plain Node. Gating on
  // `instanceof Error` would silently fail this exact check in tests (and
  // possibly some browsers), so this reads only the one property every
  // environment agrees on.
  return typeof error === "object" && error !== null && (error as { name?: unknown }).name === "AbortError";
}

/**
 * How this module obtains a session token, when there is one to obtain.
 *
 * A registered function rather than a direct Clerk import, for two reasons.
 * Clerk's token lives behind `useAuth()`, and a hook cannot be called from a
 * plain module -- importing it here would either break the rule that this is
 * the only module touching the network or drag React into the transport
 * layer. And every existing test drives this client through MSW with no
 * provider mounted; a hard dependency on Clerk would make all of them
 * require an auth context they have no reason to care about.
 *
 * `null` is the local and test posture: no provider, no token, no header --
 * which is what the backend expects when it has no Clerk key either.
 */
type TokenProvider = () => Promise<string | null>;

let tokenProvider: TokenProvider | null = null;

export function setTokenProvider(provider: TokenProvider | null): void {
  tokenProvider = provider;
}

/**
 * The `Authorization` header, or nothing.
 *
 * Failure to obtain a token is deliberately **not** swallowed into a
 * header-less request. An expired session would otherwise turn into an
 * unauthenticated call, and the backend answers those with 401 "Sign in to
 * use this service." -- which is the right message for the wrong reason, and
 * indistinguishable from never having signed in. Letting the rejection
 * propagate keeps the real cause visible.
 */
async function authHeaders(): Promise<Record<string, string>> {
  if (tokenProvider === null) return {};
  const token = await tokenProvider();
  return token === null ? {} : { authorization: `Bearer ${token}` };
}

async function request<T>(path: string, init: RequestInit, signal?: AbortSignal): Promise<T> {
  const auth = await authHeaders();
  const response = await fetch(path, {
    ...init,
    signal,
    // Spread after `init`, so a caller-supplied `authorization` cannot
    // displace the session token. Defensive rather than load-bearing, and
    // measured as such: reversing the two changes nothing today, because the
    // only header any caller sets is `content-type` and object spread merges
    // non-colliding keys identically in either order. An earlier version of
    // this comment claimed `json()` would drop the token; it does not.
    headers: { ...(init.headers as Record<string, string> | undefined), ...auth },
  });

  if (!response.ok) {
    let body: ErrorResponse | null = null;
    try {
      body = (await response.json()) as ErrorResponse;
    } catch (parseError) {
      if (isAbort(parseError)) throw parseError;
      body = null;
    }
    // Not `except: pass` — the caught parse failure becomes a typed error the
    // caller must handle, which is CLAUDE.md rule 20 in its frontend form.
    throw new ApiFailure(response.status, body?.error ?? unreadable(response.status));
  }

  try {
    return (await response.json()) as T;
  } catch (parseError) {
    if (isAbort(parseError)) throw parseError;

    // A 2xx response whose body will not parse is not "unreachable" — the
    // server answered, with a status in the 200s. Every caller's fallback
    // for a non-`ApiFailure` reads "The backend is unreachable.", which
    // would otherwise report a server that *did* answer as one that never
    // did. `unreadable()` already exists for exactly this shape of failure
    // (used on the `!response.ok` path above); this was the parse failure
    // it did not used to cover.
    throw new ApiFailure(response.status, unreadable(response.status));
  }
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
