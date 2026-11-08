import { test } from "@playwright/test";
import { KNOWLEDGE_BASE } from "../e2e-hermetic/fixtures";
import { installStub } from "../e2e-hermetic/stub";

/**
 * Not a test. One knowledge base holding a PDF and a CSV, to review how the
 * explorer shows each (kb-ingest-other-formats group 5).
 *
 *   SHOT_DIR=/tmp/kb npx playwright test -c playwright.shots.config.ts e2e-shots/kb-formats.spec.ts
 */
const OUT = process.env.SHOT_DIR ?? "shots/kb";

const PUBLICATIONS = [
  {
    filename: "flibe-density-review.pdf",
    title: "Density of FLiBe: a review",
    authors: ["A. Author", "B. Writer"],
    journal: "J. Nucl. Mater.",
    year: "2019",
    size: 482113,
    added_at: "2026-01-01T00:00:00+00:00",
    index_status: "indexed",
    kind: "pdf",
    has_pdf: true,
  },
  {
    filename: "flibe-density-measurements.csv",
    title: "flibe-density-measurements.csv",
    authors: [],
    size: 20480,
    added_at: "2026-01-01T00:00:00+00:00",
    index_status: "indexed",
    kind: "csv",
    row_count: 1240,
    abstract: "Measured FLiBe density (g/cm3) against temperature (K), 600-1100 K.",
    has_pdf: true,
  },
];

test("capture the KB explorer with a PDF and a CSV", async ({ page }) => {
  const kb = { ...KNOWLEDGE_BASE, publication_count: 2, publications: PUBLICATIONS };
  await installStub(page, {
    "GET /api/knowledge-bases/molten-salt-papers": kb,
    "GET /api/knowledge-bases/molten-salt-papers/publications": PUBLICATIONS,
  });
  await page.addInitScript(() => {
    window.localStorage.setItem("vista.activeProject.v1", "molten-salt");
    window.localStorage.setItem("vista.navRail.collapsed.v1", "false");
  });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/knowledge-bases/project");
  await page.waitForLoadState("networkidle").catch(() => undefined);
  await page.waitForTimeout(600);
  await page.screenshot({ path: `${OUT}/kb-list.png` });
  await page.getByText("flibe-density-measurements.csv").first().click().catch(() => undefined);
  await page.waitForTimeout(300);
  await page.screenshot({ path: `${OUT}/kb-expanded.png` });
});
