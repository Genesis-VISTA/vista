import { expect, test, type Page } from "@playwright/test";
import { installStub } from "./stub";

/**
 * Settings > About's update flow against the built app (openspec change
 * `in-app-updates`), with the backend stubbed in the page. In VISTA's window
 * the page sees `window.vistaApp`; here an init script plays the window,
 * recording each restart it is asked for. Without it the page is a browser tab.
 */

const OFFERED = {
  version: "0.2.3",
  commit: "abc1234def",
  checks: "on",
  checked_at: "2026-11-04T09:00:00Z",
  latest: "0.2.5",
  notes_url: "https://github.com/Genesis-VISTA/vista/releases/tag/v0.2.5",
  install_command: "curl -fsSL https://github.com/Genesis-VISTA/vista/releases/download/v0.2.5/install.sh | bash -s -- --version 0.2.5",
  data_reset: true,
};

const DOWNLOADING = { state: "downloading", version: "0.2.5", bytes: 300 * 1024 ** 2, total: 1700 * 1024 ** 2, error: null };
const READY = { ...DOWNLOADING, state: "ready", bytes: DOWNLOADING.total };

const ACTIVITY = {
  chat_runs: [
    {
      chat_session_id: "00000000-0000-4000-8000-0000000000c1",
      project_id: "00000000-0000-4000-8000-0000000000p1",
      needs_you: false,
      title: "Li6 enrichment",
      project_name: "molten-salt",
    },
  ],
  debates: 0,
  watched_jobs: [{ job_id: "42", cluster: "frontier", job_name: "neutronics", state: "RUNNING" }],
};

declare global {
  interface Window {
    __restarts: string[];
  }
}

/** Play VISTA's window: a bridge that records restarts and answers `answer`. */
async function asWindow(page: Page, answer: { ok: boolean; reason?: string } = { ok: true }) {
  await page.addInitScript((reply) => {
    window.__restarts = [];
    window.vistaApp = {
      isVistaApp: true,
      async restartToUpdate(version: string) {
        window.__restarts.push(version);
        return reply;
      },
    };
  }, answer);
}

async function openAbout(page: Page) {
  await page.getByRole("button", { name: /^Open settings/ }).click();
  const settings = page.getByRole("dialog", { name: "Settings" });
  await settings.getByRole("navigation", { name: "Settings sections" }).getByRole("button", { name: /^About/ }).click();
  return settings.getByRole("region", { name: "About" });
}

test("the window downloads, confirms what it interrupts, and restarts", async ({ page }) => {
  await asWindow(page);
  const stub = await installStub(page, {
    "GET /api/version": OFFERED,
    "POST /api/updates/download": DOWNLOADING,
    "GET /api/updates/download": DOWNLOADING,
    "GET /api/activity": ACTIVITY,
  });
  await page.goto("/");

  await expect(page.getByTestId("settings-update-marker")).toBeVisible();
  const about = await openAbout(page);
  await expect(page.getByTestId("about-update-marker")).toBeVisible();
  await expect(about.getByText("VISTA 0.2.5 is available.")).toBeVisible();
  await expect(about.getByRole("textbox", { name: "Install command" })).toHaveCount(0);

  await about.getByRole("button", { name: "Update to 0.2.5" }).click();
  await expect(about.getByRole("progressbar", { name: "Downloading VISTA 0.2.5" })).toBeVisible();
  await expect(about.getByText(/300 MB of 1\.7 GB/)).toBeVisible();
  expect(await stub.bodies("POST /api/updates/download")).toEqual([{ version: "0.2.5" }]);

  await stub.setRoute("GET /api/updates/download", READY);
  await about.getByRole("button", { name: "Restart to update" }).click();

  const confirm = about.getByRole("alertdialog", { name: "Restart to update to 0.2.5?" });
  await expect(confirm.getByText("Li6 enrichment in molten-salt is responding; its response will stop.")).toBeVisible();
  await expect(confirm.getByText(/neutronics \(42\) on Frontier keeps running/)).toBeVisible();
  await expect(confirm.getByText(/ask you to back up and reset your local data/)).toBeVisible();

  // Cancel: nothing restarts, and the download stays ready.
  await confirm.getByRole("button", { name: "Cancel" }).click();
  await expect(confirm).toHaveCount(0);
  expect(await page.evaluate(() => window.__restarts)).toEqual([]);

  await about.getByRole("button", { name: "Restart to update" }).click();
  await about.getByRole("button", { name: "Restart now" }).click();
  await expect(about.getByText("Restarting into VISTA 0.2.5…")).toBeVisible();
  expect(await page.evaluate(() => window.__restarts)).toEqual(["0.2.5"]);
  expect(await stub.unstubbed(), "page asked for a route with no fixture").toEqual([]);
});

test("a refused restart says why and keeps the update ready", async ({ page }) => {
  await asWindow(page, { ok: false, reason: "The downloaded update no longer matches its hashes." });
  await installStub(page, {
    "GET /api/version": OFFERED,
    "POST /api/updates/download": READY,
    "GET /api/updates/download": READY,
  });
  await page.goto("/");
  const about = await openAbout(page);

  await about.getByRole("button", { name: "Update to 0.2.5" }).click();
  await about.getByRole("button", { name: "Restart to update" }).click();
  await about.getByRole("button", { name: "Restart now" }).click();

  await expect(about.getByRole("alert")).toHaveText("The downloaded update no longer matches its hashes.");
  await expect(about.getByRole("button", { name: "Restart to update" })).toBeVisible();
});

test("a browser tab shows the install command instead of Update", async ({ page }) => {
  const stub = await installStub(page, { "GET /api/version": OFFERED });
  await page.goto("/");
  const about = await openAbout(page);

  await expect(about.getByText("VISTA 0.2.5 is available.")).toBeVisible();
  await expect(about.getByRole("textbox", { name: "Install command" })).toHaveText(OFFERED.install_command);
  await expect(about.getByRole("button", { name: /Update to/ })).toHaveCount(0);
  expect((await stub.requests()).filter((r) => r.includes("/api/updates/download"))).toEqual([]);
});

test("a development checkout shows its version and never offers to check", async ({ page }) => {
  await installStub(page);
  await page.goto("/");

  await expect(page.getByTestId("settings-update-marker")).toHaveCount(0);
  const about = await openAbout(page);
  await expect(about.getByRole("textbox", { name: "Installed version" })).toHaveText("dev");
  await expect(about.getByText("Update checks are off for development builds.")).toBeVisible();
  await expect(about.getByRole("button", { name: "Check now" })).toHaveCount(0);
});
