import { expect, test, type Page } from "@playwright/test";
import { SESSION_ID } from "./fixtures";
import { installStub, type Stub } from "./stub";

/**
 * The Files page: an Outputs tree and an Uploads tree, each labelled with the
 * path the agent sees it at, a preview beside them, and the selection kept in
 * the URL so another page can link to one file.
 */
async function openMoltenSalt(page: Page) {
  await page.goto("/projects");
  await page
    .locator(".project-card")
    .filter({ hasText: "molten-salt" })
    .getByRole("button", { name: "Open" })
    .click();
}

const WHEN = "2026-01-01T00:00:00+00:00";

const OUTPUTS = [
  {
    name: "run1/plot.png",
    size: 2048,
    modifiedAt: WHEN,
    source: "generated",
    shown_in: {
      chat_session_id: SESSION_ID,
      chat_title: "Density of FLiBe",
      run_id: "run-1",
      tool_call_id: "call-1",
    },
  },
  { name: "run1/older.log", size: 512, modifiedAt: "2025-12-31T00:00:00+00:00", source: "generated", shown_in: null },
  { name: "summary.txt", size: 100, modifiedAt: WHEN, source: "generated", shown_in: null },
];

const UPLOADS = [
  { name: "input.csv", size: 1024, modifiedAt: WHEN, source: "upload", shown_in: null },
];

// A 1x1 transparent PNG, so the preview's <img> has something real to load.
const PIXEL = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==",
  "base64",
);

test.describe("files page", () => {
  let stub: Stub | null = null;

  test.beforeEach(async ({ page }) => {
    stub = await installStub(page, {
      "GET /api/files/outputs": OUTPUTS,
      "GET /api/files/uploads": UPLOADS,
      // The preview of input.csv; the stub answers every route as JSON.
      "GET /api/files/uploads/input.csv": "a,b",
    });
    // Images are not fetches, so the stub cannot answer them. Registered after
    // the stub's tripwire, so it wins for this one URL.
    await page.route("**/api/files/outputs/run1/plot.png*", (route) =>
      route.fulfill({ status: 200, contentType: "image/png", body: PIXEL }),
    );
  });

  test.afterEach(async () => {
    if (stub) {
      expect(await stub.unstubbed(), "page asked for a route with no fixture").toEqual([]);
    }
    stub = null;
  });

  test("renders the Outputs and Uploads trees, labelled with their sandbox paths", async ({ page }) => {
    await openMoltenSalt(page);
    await page.getByRole("link", { name: "Files", exact: true }).click();
    await expect(page).toHaveURL(/\/files$/);

    const titles = page.locator(".files-section-title");
    await expect(titles).toHaveText(["Outputs", "Uploads"]);

    const outputs = page.locator(".files-section").filter({ hasText: "Outputs" });
    const uploads = page.locator(".files-section").filter({ hasText: "Uploads" });
    await expect(outputs.locator(".tag")).toHaveText("/mnt/data/output");
    await expect(uploads.locator(".tag")).toHaveText("/mnt/data/uploads");

    // Folders first, then files; a folder starts closed until something inside is selected.
    await expect(outputs.locator(".files-folder")).toHaveText(/run1/);
    await expect(outputs.getByText("summary.txt")).toBeVisible();
    await expect(outputs.getByText("plot.png")).toHaveCount(0);
    await expect(uploads.getByText("input.csv")).toBeVisible();

    await outputs.locator(".files-folder").click();
    await expect(outputs.getByText("plot.png")).toBeVisible();
  });

  test("a link with kind and path opens that file with its preview", async ({ page }) => {
    await openMoltenSalt(page);
    await page.goto("/files?kind=outputs&path=run1/plot.png");

    // Its folder is open and the row is the selected one.
    const row = page.locator(".files-row.selected");
    await expect(row).toContainText("plot.png");

    const preview = page.locator(".file-preview");
    await expect(preview.locator(".file-preview-name")).toHaveText("plot.png");
    await expect(preview.locator("img")).toBeVisible();
    await expect(preview.locator("img")).toHaveAttribute(
      "src",
      /\/api\/files\/outputs\/run1\/plot\.png\?project_name=molten-salt&preview=1/,
    );
  });

  test("selecting a file puts it in the URL without adding history", async ({ page }) => {
    await openMoltenSalt(page);
    await page.goto("/files");
    const before = await page.evaluate(() => history.length);

    await page.locator(".files-file").filter({ hasText: "input.csv" }).click();

    await expect(page).toHaveURL(/\/files\?kind=uploads&path=input\.csv$/);
    expect(await page.evaluate(() => history.length)).toBe(before);
  });

  test("Show in chat links to the turn that displayed the file, and only for such files", async ({ page }) => {
    await openMoltenSalt(page);
    await page.goto("/files?kind=outputs&path=run1/plot.png");

    const links = page.getByRole("link", { name: "Show in chat" });
    // The other outputs were never shown, so only one row offers it.
    await expect(links).toHaveCount(1);
    await expect(links).toHaveAttribute("href", `/?chat=${SESSION_ID}&run=run-1&step=call-1`);
    // The title names the chat for whoever hovers; the button itself does not.
    await expect(links).toHaveAttribute("title", "Shown in Density of FLiBe");
  });

  test("has no dataset catalog", async ({ page }) => {
    await openMoltenSalt(page);
    await page.goto("/files");
    await expect(page.locator(".files-section-title").first()).toBeVisible();
    await expect(page.getByText("AmSC Data Catalog")).toHaveCount(0);
    await expect(page.locator("#data-model-select")).toHaveCount(0);
  });
});
