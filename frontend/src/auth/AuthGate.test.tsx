import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { setTokenProvider } from "../api/client";
import { AuthGate } from "./AuthGate";

/**
 * What the gate must do, beyond "hide the app".
 *
 * Two behaviours here are easy to get wrong in ways that no error reveals.
 * `isLoaded` has to be consulted before `isSignedIn`, because Clerk reports
 * the latter as `undefined` until the session resolves -- a gate that read it
 * first would flash the sign-in prompt at an already-signed-in user on every
 * reload, and nothing would fail. And the token provider has to be *cleared*
 * on sign-out, not merely replaced on sign-in, or a signed-out session keeps
 * making authenticated requests until its token expires.
 */

const useAuth = vi.hoisted(() => vi.fn());
const openSignIn = vi.hoisted(() => vi.fn());

vi.mock("@clerk/react", () => ({
  useAuth: () => useAuth(),
  useClerk: () => ({ openSignIn }),
}));

vi.mock("../api/client", () => ({ setTokenProvider: vi.fn() }));

afterEach(() => {
  vi.clearAllMocks();
});

function anApp() {
  return <div>the application</div>;
}

it("shows neither the app nor a sign-in prompt until Clerk has loaded", () => {
  // The discriminating case: `isSignedIn` is `undefined` here, exactly as
  // Clerk reports it before the session resolves. A gate that branched on it
  // first would render the prompt and look correct in every other test.
  useAuth.mockReturnValue({ isLoaded: false, isSignedIn: undefined, getToken: vi.fn() });

  render(<AuthGate>{anApp()}</AuthGate>);

  expect(screen.queryByText("the application")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /sign in/i })).not.toBeInTheDocument();
  expect(screen.getByText(/checking your session/i)).toBeInTheDocument();
});

it("hides the application from a signed-out visitor", () => {
  useAuth.mockReturnValue({ isLoaded: true, isSignedIn: false, getToken: vi.fn() });

  render(<AuthGate>{anApp()}</AuthGate>);

  expect(screen.queryByText("the application")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: /sign in/i })).toBeInTheDocument();
});

it("renders the application for a signed-in user", () => {
  useAuth.mockReturnValue({ isLoaded: true, isSignedIn: true, getToken: vi.fn() });

  render(<AuthGate>{anApp()}</AuthGate>);

  expect(screen.getByText("the application")).toBeInTheDocument();
});

describe("the token provider", () => {
  it("is registered once a user is signed in", () => {
    const getToken = vi.fn().mockResolvedValue("a-session-token");
    useAuth.mockReturnValue({ isLoaded: true, isSignedIn: true, getToken });

    render(<AuthGate>{anApp()}</AuthGate>);

    const registered = vi.mocked(setTokenProvider).mock.calls.at(-1)?.[0];
    expect(registered).toBeTypeOf("function");
  });

  it("is cleared for a signed-out visitor rather than left stale", () => {
    // The failure this guards: a provider surviving sign-out would keep
    // attaching the previous token, so requests stay authenticated after the
    // user has signed out, until the token happens to expire.
    useAuth.mockReturnValue({ isLoaded: true, isSignedIn: false, getToken: vi.fn() });

    render(<AuthGate>{anApp()}</AuthGate>);

    expect(setTokenProvider).toHaveBeenCalledWith(null);
  });
});
