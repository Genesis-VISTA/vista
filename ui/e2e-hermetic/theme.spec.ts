import { expect, test, type Page } from "@playwright/test";
import { installStub } from "./stub";

/**
 * Appearance against the built app (ui-theme spec). The unit tests cover the
 * hook and the inline script in jsdom; this proves the three of them, the
 * stylesheet and the settings modal agree in a real browser, which is where a
 * flash or a stuck theme would actually show.
 *
 * `--bg` is the page ground, so it names the theme in effect.
 */
const LIGHT_BG = "#f4f4f2";
const DARK_BG = "#0a1524";

async function bg(page: Page): Promise<string> {
  return page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue("--bg").trim());
}

async function attr(page: Page): Promise<string | null> {
  return page.evaluate(() => document.documentElement.getAttribute("data-theme"));
}

/** Set before React runs, as a researcher's earlier visit would have left it. */
async function storeChoice(page: Page, choice: "light" | "dark") {
  await page.addInitScript((value) => {
    // Only on the first document: a later reload must see what the app wrote.
    if (!sessionStorage.getItem("theme-seeded")) {
      localStorage.setItem("vista.theme", value);
      sessionStorage.setItem("theme-seeded", "1");
    }
  }, choice);
}

async function openAppearance(page: Page) {
  await page.getByRole("button", { name: "Open settings" }).click();
  return page.getByRole("dialog", { name: "User settings" }).getByRole("radiogroup", { name: "Appearance" });
}

test.beforeEach(async ({ page }) => {
  await installStub(page);
});

test("follows a dark OS with nothing stored", async ({ page }) => {
  await page.emulateMedia({ colorScheme: "dark" });
  for (const route of ["/", "/projects", "/skill-hub"]) {
    await page.goto(route);
    expect(await attr(page)).toBeNull();
    expect(await bg(page)).toBe(DARK_BG);
  }
});

test("switches with the OS while open, without a reload", async ({ page }) => {
  await page.emulateMedia({ colorScheme: "light" });
  await page.goto("/projects");
  expect(await bg(page)).toBe(LIGHT_BG);
  await page.evaluate(() => ((window as unknown as { __sameDocument: boolean }).__sameDocument = true));

  await page.emulateMedia({ colorScheme: "dark" });

  expect(await bg(page)).toBe(DARK_BG);
  expect(await page.evaluate(() => (window as unknown as { __sameDocument?: boolean }).__sameDocument)).toBe(true);
});

test("a stored Light overrides a dark OS", async ({ page }) => {
  await storeChoice(page, "light");
  await page.emulateMedia({ colorScheme: "dark" });
  await page.goto("/projects");
  expect(await attr(page)).toBe("light");
  expect(await bg(page)).toBe(LIGHT_BG);
});

test("choosing Dark in Settings applies at once and survives a reload", async ({ page }) => {
  await page.emulateMedia({ colorScheme: "light" });
  await page.goto("/projects");
  await page.evaluate(() => ((window as unknown as { __sameDocument: boolean }).__sameDocument = true));

  const appearance = await openAppearance(page);
  await expect(appearance.getByRole("radio", { name: "System" })).toBeChecked();
  await appearance.getByRole("radio", { name: "Dark" }).click();

  await expect(appearance.getByRole("radio", { name: "Dark" })).toBeChecked();
  expect(await bg(page)).toBe(DARK_BG);
  expect(await page.evaluate(() => (window as unknown as { __sameDocument?: boolean }).__sameDocument)).toBe(true);

  await page.reload();
  expect(await attr(page)).toBe("dark");
  expect(await bg(page)).toBe(DARK_BG);
});

test("choosing System hands the theme back to the OS", async ({ page }) => {
  await storeChoice(page, "dark");
  await page.emulateMedia({ colorScheme: "light" });
  await page.goto("/projects");
  expect(await bg(page)).toBe(DARK_BG);

  const appearance = await openAppearance(page);
  await appearance.getByRole("radio", { name: "System" }).click();

  expect(await attr(page)).toBeNull();
  expect(await bg(page)).toBe(LIGHT_BG);
});
