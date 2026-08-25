/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://localhost:8000", changeOrigin: true },
    },
  },
  test: {
    environment: "jsdom",
    // MSW handlers in tests are written against `http://localhost` (no
    // port), matching the app's own relative-path fetches. jsdom's own
    // default origin is `http://localhost:3000`; without pinning this, a
    // relative `fetch("/api/...")` resolves to a different origin than the
    // handlers expect, and `onUnhandledRequest: "error"` (deliberately strict
    // — see `src/test/server.ts`) throws on every request instead of
    // matching it.
    environmentOptions: {
      jsdom: {
        url: "http://localhost",
      },
    },
    globals: false,
    setupFiles: ["./src/test/setup.ts"],
    restoreMocks: true,
  },
});
