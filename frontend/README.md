# UpgradePilot frontend

React + TypeScript + Vite, with Tailwind v4 and Lucide icons. Currently a
single view (`src/App.tsx`) that reads `GET /api/health` and reports whether
the backend is healthy — the full journey arrives in Phase 10, see
`../PLANNING.md`.

This file previously held the stock Vite template, which documented Oxlint
configuration (`.oxlintrc.json`, `oxlint-tsgolint`) that does not exist in
this project. There is no linter wired up on the frontend yet.

## Prerequisites

Node with `npm`. Verified against the versions below; nothing else is needed.

```bash
node --version   # v24.13.1
npm --version    # 11.8.0
```

## Setup

```bash
npm install
```

## Commands

Every command here has been run in this directory.

```bash
npm install       # install dependencies
npm run dev       # dev server on http://localhost:5173
npm run build     # tsc -b, then a production build into dist/
npm run preview   # serve the built dist/ on http://localhost:4173
npx tsc -b        # typecheck only, no build output
```

`npm run dev` needs the backend running on **port 8000** to show anything but
"Backend unreachable": `vite.config.ts` proxies `/api` to
`http://localhost:8000`. From the repository root:

```bash
cd backend && ./.venv/bin/python -m uvicorn upgradepilot.api.app:app --port 8000
```

With both running, `curl http://localhost:5173/api/health` returns the
backend's health JSON through the proxy.

## What is not set up yet

- **No tests.** `vitest` is in `devDependencies`, but there is no `test`
  script and no test files. Vitest and React Testing Library over the polling
  hook and the Human Review panel are Phase 10 work.
- **No linter.** See the note above about the deleted Oxlint config.

## Layout

| Path | Contents |
|---|---|
| `src/main.tsx` | React entry point |
| `src/App.tsx` | the health view |
| `src/index.css` | Tailwind import plus the semantic color tokens (`risk-high`, `risk-medium`, `risk-low`, `pending-input`, `surface`) |
| `vite.config.ts` | React and Tailwind plugins; the `/api` dev proxy |
