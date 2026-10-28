import { test, expect, type Page } from "@playwright/test";
import { installStub, streamOneTurn, type Stub } from "./stub";
import { SETTINGS, INFERENCE } from "./fixtures";

/**
 * The first-boot tour, walked in a real browser against stubbed responses.
 *
 * Settings here are stateful: `PUT /api/settings` merges into what the next
 * `GET` answers, and survives a reload (sessionStorage), as the backend's
 * `LocalSettingsTable` does. That is what lets the Exit and resume tests reload.
 */

const SKILL_DETAIL = {
  slug: "salt-analysis",
  frontmatter: { name: "salt-analysis" },
  markdown: "# Salt analysis\n\nLooks things up.",
  hpcJobs: [],
};

function tourAt(step: string | null) {
  return {
    "GET /api/skills/salt-analysis": SKILL_DETAIL,
    "GET /api/settings": {
      ...SETTINGS,
      onboarding_tour: step === null ? null : { status: "in_progress", step },
      onboarding_tour_enabled: true,
    },
  };
}

async function install(page: Page, step: string | null, extra: Record<string, unknown> = {}): Promise<Stub> {
  const stub = await installStub(page, { ...tourAt(step), ...extra });
  // Registered after the stub's own init script, so it wraps the stub's fetch.
  await page.addInitScript(() => {
    const KEY = "vista.test.settings";
    const routes = window.__vista.routes;
    try {
      const saved = sessionStorage.getItem(KEY);
      if (saved) routes["GET /api/settings"] = JSON.parse(saved);
    } catch {
      // no storage: the first load's settings stand
    }
    const stubbed = window.fetch.bind(window);
    window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
      const href = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      const method = (init?.method ?? "GET").toUpperCase();
      if (method === "PUT" && new URL(href, location.origin).pathname === "/api/settings") {
        const merged = { ...(routes["GET /api/settings"] as object), ...JSON.parse(String(init?.body ?? "{}")) };
        routes["GET /api/settings"] = merged;
        routes["PUT /api/settings"] = merged;
        sessionStorage.setItem(KEY, JSON.stringify(merged));
      }
      return stubbed(input, init);
    };
  });
  return stub;
}

async function activeProject(page: Page, name = "molten-salt") {
  await page.addInitScript((n) => localStorage.setItem("vista.activeProject.v1", n), name);
}

const popover = (page: Page) => page.locator(".driver-popover.vista-tour");

/** One coachmark: showing, and pointing where it should. */
async function card(page: Page, where: string) {
  await expect(popover(page)).toBeVisible({ timeout: 8000 });
  if (where === "center") {
    // A centred card hangs on driver's dummy element, not on the app.
    await expect(page.locator(".driver-active-element:not(#driver-dummy-element)")).toHaveCount(0);
  } else if (where === "overview") {
    // A screen's overview lights the page's top bar.
    await expect(page.locator(".page-topbar.driver-active-element")).toHaveCount(1, { timeout: 8000 });
  } else {
    await expect(page.locator(`[data-tour="${where}"].driver-active-element`)).toBeVisible({ timeout: 8000 });
  }
}

const press = (page: Page, name: string | RegExp = "Next") =>
  page.locator(".driver-popover").getByRole("button", { name }).click();

/** The tour states the page has written, in order. */
async function writes(stub: Stub) {
  const bodies = await stub.bodies("PUT /api/settings");
  return bodies.map((b) => b.onboarding_tour).filter(Boolean) as Array<{ status: string; step: string }>;
}

test("welcome, then the projects screen and opening the project", async ({ page }) => {
  await install(page, null);
  await page.goto("/projects");
  await card(page, "center");
  await expect(page.locator(".driver-popover")).toContainText("eight minutes");
  await press(page, "Start the tour");
  await card(page, "welcome:sidebar");
  await press(page);
  await card(page, "overview");
  await press(page);
  await card(page, "project:card");
  await press(page);
  await card(page, "project:new");
  await press(page);
  await card(page, "project:open");
  await page.locator('[data-tour="project:open"]').click();
  await card(page, "overview"); // the chat screen
  await press(page);
  await card(page, "first-question:send");
  await press(page);
  await card(page, "model:panel");
  await press(page);
  await card(page, "model:pick");
  await press(page);
  // The fixture's provider has a credential, so Settings is skipped.
  await card(page, "model:pick");
});

test("model: add a credential, then pick a model", async ({ page }) => {
  await activeProject(page);
  const stub = await install(page, "model", {
    "GET /api/settings/inference": { ...INFERENCE, has_credential: false, keys_set: { i2: false } },
  });
  await page.goto("/");
  await card(page, "overview");
  await press(page);
  await press(page);
  await press(page);
  await card(page, "model:pick");
  await expect(page.locator(".driver-popover")).toContainText("First, VISTA needs a model provider");
  await press(page);
  await card(page, "model:provider");
  await press(page);
  await card(page, "model:providers");
  // The provider is chosen on this coachmark, so its cards stay usable.
  await page.getByRole("radio", { name: /OLCF Inference/ }).click();
  await expect.poll(async () => (await stub.bodies("PUT /api/settings")).some((b) => b.inference_provider === "olcf")).toBe(true);
  await page.getByRole("radio", { name: /AmSC i2/ }).click();
  await press(page);
  await card(page, "model:credential");
  // Fetching a token from inside the field leaves the coachmark up.
  await page.locator('[data-tour="model:credential"] a').first().dispatchEvent("pointerdown");
  await page.waitForTimeout(300);
  await expect(page.locator(".driver-popover.vista-tour")).toBeVisible();
  const key = page.getByPlaceholder("API key");
  await key.click();
  await key.fill("sk-test");
  await expect(key).toHaveValue("sk-test");
  // The backend will report the key as set once it has been saved; the page asks again after saving.
  await stub.setRoute("GET /api/settings/inference", { ...INFERENCE, has_credential: true, keys_set: { i2: true } });
  await key.blur(); // a key saves when the field is left
  await expect.poll(async () => (await stub.bodies("PUT /api/settings")).some((b) => b.inference_api_key === "sk-test")).toBe(true);
  // The key is confirmed in Settings, which stays open until Next.
  await card(page, "model:credential");
  await expect(page.locator(".driver-popover")).toContainText("Key saved");
  await expect(page.locator(".modal-backdrop")).toHaveCount(1);
  await press(page);
  // Then the tour closes Settings itself and points at the picker.
  await card(page, "model:pick");
  await expect(page.locator(".modal-backdrop")).toHaveCount(0);
  await expect(page.locator(".driver-popover")).not.toContainText("First, VISTA needs a model provider");
  // Choosing a model (so the default stops being the default) ends the step.
  await stub.setRoute("GET /api/settings/inference", { ...INFERENCE, model_is_default: false });
  await page.locator(".model-picker-button").click();
  // The coachmark stays, and points at the open menu.
  await expect(page.locator('.model-picker-menu[data-tour="model:pick"].driver-active-element')).toBeVisible();
  await expect(page.locator(".driver-popover.vista-tour")).toBeVisible();
  await page.getByRole("option").first().click();
  await expect(page.locator(".driver-popover")).toContainText("Model set");
  expect((await stub.bodies("PUT /api/settings")).some((b) => "inference_model" in b)).toBe(true);
  await press(page);
  await card(page, "knowledge-bases:visit");
});

test("knowledge bases: open AI Safety Papers", async ({ page }) => {
  await activeProject(page);
  await install(page, "knowledge-bases");
  await page.goto("/projects");
  await card(page, "knowledge-bases:visit");
  await page.locator('[data-tour="knowledge-bases:visit"]').click();
  await card(page, "overview");
  await press(page);
  await card(page, "knowledge-bases:list");
  await press(page);
  await card(page, "knowledge-bases:open-kb");
  await page.locator('[data-tour="knowledge-bases:open-kb"]').click();
  await card(page, "knowledge-bases:about");
  await press(page);
  await card(page, "knowledge-bases:papers");
  await press(page);
  await card(page, "knowledge-bases:add-files");
  await press(page);
  await card(page, "skills:visit");
});

test("skills: open a skill and close it", async ({ page }) => {
  await activeProject(page, "alloy-design");
  const stub = await install(page, "skills");
  await page.goto("/projects");
  await card(page, "skills:visit");
  await page.locator('[data-tour="skills:visit"]').click();
  await card(page, "overview");
  await press(page);
  await card(page, "skills:loaded");
  await press(page);
  await card(page, "skills:available");
  await press(page);
  await card(page, "skills:open-skill");
  await page.locator('[data-tour="skills:open-skill"]').click();
  await card(page, "skills:detail");
  // Clicking inside the detail keeps the coachmark; Close moves on.
  await page.locator('[data-tour="skills:detail"]').click({ position: { x: 40, y: 120 } });
  await expect(popover(page)).toBeVisible();
  await page.locator(".modal").getByRole("button", { name: "Close" }).click();
  await card(page, "skills:load");
  await press(page);
  await card(page, "skills:import");
  const changes = (await stub.requests()).filter((r) => /^(PUT|POST|DELETE) \/api\/skills/.test(r));
  expect(changes).toEqual([]);
});

test("files: the example image and report", async ({ page }) => {
  await activeProject(page);
  await install(page, "files");
  await page.goto("/projects");
  await card(page, "files:visit");
  await page.locator('[data-tour="files:visit"]').click();
  await card(page, "overview");
  await press(page);
  await card(page, "files:outputs");
  await press(page);
  await card(page, "files:uploads");
  await press(page);
  await card(page, "files:open-chart");
  await page.locator('[data-tour="files:open-chart"]').click();
  await card(page, "files:preview");
  await expect(page.locator(".file-preview img")).toBeVisible();
  await press(page);
  await card(page, "files:open-report");
  await page.locator('[data-tour="files:open-report"]').click();
  await card(page, "files:preview");
  await expect(page.getByRole("heading", { name: "Risk summary" })).toBeVisible();
  await press(page);
  await card(page, "hpc:resources");
});

test("hpc: open the Odo card", async ({ page }) => {
  await activeProject(page);
  await install(page, "hpc");
  await page.goto("/projects");
  await card(page, "hpc:resources");
  await press(page);
  await card(page, "hpc:open-card");
  await page.locator('[data-tour="hpc:open-card"]').click();
  await card(page, "hpc:card");
  await press(page);
  await card(page, "hpc:check");
  await press(page);
  await card(page, "hpc:settings");
  await press(page);
  await card(page, "first-question:send");
});

test("first question: send the starter, see the activity, answer and finish", async ({ page }) => {
  await activeProject(page);
  const stub = await install(page, "first-question");
  await page.goto("/files");
  await card(page, "first-question:send");
  await expect(page.locator("textarea")).toHaveValue(/Cite the papers\.$/);
  await page.locator(".composer-send").click();
  await stub.waitForStream();
  await card(page, "model:panel");
  await streamOneTurn(stub);
  await card(page, "first-question:answer");
  await press(page);
  await card(page, "center");
  await expect(page.getByRole("link", { name: "Documentation" })).toBeVisible();
  await press(page, "Done");
  await expect(page.locator(".driver-popover")).toHaveCount(0);
  await expect.poll(async () => (await writes(stub)).at(-1)).toEqual({ status: "finished", step: "first-question" });
});

test("first question: a refused turn points at Settings", async ({ page }) => {
  await activeProject(page);
  await install(page, "first-question");
  await page.addInitScript(() => {
    const real = window.fetch;
    window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      if ((init?.method ?? "GET").toUpperCase() === "POST" && new URL(url, location.origin).pathname === "/api/chat") {
        return new Response(JSON.stringify({ detail: "No key.", code: "inference_not_configured" }), {
          status: 409,
          headers: { "content-type": "application/json" },
        });
      }
      return real(input, init);
    };
  });
  await page.goto("/");
  await card(page, "first-question:send");
  await page.locator(".composer-send").click();
  await card(page, "first-question:settings");
  await expect(page.locator(".driver-popover")).toContainText("needs a model connected");
});

test("skipping a step moves to the next step and saves it", async ({ page }) => {
  const stub = await install(page, null);
  await page.goto("/projects");
  await card(page, "center");
  await press(page, "Skip this step");
  // Welcome is passed over: the Projects screen is next.
  await card(page, "overview");
  await expect.poll(async () => (await writes(stub)).at(-1)).toEqual({ status: "in_progress", step: "project" });
});

test("Exit closes the tour for good: a reload shows none", async ({ page }) => {
  const stub = await install(page, null);
  await page.goto("/projects");
  await card(page, "center");
  await press(page, "Exit tutorial");
  await expect(page.locator(".driver-popover")).toHaveCount(0);
  await expect.poll(async () => (await writes(stub)).at(-1)).toMatchObject({ status: "exited" });
  await page.reload();
  await expect(page.locator(".project-card").first()).toBeVisible();
  await page.waitForTimeout(1500);
  await expect(page.locator(".driver-popover")).toHaveCount(0);
});

test("a reload mid-tour reopens the same step", async ({ page }) => {
  const stub = await install(page, null);
  await page.goto("/projects");
  await press(page, "Start the tour");
  await press(page); // sidebar
  await card(page, "overview"); // the Projects screen: step "project"
  await expect.poll(async () => (await writes(stub)).at(-1)).toEqual({ status: "in_progress", step: "project" });
  const title = await popover(page).locator(".driver-popover-title").innerText();
  await page.reload();
  await card(page, "overview");
  await expect(popover(page).locator(".driver-popover-title")).toHaveText(title);
});

test("a click on the control of a Next-only coachmark opens nothing and pulses the coachmark", async ({ page }) => {
  await activeProject(page, "alloy-design");
  const stub = await install(page, "skills");
  await page.goto("/projects");
  await page.locator('[data-tour="skills:visit"]').click();
  await card(page, "overview");
  await press(page);
  await press(page);
  await press(page);
  await card(page, "skills:open-skill");
  await page.locator('[data-tour="skills:open-skill"]').click();
  await card(page, "skills:detail");
  await page.locator(".modal").getByRole("button", { name: "Close" }).click();
  await card(page, "skills:load");
  await press(page);
  await card(page, "skills:import");
  await expect(popover(page)).not.toHaveClass(/vista-tour-nudge/);
  const before = (await stub.requests()).length;
  // A real click at the control's position: the tour's overlay answers it.
  const box = await page.locator('[data-tour="skills:import"]').boundingBox();
  expect(box).not.toBeNull();
  await page.mouse.click(box!.x + box!.width / 2, box!.y + box!.height / 2);
  await expect(popover(page)).toHaveClass(/vista-tour-nudge/);
  await expect(page.locator(".modal-backdrop")).toHaveCount(0);
  expect((await stub.requests()).length).toBe(before);
});

test("Escape closes the open settings dialog and leaves the tour; with nothing open it exits", async ({ page }) => {
  await activeProject(page);
  const stub = await install(page, "model", {
    "GET /api/settings/inference": { ...INFERENCE, has_credential: false, keys_set: { i2: false } },
  });
  await page.goto("/");
  await card(page, "overview");
  await press(page);
  await press(page);
  await press(page);
  await press(page); // the picker says a provider comes first
  await card(page, "model:provider"); // Settings opened at the provider
  await expect(page.locator(".modal-backdrop")).toHaveCount(1);
  await page.keyboard.press("Escape");
  await expect(page.locator(".modal-backdrop")).toHaveCount(0);
  // Only the dialog closed: the tour is still showing, and nothing was saved as exited.
  await expect(popover(page)).toBeVisible();
  expect((await writes(stub)).some((s) => s.status === "exited")).toBe(false);
  // With nothing open, Escape is the tour's own: it exits.
  await page.keyboard.press("Escape");
  await expect(page.locator(".driver-popover")).toHaveCount(0);
  await expect.poll(async () => (await writes(stub)).at(-1)).toMatchObject({ status: "exited" });
});

test("Escape with a skill's detail open does not exit the tour", async ({ page }) => {
  await activeProject(page, "alloy-design");
  const stub = await install(page, "skills");
  await page.goto("/projects");
  await page.locator('[data-tour="skills:visit"]').click();
  await card(page, "overview");
  await press(page);
  await press(page);
  await press(page);
  await page.locator('[data-tour="skills:open-skill"]').click();
  await card(page, "skills:detail");
  await page.keyboard.press("Escape");
  await page.waitForTimeout(500);
  // The skill detail has no Escape handler of its own (it closes with Close or
  // a click outside), and the tour leaves the key to a dialog that is open.
  await expect(popover(page)).toBeVisible();
  expect((await writes(stub)).some((s) => s.status === "exited")).toBe(false);
});
