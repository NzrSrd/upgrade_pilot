/**
 * Whether this build is gated, and by which Clerk instance.
 *
 * Read once at module load rather than per render: `import.meta.env` is
 * substituted at build time, so its value cannot change while the app runs,
 * and treating it as reactive would suggest otherwise.
 */

export const CLERK_PUBLISHABLE_KEY: string | undefined = import.meta.env
  .VITE_CLERK_PUBLISHABLE_KEY;

/**
 * Whether a Clerk instance is configured for this build.
 *
 * The frontend mirror of the backend's `auth_required`, and the two are meant
 * to agree: the backend gates when `CLERK_SECRET_KEY` is present, this app
 * gates when the publishable key is. They can still be set independently --
 * they live in different places and are deployed separately -- so `App`
 * cross-checks them against `/api/health` rather than assuming.
 *
 * Trimmed, because an exported-but-empty variable is this project's recurring
 * defect and Vite substitutes `""` for one just as happily as a key. An
 * untrimmed check would hand `ClerkProvider` a blank key, which fails as a
 * blank sign-in component rather than as a configuration error.
 */
export const isAuthConfigured: boolean =
  typeof CLERK_PUBLISHABLE_KEY === "string" && CLERK_PUBLISHABLE_KEY.trim().length > 0;
