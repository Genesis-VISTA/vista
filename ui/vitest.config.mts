import { defineConfig } from "vitest/config";

/**
 * Hermetic component test lane. Runs in jsdom, never touches a backend, and
 * is a required MR job — see openspec/specs/testing-ci.
 *
 * Playwright owns `e2e/` and `*.spec.ts`; Vitest owns `tests/` and
 * `*.test.ts(x)`. Keeping the globs disjoint stops each runner collecting the
 * other's files.
 *
 * `.mts` rather than `.ts` so Vite's native config loader reads it as ESM.
 */
export default defineConfig({
  // Reads the `@/*` mapping straight out of tsconfig.json. Vite resolves this
  // natively now, so no vite-tsconfig-paths plugin is needed.
  resolve: { tsconfigPaths: true },
  test: {
    environment: "jsdom",
    include: ["tests/**/*.test.{ts,tsx}"],
    exclude: ["node_modules/**", ".next/**", "e2e/**"],
    setupFiles: ["./tests/setup.ts"],
    restoreMocks: true,
    css: true,
  },
});
