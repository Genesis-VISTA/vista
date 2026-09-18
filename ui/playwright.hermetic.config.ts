import { defineConfig, devices } from "@playwright/test";

/**
 * Required MR job. Hermetic: this config starts the Next server and nothing
 * else — no Python backend, no MCP server, no model, no database. Every
 * `/api/**` call is answered inside the page (see `e2e-hermetic/stub.ts`).
 *
 * Kept separate from `playwright.config.ts`, which owns the seeded
 * validation-lane smoke in `e2e/` and stays schedule-or-manual.
 */
const PORT = Number(process.env.HERMETIC_PORT ?? 3100);

export default defineConfig({
  testDir: "./e2e-hermetic",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: 0,
  reporter: process.env.CI ? [["list"], ["junit", { outputFile: "playwright-report.xml" }]] : "list",
  use: {
    ...devices["Desktop Chrome"],
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: "on-first-retry",
  },
  timeout: 60_000,
  expect: { timeout: 10_000 },
  webServer: {
    // Production build, not dev: the MR job should fail on a real regression,
    // not on a first-request compile timing out.
    command: `npm run build && npx next start --port ${PORT}`,
    url: `http://127.0.0.1:${PORT}`,
    reuseExistingServer: !process.env.CI,
    timeout: 180_000,
    // No VISTA_BACKEND_URL. If a request ever reaches a route handler it will
    // fail against 127.0.0.1:8001 rather than quietly finding a live backend.
    env: { NODE_ENV: "production" },
  },
});
