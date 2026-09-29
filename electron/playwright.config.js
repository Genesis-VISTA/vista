// @ts-check
// Window behaviour against a fixture server (design T2). Needs a display, so it
// runs in the validation lane on macOS, not in PR CI.
import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: 'test',
  testMatch: '*.e2e.js',
  timeout: 60_000,
  workers: 1,
  reporter: 'list',
});
