import { expect, test, type Page } from "@playwright/test";
import { ROUTES } from "./fixtures";
import { installStub, type Stub } from "./stub";

/**
 * The Hypothesis Lab keeps the forum's status current while it is open
 * (openspec change global-repo-tokens, task 4.3).
 *
 * A debate's first push is refused seconds after the page has loaded, so a
 * status read only on load never showed why posts stay "not yet published". The
 * page re-reads it on an interval; the fake clock skips that interval instead of
 * waiting it out.
 */
const STATUS = {
  enabled: true,
  shared: true,
  remote: "https://github.com/org/forum.git",
  git_ok: true,
  git_reason: null,
  unpublished: 0,
  publish_error: null,
};

const REFUSED = {
  ...STATUS,
  unpublished: 2,
  publish_error: {
    reason: "refused",
    credential: "settings-github",
    message: "remote: Write access to repository not granted.",
    at: "2026-10-15T16:00:00+00:00",
  },
};

const NOTICE = "Your GitHub token from Settings was refused by the forum.";
const POLL_MS = 16_000;

async function openLab(page: Page) {
  await page.goto("/projects");
  await page
    .locator(".project-card")
    .filter({ hasText: "molten-salt" })
    .getByRole("button", { name: "Open" })
    .click();
  await page
    .getByRole("complementary", { name: "Primary navigation" })
    .getByRole("link", { name: "Hypothesis Lab" })
    .click();
  await expect(page.getByRole("heading", { name: "Hypothesis Lab" })).toBeVisible();
}

test.describe("hypothesis lab publish notice", () => {
  let stub: Stub | null = null;

  test.afterEach(async () => {
    if (stub) {
      expect(await stub.unstubbed(), "page asked for a route with no fixture").toEqual([]);
    }
    stub = null;
  });

  test("a publish refused after the page loaded is explained without a reload", async ({
    page,
  }) => {
    await page.clock.install();
    stub = await installStub(page, {
      ...ROUTES,
      "GET /api/debates": [],
      "GET /api/forum/status": STATUS,
    });
    await openLab(page);
    await expect(page.getByText(NOTICE)).toHaveCount(0);

    await stub.setRoute("GET /api/forum/status", REFUSED);
    await page.clock.runFor(POLL_MS);
    await expect(page.getByText(NOTICE)).toBeVisible();
  });

  test("the notice goes once a push succeeds", async ({ page }) => {
    await page.clock.install();
    stub = await installStub(page, {
      ...ROUTES,
      "GET /api/debates": [],
      "GET /api/forum/status": REFUSED,
    });
    await openLab(page);
    await expect(page.getByText(NOTICE)).toBeVisible();

    await stub.setRoute("GET /api/forum/status", STATUS);
    await page.clock.runFor(POLL_MS);
    await expect(page.getByText(NOTICE)).toHaveCount(0);
  });
});
