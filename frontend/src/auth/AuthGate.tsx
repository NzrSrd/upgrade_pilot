import { useAuth, useClerk } from "@clerk/react";
import { useEffect } from "react";
import type { ReactNode } from "react";

import { setTokenProvider } from "../api/client";
import { EmptyState } from "../components/ui";

/**
 * Signed in, or nothing.
 *
 * Rendered only when a Clerk instance is configured -- `main.tsx` decides
 * that, so this component never has to represent the ungated case and cannot
 * accidentally allow it.
 *
 * **`isLoaded` is checked before `isSignedIn`, and the order is load-bearing.**
 * Clerk reports `isSignedIn` as `undefined` until it has resolved the session,
 * so a component that branched on it first would flash the sign-in prompt at
 * an already-signed-in user on every reload.
 */
export function AuthGate({ children }: { children: ReactNode }) {
  const { isLoaded, isSignedIn, getToken } = useAuth();
  const { openSignIn } = useClerk();

  // Registered in an effect rather than during render, because registering is
  // a side effect on a module-level slot: doing it in the render body would
  // run under StrictMode's double invocation and, worse, would run before
  // React had committed the tree that depends on it.
  useEffect(() => {
    if (!isSignedIn) {
      // Cleared, not left stale. A signed-out user whose provider still
      // returned the previous token would keep making authenticated requests
      // after sign-out until the token expired.
      setTokenProvider(null);
      return;
    }
    setTokenProvider(() => getToken());
    return () => setTokenProvider(null);
  }, [isSignedIn, getToken]);

  if (!isLoaded) {
    return (
      <div className="grid min-h-screen place-items-center">
        <EmptyState>Checking your session…</EmptyState>
      </div>
    );
  }

  if (!isSignedIn) {
    return (
      <div className="grid min-h-screen place-items-center px-6">
        <div className="max-w-sm space-y-4 text-center">
          <h1 className="text-lg font-medium text-ink">UpgradePilot</h1>
          <p className="text-sm text-ink-muted">
            This instance is private. Sign in to continue.
          </p>
          <button
            type="button"
            onClick={() => openSignIn()}
            // The primary-action styling `ConfigurationForm`'s submit already
            // uses. `bg-accent` was the first attempt and does not exist --
            // there is no accent token, and a class Tailwind cannot resolve
            // renders as nothing rather than failing the build.
            className="rounded-md border border-edge-strong bg-surface-raised px-4 py-2 text-sm font-medium"
          >
            Sign in
          </button>
        </div>
      </div>
    );
  }

  return <>{children}</>;
}
