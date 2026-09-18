import { defineConfig } from "@playwright/test";

/**
 * Screenshot capture, deliberately separate from playwright.hermetic.config.ts
 * so CI never picks it up. Same server shape as the hermetic lane: a built
 * Next app and nothing else behind it.
 */
const PORT = Number(process.env.SHOT_PORT ?? 3200);

export default defineConfig({
  testDir: "./e2e-shots",
  timeout: 120_000,
  retries: 0,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 2,
  },
  webServer: {
    command: `npm run build && npx next start --port ${PORT}`,
    url: `http://127.0.0.1:${PORT}`,
    timeout: 300_000,
    reuseExistingServer: false,
  },
});
