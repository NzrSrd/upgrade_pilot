import { ClerkProvider } from "@clerk/react";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { AuthGate } from "./auth/AuthGate";
import { CLERK_PUBLISHABLE_KEY, isAuthConfigured } from "./auth/config";
import "./index.css";

/**
 * The gate is mounted only when a Clerk instance is configured.
 *
 * Not a convenience. `ClerkProvider` requires a publishable key and renders a
 * blank sign-in surface without one, so a build with no key would show an
 * unusable screen rather than an obvious misconfiguration. And local
 * development deliberately has no Clerk instance -- the backend runs open
 * when `CLERK_SECRET_KEY` is absent, and the frontend has to match, or the
 * two disagree about whether anyone needs to sign in.
 *
 * `App` cross-checks this against `/api/health`'s `auth_required`, because
 * the two keys live in different places and are deployed separately: a gated
 * backend behind an ungated frontend is a real configuration and would
 * otherwise present as every request failing with 401.
 */
const tree = isAuthConfigured ? (
  <ClerkProvider publishableKey={CLERK_PUBLISHABLE_KEY!}>
    <AuthGate>
      <App />
    </AuthGate>
  </ClerkProvider>
) : (
  <App />
);

createRoot(document.getElementById("root")!).render(<StrictMode>{tree}</StrictMode>);
