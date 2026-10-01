import { expect, test, type Page } from "@playwright/test";
import { CHAT_SESSION, CHAT_SESSION_SUMMARY, SESSION_ID } from "./fixtures";
import { RUN_ID, by, installStub, startRun, type Stub } from "./stub";

/**
 * Run status dots (openspec change background-chat-runs, tasks 8.1 and 8.2).
 *
 * The conversation list shows each conversation's status, and the nav rail's
 * Chat entry shows the most urgent one, both from `GET /api/chat/runs/status`.
 * The stub answers that route, so each test sets the statuses it needs and
 * checks what is drawn. The page polls it every few seconds, so a status the
 * test changes mid-way shows up within one poll.
 */
const SECOND_ID = "00000000-0000-4000-8000-000000000004";
const SECOND_SUMMARY = { ...CHAT_SESSION_SUMMARY, id: SECOND_ID, title: "Second thread" };
const POLL = { timeout: 10_000 };

type Kind = "working" | "needs_you" | "done" | "failed" | "interrupted";

const entry = (id: string, status: Kind) => ({
  chat_session_id: id,
  status,
  unseen: status !== "working" && status !== "needs_you",
});

const ROUTES = {
  "GET /api/chat/sessions": [CHAT_SESSION_SUMMARY, SECOND_SUMMARY],
  "GET /api/chat/session": by("chat_session_id", {
    [SECOND_ID]: { ...CHAT_SESSION, id: SECOND_ID, title: "Second thread" },
    default: CHAT_SESSION,
  }),
};

async function openProject(page: Page) {
  await page.goto("/projects");
  await page
    .locator(".project-card")
    .filter({ hasText: "molten-salt" })
    .getByRole("button", { name: "Open" })
    .click();
  await expect(page.getByPlaceholder(/Ask a question/)).toBeVisible();
}

const rail = (page: Page) => page.getByRole("complementary", { name: "Primary navigation" });
const railDot = (page: Page) => rail(page).locator(".nav-rail-run-dot");
const listItem = (page: Page, title: string) =>
  page.locator(".conversation-list-item").filter({ hasText: title });

async function goToSkills(page: Page) {
  await rail(page).getByRole("link", { name: "Skills" }).click();
  await expect(page.locator(".page-topbar").getByRole("heading", { level: 1 })).toHaveText(
    "Skills",
  );
}

test.describe("run status dots", () => {
  let stub: Stub | null = null;

  test.afterEach(async () => {
    if (stub) {
      expect(await stub.unstubbed(), "page asked for a route with no fixture").toEqual([]);
    }
    stub = null;
  });

  // -------------------------------------------------------------------------
  // Conversation list: one dot per state
  // -------------------------------------------------------------------------

  const LIST_CASES: Array<{ status: Kind; label: string }> = [
    { status: "working", label: "Working" },
    { status: "needs_you", label: "Needs you" },
    { status: "done", label: "Finished, not yet opened" },
    { status: "failed", label: "Failed, not yet opened" },
    { status: "interrupted", label: "Interrupted, not yet opened" },
  ];

  for (const { status, label } of LIST_CASES) {
    test(`the list shows a ${status} dot on that conversation only`, async ({ page }) => {
      stub = await installStub(page, {
        ...ROUTES,
        "GET /api/chat/runs/status": [entry(SESSION_ID, status)],
      });
      await openProject(page);

      const dot = listItem(page, "Density of FLiBe").getByRole("img", { name: label });
      await expect(dot).toBeVisible();
      await expect(dot).toHaveAttribute("data-status", status);
      await expect(listItem(page, "Second thread").locator(".run-dot")).toHaveCount(0);
    });
  }

  test("an idle project shows no dots, and a run starting elsewhere adds one", async ({
    page,
  }) => {
    stub = await installStub(page, ROUTES);
    await openProject(page);
    await expect(page.locator(".conversation-list-item")).toHaveCount(2);
    await expect(page.locator(".run-dot")).toHaveCount(0);

    await stub.setRoute("GET /api/chat/runs/status", [entry(SECOND_ID, "working")]);
    await expect(listItem(page, "Second thread").locator(".run-dot")).toHaveAttribute(
      "data-status",
      "working",
      POLL,
    );
  });

  test("the working glow is animated, and still under reduced motion", async ({ page }) => {
    stub = await installStub(page, {
      ...ROUTES,
      "GET /api/chat/runs/status": [entry(SESSION_ID, "working")],
    });
    await openProject(page);
    const dot = listItem(page, "Density of FLiBe").locator(".run-dot");
    await expect(dot).toBeVisible();
    const style = () =>
      dot.evaluate((el) => {
        const css = getComputedStyle(el);
        return { animation: css.animationName, glow: css.boxShadow };
      });

    expect((await style()).animation).toBe("run-dot-glow");

    await page.emulateMedia({ reducedMotion: "reduce" });
    const still = await style();
    expect(still.animation).toBe("none");
    expect(still.glow).not.toBe("none");
  });

  // -------------------------------------------------------------------------
  // Nav rail summary
  // -------------------------------------------------------------------------

  const URGENCY_CASES: Array<{ statuses: Kind[]; shown: Kind }> = [
    { statuses: ["done", "failed", "needs_you"], shown: "needs_you" },
    { statuses: ["done", "working", "failed"], shown: "failed" },
    { statuses: ["working", "interrupted", "done"], shown: "interrupted" },
    { statuses: ["working", "done"], shown: "done" },
    { statuses: ["working"], shown: "working" },
  ];

  for (const { statuses, shown } of URGENCY_CASES) {
    test(`the rail shows ${shown} for ${statuses.join(" + ")}`, async ({ page }) => {
      const ids = statuses.map((_, i) => `00000000-0000-4000-8000-00000000010${i}`);
      stub = await installStub(page, {
        ...ROUTES,
        "GET /api/chat/runs/status": statuses.map((s, i) => entry(ids[i], s)),
      });
      await openProject(page);
      await expect(railDot(page)).toHaveAttribute("data-status", shown);
    });
  }

  test("a prompt while on Skills lights the rail, stays on Skills, and Chat opens it", async ({
    page,
  }) => {
    stub = await installStub(page, ROUTES);
    await openProject(page);
    await goToSkills(page);
    await expect(railDot(page)).toHaveCount(0);

    // A run in the second conversation starts waiting on a Lux login.
    await stub.setRoute("GET /api/chat/runs/status", [entry(SECOND_ID, "needs_you")]);
    await stub.setRoute(
      "GET /api/chat/session",
      by("chat_session_id", {
        [SECOND_ID]: {
          ...CHAT_SESSION,
          id: SECOND_ID,
          title: "Second thread",
          run_status: "needs_you",
          run_state: "running",
        },
        default: CHAT_SESSION,
      }),
    );
    await expect(railDot(page)).toHaveAttribute("data-status", "needs_you", POLL);
    await expect(rail(page).getByRole("button", { name: "Chat, needs you" })).toBeVisible();

    // Nothing moved on its own.
    await page.waitForTimeout(500);
    expect(new URL(page.url()).pathname).toBe("/skills");

    await stub.armAttach();
    await rail(page).getByRole("button", { name: /^Chat/ }).click();
    await expect(page.locator(".chat-session-banner")).toHaveText("Second thread");

    await stub.waitForStream("attach");
    await startRun(stub, "Submit the Lux job", "attach", RUN_ID);
    await stub.send(
      "mcp_form_elicitation",
      {
        event_kind: "mcp_form_elicitation",
        mode: "form",
        elicitation_id: "ssh-login-1",
        message: "SSH login for Lux",
        requested_schema: {
          type: "object",
          properties: { username: { type: "string", title: "Username" } },
          required: ["username"],
        },
      },
      "attach",
    );
    await expect(page.locator(".elicitation-modal")).toContainText("SSH login for Lux");
    await stub.end("attach");
  });

  test("with two conversations waiting, Chat opens the list", async ({ page }) => {
    stub = await installStub(page, {
      ...ROUTES,
      "GET /api/chat/runs/status": [
        entry(SESSION_ID, "needs_you"),
        entry(SECOND_ID, "needs_you"),
      ],
    });
    await openProject(page);
    await goToSkills(page);
    await expect(railDot(page)).toHaveAttribute("data-status", "needs_you");

    await rail(page).getByRole("button", { name: /^Chat/ }).click();
    await expect(page.locator(".conversation-list-item")).toHaveCount(2);
    await expect(page.locator(".chat-session-banner")).toHaveCount(0);
  });
});
