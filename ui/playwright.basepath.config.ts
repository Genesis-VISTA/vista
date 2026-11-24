import { defineConfig, devices } from "@playwright/test";

/**
 * The hermetic flow once more, with the UI built and served under a base path
 * (`VISTA_BASE_PATH`, openspec change `multi-user-hosted`). Only
 * `e2e-hermetic/basepath.spec.ts` runs. `basePath` is fixed at build time, so
 * this builds its own copy: run it on its own, since it rewrites `.next`.
 */
const PORT = Number(process.env.HERMETIC_PORT ?? 3101);
const BASE = process.env.VISTA_BASE_PATH || "/vista/ui";
process.env.HERMETIC_BASE_PATH = BASE;

export default defineConfig({
  testDir: "./e2e-hermetic",
  testMatch: "basepath.spec.ts",
  forbidOnly: !!process.env.CI,
  retries: 0,
  reporter: "list",
  use: {
    ...devices["Desktop Chrome"],
    baseURL: `http://127.0.0.1:${PORT}`,
  },
  timeout: 60_000,
  expect: { timeout: 10_000 },
  webServer: {
    command: `npm run build && npx next start --port ${PORT}`,
    url: `http://127.0.0.1:${PORT}${BASE}/projects`,
    reuseExistingServer: false,
    timeout: 180_000,
    env: { NODE_ENV: "production", VISTA_BASE_PATH: BASE },
  },
});
