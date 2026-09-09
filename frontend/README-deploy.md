# Deploying the frontend

`vercel.json` is JSON and cannot carry comments, so the reasoning lives here.
See `docs/adr/ADR-002-deployment.md` D4 for the decisions themselves.

## The `/api` rewrite

```json
{ "source": "/api/:path*", "destination": "https://<cloud-run-url>/api/:path*" }
```

**`REPLACE_WITH_CLOUD_RUN_URL` is a deliberate placeholder, and a deliberately
invalid host.** No Cloud Run service exists yet (Phase 13.4 creates it), and a
plausible-looking wrong URL would proxy silently to nothing. This one fails DNS
resolution, so a premature deploy breaks loudly instead of serving an app whose
every request quietly disappears.

Two things this rewrite is doing:

- **It keeps the browser same-origin**, so `src/api/client.ts` can go on using
  relative `/api/...` paths and CORS never enters the picture. That is why the
  client needs no base-URL setting.
- **It is a plain rewrite, not Routing Middleware** — a simplification Clerk
  bought. The earlier design in ADR-002 injected a shared secret server-side,
  which a rewrite cannot do: rewrites can *match* on headers but not *set*
  them. Clerk's session token travels from the browser, so nothing needs
  injecting and the middleware disappeared.

## The SPA fallback

```json
{ "source": "/((?!api/).*)", "destination": "/index.html" }
```

Ordered last and explicitly excluding `api/`, because rewrites are evaluated in
order and a bare `/(.*)` fallback would shadow the API rewrite above it —
turning every backend call into a request for `index.html`, which parses as
HTML where JSON was expected. `client.ts` has a specific error for that
(`unreadable()`), so the symptom would be "the server returned a response this
client could not read" on every single request.

## Environment variables to set in Vercel

| Variable | Value | Notes |
|---|---|---|
| `VITE_CLERK_PUBLISHABLE_KEY` | `pk_test_…` / `pk_live_…` | Public; ships in the bundle. `VITE_` prefix is required or Vite will not expose it. |

The Clerk **secret** key does not belong here. It goes to Cloud Run via Secret
Manager, and a secret in this directory is a hazard even when gitignored.

Note that `pk_test_` is a Clerk *development* instance. Production is a
separate instance with a separate key and needs a real domain — so the value
here differs between a preview deployment and production.
