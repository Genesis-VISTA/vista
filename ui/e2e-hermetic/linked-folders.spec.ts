import { expect, test, type Page } from "@playwright/test";
import { SESSION_ID } from "./fixtures";
import { by, installStub, type Stub } from "./stub";

/**
 * Linked folders (openspec change linked-folders, tasks 8.2 and 8.3), all on the
 * Files page: linking one beside Upload, renaming and unlinking it, and browsing it
 * a directory at a time, read-only.
 */

const LINKS = "/api/projects/molten-salt/linked-folders";
const WARNING =
  "The agent can read every file in this folder. If any files contain credentials, it may see them, be careful!";

const SALT = {
  name: "salt-data",
  host_path: "/Users/ada/Research/salt-data",
  sandbox_path: "/mnt/linked/salt-data",
  available: true,
  reason: null,
};
const GONE = {
  name: "msr-runs",
  host_path: "/Volumes/External/msr-runs",
  sandbox_path: "/mnt/linked/msr-runs",
  available: false,
  reason: "missing",
};

async function openMoltenSalt(page: Page) {
  await page.goto("/projects");
  await page
    .locator(".project-card")
    .filter({ hasText: "molten-salt" })
    .getByRole("button", { name: "Open" })
    .click();
}

/** The Files page with Link folder's panel open: the panel, and the Linked section. */
async function openLinking(page: Page) {
  await openMoltenSalt(page);
  await page.goto("/files");
  await page.locator(".files-toolbar").getByRole("button", { name: "Link folder…" }).click();
  return { panel: page.getByTestId("files-link-panel"), linked: page.getByTestId("files-linked") };
}

const EMPTY_FILES = { "GET /api/files/outputs": [], "GET /api/files/uploads": [] };

test.describe("linked folders", () => {
  let stub: Stub | null = null;

  test.afterEach(async () => {
    if (stub) {
      expect(await stub.unstubbed(), "page asked for a route with no fixture").toEqual([]);
    }
    stub = null;
  });

  test("a folder is linked by typed path, renamed and unlinked on the Files page", async ({ page }) => {
    const renamed = { ...SALT, name: "msr-2025", sandbox_path: "/mnt/linked/msr-2025" };
    stub = await installStub(page, {
      ...EMPTY_FILES,
      [`GET ${LINKS}`]: [],
      [`POST ${LINKS}`]: SALT,
      [`PATCH ${LINKS}/salt-data`]: renamed,
      [`DELETE ${LINKS}/msr-2025`]: {},
      [`GET ${LINKS}/salt-data/entries`]: [],
    });
    const { panel, linked } = await openLinking(page);
    await expect(linked).toContainText("No linked folders yet.");
    await expect(linked).toContainText("from its next message");
    await expect(panel).toContainText(WARNING);
    // A browser has no folder picker: only the typed path.
    await expect(panel.getByRole("button", { name: "Link folder…" })).toHaveCount(0);

    await stub.setRoute(`GET ${LINKS}`, [SALT]);
    await panel.getByLabel("Folder path to link").fill("/Users/ada/Research/salt-data");
    await panel.getByRole("button", { name: "Link", exact: true }).click();
    await expect(linked).toContainText("/mnt/linked/salt-data");
    expect(await stub.bodies(`POST ${LINKS}`)).toEqual([{ path: "/Users/ada/Research/salt-data" }]);

    await stub.setRoute(`GET ${LINKS}`, [renamed]);
    await linked.getByRole("button", { name: "Rename salt-data" }).click();
    await linked.getByLabel("New name for salt-data").fill("msr-2025");
    await linked.getByRole("button", { name: "Save name" }).click();
    await expect(linked).toContainText("/mnt/linked/msr-2025");
    expect(await stub.bodies(`PATCH ${LINKS}/salt-data`)).toEqual([{ name: "msr-2025" }]);

    await stub.setRoute(`GET ${LINKS}`, []);
    await linked.getByRole("button", { name: "Unlink msr-2025" }).click();
    await expect(linked).toContainText("No linked folders yet.");
    expect(await stub.requests()).toContain(`DELETE ${LINKS}/msr-2025`);
  });

  test("the VISTA window offers its folder picker", async ({ page }) => {
    await page.addInitScript(() => {
      window.vistaApp = {
        isVistaApp: true,
        restartToUpdate: async () => ({ ok: true }),
        pickFolder: async () => "/Users/ada/Research/salt-data",
      };
    });
    stub = await installStub(page, {
      ...EMPTY_FILES,
      [`GET ${LINKS}`]: [],
      [`POST ${LINKS}`]: SALT,
      [`GET ${LINKS}/salt-data/entries`]: [],
    });
    const { panel, linked } = await openLinking(page);
    await stub.setRoute(`GET ${LINKS}`, [SALT]);
    await panel.getByRole("button", { name: "Link folder…" }).click();
    await expect(linked).toContainText("/mnt/linked/salt-data");
    expect(await stub.bodies(`POST ${LINKS}`)).toEqual([{ path: "/Users/ada/Research/salt-data" }]);
  });

  test("the project's settings point to the Files page and manage no links", async ({ page }) => {
    stub = await installStub(page);
    await page.goto("/projects");
    await page
      .locator(".project-card")
      .filter({ hasText: "molten-salt" })
      .getByRole("button", { name: "Edit" })
      .click();
    const dialog = page.getByRole("dialog", { name: "Edit project" });
    await expect(dialog).toContainText("linked, renamed and unlinked on the project’s Files page");
    await expect(dialog.getByLabel("Folder path to link")).toHaveCount(0);
  });

  test("the Files page browses a linked folder a level at a time, read-only", async ({ page }) => {
    const when = "2026-11-20T10:00:00+00:00";
    stub = await installStub(page, {
      "GET /api/files/outputs": [],
      "GET /api/files/uploads": [],
      [`GET ${LINKS}`]: [SALT, GONE],
      [`GET ${LINKS}/salt-data/entries`]: by("dir", {
        default: [
          { name: "README.md", path: "README.md", type: "file", size: 120, modified: when, shown_in: null },
          { name: "runs", path: "runs", type: "folder", size: null, modified: when },
        ],
        runs: [{ name: "2026-11", path: "runs/2026-11", type: "folder", size: null, modified: when }],
        "runs/2026-11": [
          {
            name: "phase.png",
            path: "runs/2026-11/phase.png",
            type: "file",
            size: 2048,
            modified: when,
            shown_in: {
              chat_session_id: SESSION_ID,
              chat_title: "Phases",
              run_id: "run-1",
              tool_call_id: "call-1",
            },
          },
        ],
      }),
      "GET /api/files/linked/salt-data/README.md": "# Salt data\n\nRuns of FLiBe.",
    });
    await openMoltenSalt(page);
    await page.goto("/files");

    await expect(page.locator(".files-section-title")).toHaveText(["Outputs", "Uploads", "Linked"]);
    const linked = page.getByTestId("files-linked");
    await expect(linked).toContainText("/mnt/linked/salt-data");
    await expect(linked).toContainText("Unavailable: missing");
    await expect(linked.getByRole("button", { name: /^msr-runs/ })).toBeDisabled();
    // Nothing is listed until a folder is opened.
    await expect(linked.getByText("README.md")).toHaveCount(0);

    await linked.getByRole("button", { name: /^salt-data/ }).click();
    await expect(linked.getByText("README.md")).toBeVisible();
    await linked.getByRole("button", { name: "runs", exact: true }).click();
    await linked.getByRole("button", { name: "2026-11", exact: true }).click();
    await expect(linked.getByText("phase.png")).toBeVisible();
    expect(await stub.requests()).toEqual(
      expect.arrayContaining([
        `GET ${LINKS}/salt-data/entries`,
        `GET ${LINKS}/salt-data/entries?dir=runs`,
        `GET ${LINKS}/salt-data/entries?dir=runs%2F2026-11`,
      ]),
    );

    // Read-only: download and Show in chat, no delete.
    await expect(linked.getByRole("button", { name: /^Delete/ })).toHaveCount(0);
    const show = linked.getByRole("link", { name: "Show in chat" });
    await expect(show).toHaveAttribute("href", `/?chat=${SESSION_ID}&run=run-1&step=call-1`);

    await linked.locator(".files-file").filter({ hasText: "README.md" }).click();
    await expect(page).toHaveURL(/\/files\?kind=linked&path=salt-data%2FREADME\.md$/);
    const preview = page.locator(".file-preview");
    await expect(preview.locator(".file-preview-name")).toHaveText("README.md");
    await expect(preview.locator(".file-preview-markdown")).toContainText("Salt data");
  });

  test("a folder is linked from beside Upload on the Files page, and shown at once", async ({ page }) => {
    const when = "2026-11-20T10:00:00+00:00";
    stub = await installStub(page, {
      "GET /api/files/outputs": [],
      "GET /api/files/uploads": [],
      [`GET ${LINKS}`]: [],
      [`POST ${LINKS}`]: SALT,
      [`GET ${LINKS}/salt-data/entries`]: [
        { name: "README.md", path: "README.md", type: "file", size: 120, modified: when, shown_in: null },
      ],
    });
    await openMoltenSalt(page);
    await page.goto("/files");
    const linked = page.getByTestId("files-linked");
    await expect(linked).toContainText("No linked folders yet.");
    // Beside Upload, as a panel that says what linking grants before anything is linked.
    const toolbar = page.locator(".files-toolbar");
    await expect(toolbar.getByRole("button")).toHaveText(["Upload +", "Link folder…", "Refresh"]);
    await toolbar.getByRole("button", { name: "Link folder…" }).click();
    const panel = page.getByTestId("files-link-panel");
    await expect(panel).toContainText(WARNING);

    await stub.setRoute(`GET ${LINKS}`, [SALT]);
    await panel.getByLabel("Folder path to link").fill("/Users/ada/Research/salt-data");
    await panel.getByRole("button", { name: "Link", exact: true }).click();
    await expect(panel).toHaveCount(0);
    // Linked, listed, and opened, so its top level shows straight away.
    await expect(linked).toContainText("/mnt/linked/salt-data");
    await expect(linked.getByText("README.md")).toBeVisible();
    expect(await stub.bodies(`POST ${LINKS}`)).toEqual([{ path: "/Users/ada/Research/salt-data" }]);
    // Managed where it is listed.
    await expect(linked.getByRole("button", { name: "Unlink salt-data" })).toBeVisible();
  });

  test("a link to a linked file opens its folders and previews it", async ({ page }) => {
    const when = "2026-11-20T10:00:00+00:00";
    stub = await installStub(page, {
      "GET /api/files/outputs": [],
      "GET /api/files/uploads": [],
      [`GET ${LINKS}`]: [SALT],
      [`GET ${LINKS}/salt-data/entries`]: by("dir", {
        default: [{ name: "docs", path: "docs", type: "folder", size: null, modified: when }],
        docs: [{ name: "notes.md", path: "docs/notes.md", type: "file", size: 10, modified: when }],
      }),
      "GET /api/files/linked/salt-data/docs/notes.md": "notes",
    });
    await openMoltenSalt(page);
    await page.goto("/files?kind=linked&path=salt-data/docs/notes.md");
    await expect(page.locator(".files-row.selected")).toContainText("notes.md");
    await expect(page.locator(".file-preview .file-preview-name")).toHaveText("notes.md");
  });
});
