import { expect, test, type Page } from "@playwright/test";
import { CHAT_SESSION } from "../e2e-hermetic/fixtures";
import { installStub, streamOneTurn, type Stub } from "../e2e-hermetic/stub";

/**
 * Not a test. Every screen and modal, once in each theme, for reviewing the
 * dark palette (add-dark-mode task 6.2) and for the before/after review page
 * built from these files. Like shots.spec.ts it drives the hermetic stub, so
 * both sides of each pair show exactly the same data; only the theme differs.
 *
 *   SHOT_DIR=/tmp/theme npx playwright test -c playwright.shots.config.ts e2e-shots/theme.spec.ts
 *
 * writes `<name>-light.png` and `<name>-dark.png` for every entry in SCREENS.
 * Kept out of the hermetic config so CI never runs it.
 */
const OUT = process.env.SHOT_DIR ?? "shots/theme";
const QUESTION = "What is the density of FLiBe at 873 K?";
const SAVED_ANSWER =
  "At 873 K, FLiBe (LiF-BeF₂, 66-34 mol%) has a density of about **1.96 g/cm³**, from the MSTDB-TP correlation ρ = 2.413 − 4.88×10⁻⁴·T.";

const OVERRIDES = {
  // A conversation that already happened: the thread has content, and Generate
  // report is enabled, since it writes from the saved message history.
  "GET /api/chat/session": {
    ...CHAT_SESSION,
    messages: [
      { id: "m1", role: "user", content: QUESTION },
      { id: "m2", role: "assistant", content: SAVED_ANSWER },
    ],
    message_history: [
      { kind: "request", parts: [{ part_kind: "user-prompt", content: QUESTION }] },
      { kind: "response", parts: [{ part_kind: "text", content: SAVED_ANSWER }] },
    ],
  },
  "GET /api/files/uploads": [
    { name: "flibe-density.csv", size: 20480, modifiedAt: "2026-01-01T00:00:00+00:00", source: "upload" },
    { name: "nacl-kcl-eutectic.csv", size: 8192, modifiedAt: "2026-01-01T00:00:00+00:00", source: "upload" },
  ],
  "POST /api/projects/molten-salt/reports/generate": {
    title: "Density of FLiBe at 873 K",
    slug_suggestion: "flibe-density-873k",
    summary: "FLiBe density from the MSTDB correlation, checked against two papers.",
    body: "## Result\n\nAt 873 K, FLiBe (LiF-BeF₂, 66-34 mol%) has a density of about 1.96 g/cm³.\n\n## Method\n\nThe linear correlation ρ = 2.413 − 4.88×10⁻⁴·T (g/cm³, T in K) from MSTDB-TP, cross-checked against the knowledge base.",
  },
  "GET /api/debates": [],
  "POST /api/skills/generate": {
    name_suggestion: "flibe-density",
    description_suggestion: "Look up FLiBe density at a temperature from MSTDB and cite the source.",
    body: "# FLiBe density\n\n1. Query MSTDB-TP for the LiF-BeF₂ density correlation.\n2. Evaluate it at the requested temperature.\n3. Cite the correlation's source.",
  },
};

async function open(page: Page): Promise<Stub> {
  const stub = await installStub(page, OVERRIDES);
  await page.addInitScript(() => {
    window.localStorage.setItem("vista.activeProject.v1", "molten-salt");
    window.localStorage.setItem("vista.navRail.collapsed.v1", "false");
  });
  return stub;
}

async function openProject(page: Page) {
  await page.goto("/projects");
  await page.locator(".project-card").filter({ hasText: "molten-salt" }).getByRole("button", { name: /Open|Reopen/ }).click();
  await expect(page.getByPlaceholder(/Ask a question/)).toBeVisible();
}

async function openConversation(page: Page) {
  await page.locator(".conversation-list-open").first().click();
  await expect(page.locator(".chat-session-banner")).toBeVisible();
}

/** A turn that called a tool and answered, so the thread holds every bubble kind. */
async function finishedTurn(page: Page, stub: Stub) {
  await openProject(page);
  await openConversation(page);
  await page.getByPlaceholder(/Ask a question/).fill(QUESTION);
  await page.getByRole("button", { name: "Send" }).click();
  await stub.waitForStream();
  await streamOneTurn(stub, { prompt: QUESTION });
  await expect(page.locator(".chat-bubble.assistant").last()).toContainText(/\S/);
}

/** A run left waiting on the page: a prompt event on the live stream. */
async function waitingOn(page: Page, stub: Stub, event: string, data: Record<string, unknown>) {
  await openProject(page);
  await openConversation(page);
  await page.getByPlaceholder(/Ask a question/).fill(QUESTION);
  await page.getByRole("button", { name: "Send" }).click();
  await stub.waitForStream();
  await stub.send("run_started", { event_kind: "run_started", run_id: "run-shots", user_prompt: QUESTION });
  await stub.send(event, { event_kind: event, ...data });
}

const settings = (page: Page) => page.getByRole("dialog", { name: "Settings" });

const SCREENS: { name: string; go: (page: Page, stub: Stub) => Promise<void> }[] = [
  { name: "chat-start", go: async (page) => openProject(page) },
  {
    name: "chat-thread",
    go: async (page) => {
      await openProject(page);
      await openConversation(page);
    },
  },
  { name: "chat-tool-call", go: finishedTurn },
  {
    name: "chat-activity",
    go: async (page, stub) => {
      await finishedTurn(page, stub);
      await page.getByRole("tab", { name: "Activity" }).click();
    },
  },
  {
    name: "tool-approval",
    go: async (page, stub) => {
      await waitingOn(page, stub, "mcp_tool_approval", {
        mode: "tool_approval",
        elicitation_id: "approval-shots",
        tool_name: "submit_job",
        message: "Approve call to 'submit_job'?",
        args: { cluster: "frontier", nodes: 4, walltime: "02:00:00" },
      });
      await expect(page.getByRole("button", { name: /approve/i }).first()).toBeVisible();
    },
  },
  {
    name: "elicitation",
    go: async (page, stub) => {
      await waitingOn(page, stub, "mcp_form_elicitation", {
        mode: "form",
        elicitation_id: "ssh-shots",
        message: "SSH login for Lux",
        requested_schema: {
          type: "object",
          title: "SSH login",
          properties: { username: { type: "string", title: "Username" }, password: { type: "string", title: "Password" } },
          required: ["username", "password"],
        },
      });
      await expect(page.locator(".elicitation-modal")).toBeVisible();
    },
  },
  {
    name: "report",
    go: async (page) => {
      await openProject(page);
      await openConversation(page);
      await page.getByRole("button", { name: /Generate report/ }).click();
      const report = page.getByRole("dialog", { name: "Conversation report" });
      await expect(report.locator(".report-summary")).toContainText("MSTDB correlation");
    },
  },
  {
    name: "skill-editor",
    go: async (page) => {
      await openProject(page);
      await openConversation(page);
      await page.getByRole("button", { name: /Generate report/ }).click();
      const report = page.getByRole("dialog", { name: "Conversation report" });
      await expect(report.locator(".report-summary")).toContainText("MSTDB correlation");
      await report.getByRole("button", { name: "Save as skill" }).click();
      // The draft arrives after the modal opens; wait for it to fill a field.
      await page.waitForFunction(() =>
        [...document.querySelectorAll("[aria-label='Save as skill'] input")].some(
          (el) => (el as HTMLInputElement).value === "flibe-density",
        ),
      );
    },
  },
  { name: "projects", go: async (page) => void (await page.goto("/projects")) },
  {
    name: "new-project",
    go: async (page) => {
      await page.goto("/projects");
      await page.getByRole("button", { name: /New project/ }).first().click();
      await expect(page.locator(".modal")).toBeVisible();
    },
  },
  { name: "skills", go: async (page) => void (await page.goto("/skills")) },
  {
    name: "skill-import",
    go: async (page) => {
      await page.goto("/skills");
      await page.getByRole("button", { name: "Import…" }).click();
      await expect(page.getByRole("dialog", { name: "Import skill" })).toBeVisible();
    },
  },
  { name: "skill-hub", go: async (page) => void (await page.goto("/skill-hub")) },
  { name: "knowledge-bases", go: async (page) => void (await page.goto("/knowledge-bases/project")) },
  { name: "datasets", go: async (page) => void (await page.goto("/datasets")) },
  { name: "hypothesis-lab", go: async (page) => void (await page.goto("/hypothesis-lab")) },
  {
    name: "settings",
    go: async (page) => {
      await page.goto("/projects");
      await page.getByRole("button", { name: "Open settings" }).click();
      await expect(settings(page).getByRole("region", { name: "Agent" })).toBeVisible();
    },
  },
  {
    name: "settings-appearance",
    go: async (page) => {
      await page.goto("/projects");
      await page.getByRole("button", { name: "Open settings" }).click();
      await settings(page).getByRole("button", { name: "Appearance" }).click();
      await expect(settings(page).getByRole("radio", { name: "System" })).toBeVisible();
    },
  },
  {
    // The collapsed rail's resources: short labels under short facility labels.
    name: "rail-collapsed",
    go: async (page) => {
      await page.goto("/projects");
      await page.getByRole("button", { name: "Collapse navigation" }).click();
      await expect(page.getByRole("button", { name: "Frontier: Ready" })).toBeVisible();
    },
  },
  {
    // Too narrow for both columns: the list on its own.
    name: "settings-narrow",
    go: async (page) => {
      await page.setViewportSize({ width: 600, height: 800 });
      await page.goto("/projects");
      await page.getByRole("button", { name: "Open settings" }).click();
      await expect(settings(page).getByRole("navigation", { name: "Settings sections" })).toBeVisible();
    },
  },
  {
    name: "settings-narrow-cluster",
    go: async (page) => {
      await page.setViewportSize({ width: 600, height: 800 });
      await page.goto("/projects");
      await page.getByRole("button", { name: "Open settings" }).click();
      await settings(page).getByRole("button", { name: /^Frontier\b/ }).click();
      await expect(settings(page).getByRole("button", { name: "All settings" })).toBeVisible();
    },
  },
  {
    name: "settings-cluster",
    go: async (page) => {
      await page.goto("/projects");
      await page.getByRole("button", { name: "Open settings" }).click();
      await settings(page).getByRole("button", { name: /^Frontier\b/ }).click();
      await expect(settings(page).getByRole("region", { name: "Frontier" })).toBeVisible();
    },
  },
];

for (const scheme of ["light", "dark"] as const) {
  test.describe(scheme, () => {
    test.use({ colorScheme: scheme });
    for (const screen of SCREENS) {
      test(screen.name, async ({ page }) => {
        const stub = await open(page);
        await screen.go(page, stub);
        // Let fetches settle and transitions finish, as shots.spec.ts does.
        await page.waitForLoadState("networkidle").catch(() => undefined);
        await page.waitForTimeout(500);
        await page.screenshot({ path: `${OUT}/${screen.name}-${scheme}.png` });
      });
    }
  });
}
