import type { Page } from "@playwright/test";
import { test } from "@playwright/test";
import { installStub } from "../e2e-hermetic/stub";

/**
 * Both sides of a pair have to start from the same state, and the controls
 * that set it changed across this change. Writing what the app reads is the
 * one way to do that identically on either side; both keys are the same
 * before and after, only their defaults differ.
 */
async function withProject(page: Page, overrides: Record<string, unknown>) {
  await installStub(page, overrides);
  await page.addInitScript(() => {
    window.localStorage.setItem("vista.activeProject.v1", "molten-salt");
    // The rail's default flipped in this change: it used to open collapsed and
    // now opens expanded. Left to the defaults, every pair would differ by an
    // icon-only rail versus a labelled one, which swamps the thing each pair
    // is actually showing. Pinning it makes the rail a matched precondition;
    // that the default changed is worth saying in prose, not in seven images.
    window.localStorage.setItem("vista.navRail.collapsed.v1", "false");
  });
  await page.setViewportSize({ width: 1440, height: 900 });
}

/**
 * Not a test. This drives the app through the hermetic stub purely to capture
 * the before/after pairs the change's review needs.
 *
 * It reuses the browser suite's fixtures so both sides of a pair show exactly
 * the same data: a screenshot pair is only evidence if the only thing that
 * differs between the two images is the thing under review. Running it against
 * a real stack would put a different conversation, a different file list and a
 * different set of timestamps on each side.
 *
 * Kept out of the hermetic config so CI never runs it.
 */

const LABEL = process.env.SHOT_LABEL ?? "after";
const OUT = process.env.SHOT_DIR ?? `shots/${LABEL}`;

/** Datasets and outputs, which the browser suite leaves empty. */
const OVERRIDES = {
  "GET /api/files/uploads": [
    { name: "flibe-density.csv", size: 20480, modifiedAt: "2026-01-01T00:00:00+00:00", source: "upload" },
    { name: "nacl-kcl-eutectic.csv", size: 8192, modifiedAt: "2026-01-01T00:00:00+00:00", source: "upload" },
    { name: "mstdb-export.json", size: 131072, modifiedAt: "2026-01-01T00:00:00+00:00", source: "upload" },
  ],
  "GET /api/files/outputs": [
    { name: "density-vs-temperature.png", size: 61440, modifiedAt: "2026-01-01T00:00:00+00:00", source: "generated" },
    { name: "viscosity-fit.png", size: 45056, modifiedAt: "2026-01-01T00:00:00+00:00", source: "generated" },
  ],
};

const ROUTES = [
  ["chat", "/"],
  ["projects", "/projects"],
  ["skills", "/skills"],
  ["datasets", "/datasets"],
  ["skill-hub", "/skill-hub"],
  ["knowledge-bases", "/knowledge-bases/project"],
] as const;

test("capture every route", async ({ page }) => {
  await withProject(page, OVERRIDES);

  for (const [name, route] of ROUTES) {
    await page.goto(route);
    if (route === "/") {
      // The chat route opens on the conversation list; the thread is the part
      // worth comparing.
      await page.waitForTimeout(400);
      await page
        .locator(".conversation-list-open, .chat-list button, .conversation-list-item")
        .first()
        .click()
        .catch(() => undefined);
    }
    // Let the route's own fetches settle rather than racing a spinner.
    await page.waitForLoadState("networkidle").catch(() => undefined);
    await page.waitForTimeout(600);
    await page.screenshot({ path: `${OUT}/${name}.png`, fullPage: false });
  }
});

test("capture the salt-button flow", async ({ page }) => {
  await withProject(page, OVERRIDES);

  await page.goto("/");
  await page.waitForTimeout(400);
  await page
    .locator(".conversation-list-open, .chat-list button, .conversation-list-item")
    .first()
    .click()
    .catch(() => undefined);
  await page.waitForTimeout(800);

  // Before this change the chat column carried "Analyze salt…" and "Predict
  // salt…" chips that opened a modal and built a shell command out of what you
  // typed. Task 7.7 removed that path, so after the change the control does
  // not exist and the same corner of the screen offers opener chips that only
  // prefill the composer. Shoot whichever the build has: the pair is the
  // point, not the selector.
  const saltChip = page.getByRole("button", { name: /Analyze salt/i });
  if (await saltChip.count()) {
    await saltChip.first().click();
  } else {
    await page
      .locator(".chat-opener-chips button")
      .first()
      .click()
      .catch(() => undefined);
  }
  await page.waitForTimeout(600);

  await page.screenshot({ path: `${OUT}/salt-flow.png`, fullPage: false });
});
