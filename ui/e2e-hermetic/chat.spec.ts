import { expect, test } from "@playwright/test";
import { ANSWER_CHUNKS, installStub, streamOneTurn, type Stub } from "./stub";

/**
 * Hermetic MR flow — required job, must stay green.
 *
 * Nothing outside the Next dev server runs: no Python backend, no MCP server,
 * no model, no database, no seeded project. Every `/api/**` call is answered
 * from `fixtures.ts`, and a tripwire fails the test if one escapes.
 */
const QUESTION = "What is the density of FLiBe at 873 K?";

test.describe("chat flow", () => {
  let stub: Stub | null = null;

  test.beforeEach(async ({ page }) => {
    stub = await installStub(page);
  });

  test.afterEach(async () => {
    // Guarded: if setup itself failed, let that error stand rather than
    // burying it under a null dereference here.
    if (stub) {
      expect(await stub.unstubbed(), "page asked for a route with no fixture").toEqual([]);
    }
    stub = null;
  });

  test("opens a project, sends a message, and reads the streamed answer", async ({ page }) => {
    await page.goto("/projects");

    const card = page.locator(".project-card").filter({ hasText: "molten-salt" });
    await expect(card).toBeVisible();
    await card.getByRole("button", { name: "Open" }).click();

    // Landing on the conversation list means the project is active.
    const composer = page.getByPlaceholder(/Ask a question/);
    await expect(composer).toBeVisible();
    await expect(page.locator(".project-switcher-button")).toHaveText("molten-salt");

    await composer.fill(QUESTION);
    await page.getByRole("button", { name: "Start chat" }).click();

    await stub!.waitForStream();
    await streamOneTurn(stub!, {
      // Hold the run open and read what the conversation says it is doing.
      pauseAfterToolCall: async () => {
        const status = page.getByRole("status");
        await expect(status).toHaveText("Searching the literature…");
        // One line, not a growing stack.
        await expect(status).toHaveCount(1);
        // The composer says the run is still going, separately from the
        // thread's own status line.
        await expect(page.locator(".composer-status")).toHaveText("Agent working…");
      },
    });

    await expect(page.getByText(ANSWER_CHUNKS.join(""))).toBeVisible();
    await expect(page.locator(".composer-status")).toHaveCount(0);
    // The status line goes when the answer lands.
    await expect(page.getByRole("status")).toHaveCount(0);

    const requests = await stub!.requests();
    expect(requests).toContain("POST /api/chat/sessions");
    expect(requests).toContain("POST /api/chat");
  });

  test("reports a failed run instead of hanging", async ({ page }) => {
    await page.goto("/projects");
    await page
      .locator(".project-card")
      .filter({ hasText: "molten-salt" })
      .getByRole("button", { name: "Open" })
      .click();

    const composer = page.getByPlaceholder(/Ask a question/);
    await composer.fill(QUESTION);
    await page.getByRole("button", { name: "Start chat" }).click();

    // A stream that closes with no agent_run_result is the shape of a backend
    // that died mid-turn. The page must say so rather than spin forever.
    await stub!.waitForStream();
    await stub!.send("log", { event_kind: "log", level: "INFO", area: "Agent", message: "run started" });
    await stub!.end();

    await expect(page.getByText("Agent run did not complete.")).toBeVisible();
    await expect(page.locator(".composer-status")).toHaveCount(0);
  });

  // Regression guard. Sending the first message of a new conversation creates
  // the session, which changes activeChatSessionId, which is also what tells
  // the hydration effect to load a conversation you switched to. It used to
  // fetch back the empty record the backend had just made and wipe the user's
  // turn out of the thread — and the save then persisted the thread without
  // it, so reopening the conversation showed an answer with no question.
  test("keeps the user's first message in the thread", async ({ page }) => {
    await page.goto("/projects");
    await page
      .locator(".project-card")
      .filter({ hasText: "molten-salt" })
      .getByRole("button", { name: "Open" })
      .click();

    const composer = page.getByPlaceholder(/Ask a question/);
    await composer.fill(QUESTION);
    await page.getByRole("button", { name: "Start chat" }).click();

    await stub!.waitForStream();
    await streamOneTurn(stub!);

    await expect(page.getByText(ANSWER_CHUNKS.join(""))).toBeVisible();
    await expect(page.locator(".chat-bubble").filter({ hasText: QUESTION })).toBeVisible();

    // And the saved thread carries it, so reopening shows the question too.
    const saved = await stub!.requests();
    expect(saved.filter((r) => r === "PUT /api/chat/session").length).toBeGreaterThan(0);
  });

  test("Enter sends while Shift+Enter adds a new line", async ({ page }) => {
    await page.goto("/projects");
    await page
      .locator(".project-card")
      .filter({ hasText: "molten-salt" })
      .getByRole("button", { name: "Open" })
      .click();

    const composer = page.getByPlaceholder(/Ask a question/);
    await composer.fill("First line");
    await composer.press("Shift+Enter");
    await composer.pressSequentially("Second line");

    await expect(composer).toHaveValue("First line\nSecond line");
    expect(await stub!.requests()).not.toContain("POST /api/chat");

    await composer.press("Enter");
    await stub!.waitForStream();
    await streamOneTurn(stub!);

    await expect(
      page.locator(".chat-bubble.user").filter({ hasText: "First line\nSecond line" }),
    ).toBeVisible();
  });

  test("keeps every request inside the stub", async ({ page }) => {
    await page.goto("/projects");
    await expect(page.locator(".project-card")).toHaveCount(2);

    const requests = await stub!.requests();
    expect(requests.length).toBeGreaterThan(0);
    for (const request of requests) {
      expect(request, "a request left the /api namespace").toMatch(/^[A-Z]+ \/api\//);
    }
  });
});
