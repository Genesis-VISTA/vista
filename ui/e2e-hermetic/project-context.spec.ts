import { expect, test } from "@playwright/test";
import { installStub, type Stub } from "./stub";

/**
 * A project is context you are inside, not a mode you enter and leave.
 *
 * Before this there was exactly one way to change project in the whole app,
 * and it lived on a page you had to navigate to first. These tests pin the
 * three things that changed: you can switch from anywhere, switching does not
 * move you, and a page that needs a project says so instead of sitting empty.
 */
async function openMoltenSalt(page: import("@playwright/test").Page) {
  await page.goto("/projects");
  await page
    .locator(".project-card")
    .filter({ hasText: "molten-salt" })
    .getByRole("button", { name: "Open" })
    .click();
}

test.describe("project context", () => {
  let stub: Stub | null = null;

  test.beforeEach(async ({ page }) => {
    stub = await installStub(page);
  });

  test.afterEach(async () => {
    if (stub) {
      expect(await stub.unstubbed(), "page asked for a route with no fixture").toEqual([]);
    }
    stub = null;
  });

  // The point of moving the switcher into the header: you changed which
  // project, not what you were doing.
  test("switching project keeps you on the page you were on", async ({ page }) => {
    await openMoltenSalt(page);
    await page.goto("/datasets");

    const switcher = page.locator(".project-switcher-button");
    await expect(switcher).toHaveText("molten-salt");

    await switcher.click();
    await page.getByRole("option", { name: "alloy-design" }).click();

    await expect(switcher).toHaveText("alloy-design");
    await expect(page).toHaveURL(/\/datasets$/);
    await expect(page.getByRole("heading", { level: 1, name: "Datasets" })).toBeVisible();
  });

  test("the switcher closes on Escape and marks the active project", async ({ page }) => {
    await openMoltenSalt(page);
    await page.goto("/skills");

    await page.locator(".project-switcher-button").click();
    const menu = page.getByRole("listbox", { name: "Projects" });
    await expect(menu).toBeVisible();
    await expect(menu.getByRole("option", { name: "molten-salt" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    await expect(menu.getByRole("option", { name: "alloy-design" })).toHaveAttribute(
      "aria-selected",
      "false",
    );

    await page.keyboard.press("Escape");
    await expect(menu).toBeHidden();
  });

  // Chat is the only route with nothing to show without a project.
  test("chat sends you to pick a project, then back to chat", async ({ page }) => {
    await page.goto("/");

    await expect(page).toHaveURL(/\/projects$/);
    await page
      .locator(".project-card")
      .filter({ hasText: "molten-salt" })
      .getByRole("button", { name: "Open" })
      .click();

    await expect(page).toHaveURL(/\/$/);
    await expect(page.getByPlaceholder(/Ask a question/)).toBeVisible();
  });

  test("a project that no longer exists does not strand the chat page", async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.setItem("vista.activeProject.v1", "deleted-project");
    });
    await page.goto("/");

    await expect(page).toHaveURL(/\/projects$/);
    // And the dead pointer is cleared rather than left to redirect forever.
    expect(await page.evaluate(() => localStorage.getItem("vista.activeProject.v1"))).toBeNull();
  });

  // Datasets and skills stay reachable and offer the same way out.
  for (const [route, title] of [
    ["/datasets", "Datasets"],
    ["/skills", "Skills"],
  ] as const) {
  test(`${route} asks for a project in place`, async ({ page }) => {
    await page.goto(route);

    await expect(page).toHaveURL(new RegExp(`${route}$`));
    await expect(page.getByRole("heading", { level: 1, name: title })).toBeVisible();

    const panel = page.locator(".project-required");
    await expect(panel).toBeVisible();
    await expect(panel).toContainText(title);

    await panel.getByRole("button", { name: /molten-salt/ }).click();

    // Choosing keeps you where you asked to be.
    await expect(page).toHaveURL(new RegExp(`${route}$`));
    await expect(panel).toHaveCount(0);
    await expect(page.locator(".project-switcher-button")).toHaveText("molten-salt");
  });
  }

  // "Usable from anywhere" has to include the case where you have no project
  // yet, otherwise the only way to get one is still a page you navigate to.
  test("the switcher can pick a project when none is selected", async ({ page }) => {
    await page.goto("/datasets");

    const switcher = page.locator(".project-switcher-button");
    await expect(switcher).toHaveText("No project");

    await switcher.click();
    await page.getByRole("option", { name: "molten-salt" }).click();

    await expect(switcher).toHaveText("molten-salt");
    await expect(page).toHaveURL(/\/datasets$/);
    await expect(page.locator(".project-required")).toHaveCount(0);
  });

  test("the rail opens expanded and remembers being collapsed", async ({ page }) => {
    await page.goto("/projects");

    const rail = page.locator(".nav-rail");
    await expect(rail).toHaveClass(/expanded/);
    await expect(page.getByText("Opened Project")).toBeVisible();

    await page.getByRole("button", { name: "Collapse navigation" }).click();
    await expect(rail).toHaveClass(/collapsed/);

    await page.reload();
    await expect(rail).toHaveClass(/collapsed/);
  });

  // Every one of these used to be inert and claim, on hover, to be "coming
  // soon" — to a user whose only problem was not having picked a project yet.
  test("project navigation works without a project selected", async ({ page }) => {
    await page.goto("/projects");

    for (const [label, url] of [
      ["Skills", /\/skills$/],
      ["Datasets", /\/datasets$/],
    ] as const) {
      await page.locator(".nav-rail").getByRole("link", { name: label, exact: true }).click();
      await expect(page).toHaveURL(url);
      await expect(page.locator(".project-required")).toBeVisible();
      await page.goto("/projects");
    }

    await expect(page.locator(".nav-rail [aria-disabled='true']")).toHaveCount(0);
    await expect(page.locator(".nav-rail [title*='coming soon']")).toHaveCount(0);
  });
});

test.describe("projects page", () => {
  test("explains what a project is when there are none", async ({ page }) => {
    await installStub(page, { "GET /api/projects": [] });
    await page.goto("/projects");

    const empty = page.locator(".projects-empty");
    await expect(empty).toBeVisible();
    await expect(empty).toContainText("system prompt");
    await expect(page.locator(".projects-grid")).toHaveCount(0);

    await empty.getByRole("button", { name: "Create a project" }).click();
    await expect(page.getByRole("dialog")).toBeVisible();
  });

  // One request per count per card. Fine at this many projects, bad at fifty —
  // see the comment on useProjectStats.
  test("each card carries its own counts", async ({ page }) => {
    const stub = await installStub(page);
    await page.goto("/projects");

    const card = page.locator(".project-card").filter({ hasText: "molten-salt" });
    await expect(card.locator(".project-card-stats")).toContainText("1 conversation");
    await expect(card.locator(".project-card-stats")).toContainText("0 datasets");

    const requests = await stub.requests();
    const perProject = requests.filter((r) => r.includes("project_name=molten-salt"));
    expect(perProject.some((r) => r.startsWith("GET /api/chat/sessions"))).toBe(true);
    expect(perProject.some((r) => r.startsWith("GET /api/files/uploads"))).toBe(true);
    expect(perProject.some((r) => r.startsWith("GET /api/files/outputs"))).toBe(true);
  });
});
