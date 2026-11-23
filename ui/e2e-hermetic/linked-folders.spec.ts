import { expect, test, type Page } from "@playwright/test";
import { SESSION_ID } from "./fixtures";
import { by, installStub, type Stub } from "./stub";

/**
 * Linked folders (openspec change linked-folders, tasks 8.1 and 8.2): linking,
 * renaming and unlinking one in a project's settings, and browsing one on the
 * Files page a directory at a time, read-only.
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

async function editMoltenSalt(page: Page) {
  await page.goto("/projects");
  await page
    .locator(".project-card")
    .filter({ hasText: "molten-salt" })
    .getByRole("button", { name: "Edit" })
    .click();
  return page.getByTestId("linked-folders");
}

test.describe("linked folders", () => {
  let stub: Stub | null = null;

  test.afterEach(async () => {
    if (stub) {
      expect(await stub.unstubbed(), "page asked for a route with no fixture").toEqual([]);
    }
    stub = null;
  });

  test("a folder is linked by typed path, renamed and unlinked in the project's settings", async ({ page }) => {
    stub = await installStub(page, {
      [`GET ${LINKS}`]: [],
      [`POST ${LINKS}`]: SALT,
      [`PATCH ${LINKS}/salt-data`]: { ...SALT, name: "msr-2025", sandbox_path: "/mnt/linked/msr-2025" },
      [`DELETE ${LINKS}/msr-2025`]: {},
    });
    const section = await editMoltenSalt(page);
    await expect(section).toContainText("No linked folders.");
    await expect(section).toContainText(WARNING);
    await expect(section).toContainText("Changes apply to each conversation from its next message.");
    // A browser has no folder picker: only the typed path.
    await expect(section.getByRole("button", { name: "Link folder…" })).toHaveCount(0);

    await stub.setRoute(`GET ${LINKS}`, [SALT]);
    await section.getByLabel("Folder path to link").fill("/Users/ada/Research/salt-data");
    await section.getByRole("button", { name: "Link", exact: true }).click();
    await expect(section.getByRole("list", { name: "Linked folders" })).toContainText("salt-data");
    await expect(section).toContainText("/mnt/linked/salt-data");
    expect(await stub.bodies(`POST ${LINKS}`)).toEqual([{ path: "/Users/ada/Research/salt-data" }]);

    const renamed = { ...SALT, name: "msr-2025", sandbox_path: "/mnt/linked/msr-2025" };
    await stub.setRoute(`GET ${LINKS}`, [renamed]);
    await section.getByRole("button", { name: "Rename salt-data" }).click();
    await section.getByLabel("New name for salt-data").fill("msr-2025");
    await section.getByRole("button", { name: "Save name" }).click();
    await expect(section).toContainText("/mnt/linked/msr-2025");
    expect(await stub.bodies(`PATCH ${LINKS}/salt-data`)).toEqual([{ name: "msr-2025" }]);

    await stub.setRoute(`GET ${LINKS}`, []);
    await section.getByRole("button", { name: "Unlink msr-2025" }).click();
    await expect(section).toContainText("No linked folders.");
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
    stub = await installStub(page, { [`GET ${LINKS}`]: [], [`POST ${LINKS}`]: SALT });
    const section = await editMoltenSalt(page);
    await stub.setRoute(`GET ${LINKS}`, [SALT]);
    await section.getByRole("button", { name: "Link folder…" }).click();
    await expect(section).toContainText("/mnt/linked/salt-data");
    expect(await stub.bodies(`POST ${LINKS}`)).toEqual([{ path: "/Users/ada/Research/salt-data" }]);
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
    await expect(linked.getByRole("button", { name: /msr-runs/ })).toBeDisabled();
    // Nothing is listed until a folder is opened.
    await expect(linked.getByText("README.md")).toHaveCount(0);

    await linked.getByRole("button", { name: /salt-data/ }).click();
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
