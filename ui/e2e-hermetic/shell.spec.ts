import { expect, test } from "@playwright/test";
import { installStub, type Stub } from "./stub";

/**
 * One shell across every route.
 *
 * The header's own geometry is the thing worth pinning: before this change
 * each page built its own title four different ways across three different
 * paddings, which is most of what made the app feel like separate apps. A
 * screenshot proves that once; this proves it on every merge.
 */
type Metrics = {
  headerHeight: number;
  headerTop: number;
  /** Distance from the rail's edge to the header's left edge. */
  headerOffset: number;
  /** The page body's own side padding. */
  gutter: number;
  /** Where the body starts vertically, under the header. */
  contentTop: number;
  overflows: boolean;
};

async function measure(page: import("@playwright/test").Page): Promise<Metrics> {
  await expect(page.locator(".page-topbar")).toBeVisible();
  return page.evaluate(() => {
    const rail = document.querySelector(".nav-rail")!.getBoundingClientRect();
    const header = document.querySelector(".page-topbar")!.getBoundingClientRect();
    const body = document.querySelector(".app-page-body, .workspace")!;
    const bodyBox = body.getBoundingClientRect();
    return {
      headerHeight: Math.round(header.height),
      headerTop: Math.round(header.top),
      headerOffset: Math.round(header.left - rail.width),
      // The body's own padding, not its first child's offset: a page showing a
      // centred panel would otherwise measure the centring, not the frame.
      gutter: Math.round(parseFloat(getComputedStyle(body).paddingLeft)),
      contentTop: Math.round(bodyBox.top),
      overflows: document.documentElement.scrollWidth > window.innerWidth,
    };
  });
}

/**
 * Direction A splits the body treatment by route type. List routes carry the
 * artboard's 26px 30px gutter around a card grid; master/detail routes (chat,
 * knowledge bases) run edge to edge so their panes meet the window, the way
 * the workspace always has. That is the one measurement allowed to differ.
 * Everything else about the frame stays identical, which is what this pins.
 */
const ROUTES = [
  { path: "/", gutter: 0 },
  { path: "/projects", gutter: 30 },
  { path: "/skills", gutter: 30 },
  { path: "/datasets", gutter: 30 },
  { path: "/skill-hub", gutter: 30 },
  { path: "/knowledge-bases", gutter: 0 },
  { path: "/knowledge-bases/project", gutter: 0 },
] as const;

test.describe("shared shell", () => {
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

  for (const width of [1440, 768]) {
    test(`every route lays out identically at ${width}px`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      await page.goto("/projects");
      await page
        .locator(".project-card")
        .filter({ hasText: "molten-salt" })
        .getByRole("button", { name: "Open" })
        .click();

      const seen: Omit<Metrics, "gutter">[] = [];
      for (const route of ROUTES) {
        await page.goto(route.path);
        const { gutter, ...frame } = await measure(page);
        expect(frame.headerTop, `${route.path}: header is not at the top`).toBe(0);
        expect(frame.headerOffset, `${route.path}: header does not start at the rail edge`).toBe(0);
        expect(frame.overflows, `${route.path}: page scrolls sideways`).toBe(false);
        expect(gutter, `${route.path}: body gutter is not what its route type calls for`).toBe(
          route.gutter
        );
        seen.push(frame);
      }

      // Every route agrees with the first, so adding a route to ROUTES is the
      // only thing needed to bring it under this check.
      for (let i = 1; i < seen.length; i += 1) {
        expect(seen[i], `${ROUTES[i].path} does not match ${ROUTES[0].path}`).toEqual(seen[0]);
      }
    });
  }

  test("each page names itself, under the project it belongs to", async ({ page }) => {
    await page.goto("/projects");
    await page
      .locator(".project-card")
      .filter({ hasText: "molten-salt" })
      .getByRole("button", { name: "Open" })
      .click();

    for (const [route, title] of [
      ["/", "Conversations"],
      ["/skills", "Skills"],
      ["/datasets", "Datasets"],
      ["/skill-hub", "Skill Hub"],
      ["/knowledge-bases/project", "Knowledge Bases"],
    ] as const) {
      await page.goto(route);
      const header = page.locator(".page-topbar");
      await expect(header.getByRole("heading", { level: 1 })).toHaveText(title);
      await expect(header.locator(".project-switcher-button")).toHaveText("molten-salt");
    }
  });

  // Two routes list things that are not the project's, so claiming one in the
  // breadcrumb would say something untrue about what is on screen.
  test("pages that are not project-scoped say so by omission", async ({ page }) => {
    await page.goto("/projects");
    await page
      .locator(".project-card")
      .filter({ hasText: "molten-salt" })
      .getByRole("button", { name: "Open" })
      .click();

    for (const route of ["/projects", "/knowledge-bases"]) {
      await page.goto(route);
      const header = page.locator(".page-topbar");
      await expect(header.getByRole("heading", { level: 1 })).toBeVisible();
      await expect(header.locator(".project-switcher")).toHaveCount(0);
    }
  });

  // The chat column used to carry its own header, repeating the title the top
  // bar already showed and hanging the conversation controls off it. That
  // header is gone, so the controls have to reach the shared bar's actions
  // slot instead — in both of the chat route's states.
  test("chat hands its conversation controls to the shared bar", async ({ page }) => {
    await page.goto("/projects");
    await page
      .locator(".project-card")
      .filter({ hasText: "molten-salt" })
      .getByRole("button", { name: "Open" })
      .click();

    const header = page.locator(".page-topbar");

    // Listing conversations: one control, and no second header under the bar.
    await expect(header.getByRole("heading", { level: 1 })).toHaveText("Conversations");
    await expect(header.getByRole("button", { name: "New conversation" })).toBeVisible();
    // The chat column carries no header of its own. The artifacts column
    // still does — that one is its tab strip, not a repeated page title.
    await expect(page.locator(".workspace > .panel:first-of-type .panel-header")).toHaveCount(0);

    // Inside one: the controls swap, and the title follows.
    await page.locator(".conversation-list-open").first().click();
    await expect(header.getByRole("heading", { level: 1 })).toHaveText("Chat");
    await expect(header.getByRole("button", { name: "Back to conversations" })).toBeVisible();
    await expect(header.getByRole("button", { name: /Save as skill/ })).toBeVisible();
    await expect(header.getByRole("button", { name: "New conversation" })).toHaveCount(0);
    await expect(page.locator(".workspace > .panel:first-of-type .panel-header")).toHaveCount(0);

    await header.getByRole("button", { name: "Back to conversations" }).click();
    await expect(header.getByRole("heading", { level: 1 })).toHaveText("Conversations");
  });

  // The scoped toolbar is gone. Everything it carried has to still be here.
  test("knowledge bases keeps both modes and every control", async ({ page }) => {
    await page.goto("/projects");
    await page
      .locator(".project-card")
      .filter({ hasText: "molten-salt" })
      .getByRole("button", { name: "Open" })
      .click();

    await page.goto("/knowledge-bases/project");
    const header = page.locator(".page-topbar");
    await expect(header.getByRole("button", { name: "Refresh" })).toBeVisible();
    await expect(header.getByRole("button", { name: "+ New" })).toBeVisible();
    await expect(page.locator(".kb-project-toolbar")).toHaveCount(0);

    // The project's knowledge bases are the tabs; "Show all" leaves that scope,
    // so it sits with them.
    const tabs = page.locator(".kb-tabs-row");
    await expect(tabs.getByRole("tab", { name: /Molten Salt Papers/ })).toBeVisible();
    await tabs.getByRole("link", { name: "Show all" }).click();

    await expect(page).toHaveURL(/\/knowledge-bases$/);
    await expect(page.locator(".kb-layout")).toBeVisible();
    await expect(page.getByPlaceholder("Search knowledge bases")).toBeVisible();
    await expect(header.getByRole("button", { name: "Refresh" })).toBeVisible();
    await expect(header.getByRole("button", { name: "+ New" })).toBeVisible();
    await expect(header.getByText("1 total")).toBeVisible();
  });

  // Both directions, from the rail rather than the page. The two views were
  // once one route told apart by a query param, and the App Router no-ops a
  // navigation that only changes the query on the route you are already on —
  // so the global entry did nothing while you were on the scoped view. They
  // are separate paths now, which makes that impossible rather than guarded
  // against; what this still pins is that the rail highlights exactly one of
  // them, since the global href is a prefix of the scoped one.
  test("the rail switches knowledge base scope in both directions", async ({ page }) => {
    await page.goto("/projects");
    await page
      .locator(".project-card")
      .filter({ hasText: "molten-salt" })
      .getByRole("button", { name: "Open" })
      .click();

    const railScoped = page.locator('.nav-rail a[href="/knowledge-bases/project"]');
    const railGlobal = page.locator('.nav-rail a[href="/knowledge-bases"]');

    await page.goto("/knowledge-bases");
    await expect(page.locator(".kb-layout")).toBeVisible();
    await expect(railGlobal).toHaveClass(/active/);

    await railScoped.click();
    await expect(page.locator(".kb-layout-scoped")).toBeVisible();
    await expect(railScoped).toHaveClass(/active/);
    // The global entry's href is a prefix of this one, so a naive prefix
    // match would light both.
    await expect(railGlobal).not.toHaveClass(/active/);

    await railGlobal.click();
    await expect(page.locator(".kb-layout")).toBeVisible();
    await expect(page.locator(".kb-layout-scoped")).toHaveCount(0);
    await expect(railScoped).not.toHaveClass(/active/);

    // A bare URL still means global, and still lights the global entry.
    await page.goto("/knowledge-bases");
    await expect(page.locator(".kb-layout")).toBeVisible();
    await expect(railGlobal).toHaveClass(/active/);
  });

  // The white lockup is only sanctioned on a solid dark ground, and the
  // wordmark must not be split from the emblem or recoloured.
  test("the lockup sits on its dark plate in the rail", async ({ page }) => {
    await page.goto("/skills");

    const plate = page.locator(".nav-rail-lockup");
    await expect(plate).toBeVisible();
    await expect(plate.locator("img")).toHaveAttribute("alt", "Genesis VISTA");
    await expect(plate).toHaveCSS("background-color", "rgb(17, 42, 77)");

    // And it is the only one on screen.
    await expect(page.locator('img[alt="Genesis VISTA"]')).toHaveCount(1);
  });
});
