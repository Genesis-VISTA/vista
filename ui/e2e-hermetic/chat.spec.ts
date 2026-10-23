import { expect, test } from "@playwright/test";
import { CHAT_SESSION, SESSION_ID } from "./fixtures";
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

/**
 * A link that opens a conversation at a turn, as the Files page's "shown in" and the
 * artifacts feed make them: `/?chat=<id>&run=<run>&step=<tool call>`.
 */
test.describe("opening a conversation at a turn", () => {
  let stub: Stub | null = null;

  const filler = Array.from({ length: 12 }, (_, i) => `Line ${i + 1} of a long answer.`).join("\n\n");
  const turn = (run: string, answer: string, stepId: string | null) => [
    { id: `u-${run}`, role: "user", content: `Question for ${run}`, run_id: run },
    {
      id: `s-${run}`,
      role: "assistant",
      content: "Calling `display_file`…",
      intermediate: true,
      run_id: run,
      ...(stepId ? { tool_call_id: stepId } : {}),
    },
    { id: `a-${run}`, role: "assistant", content: answer, run_id: run },
  ];
  // The first turn is far above the last, so reaching it takes a scroll.
  const MESSAGES = [
    ...turn("run-1", `First answer\n\n${filler}`, "call-1"),
    ...turn("run-2", `Second answer\n\n${filler}`, "call-2"),
    ...turn("run-3", `Third answer\n\n${filler}`, "call-3"),
    // A step saved before steps carried their call's id.
    ...turn("run-4", "Last answer", null),
  ];

  test.beforeEach(async ({ page }) => {
    stub = await installStub(page, {
      "GET /api/chat/session": { ...CHAT_SESSION, messages: MESSAGES },
    });
    // The project is active once it has been opened; the link then names a conversation.
    await page.goto("/projects");
    await page
      .locator(".project-card")
      .filter({ hasText: "molten-salt" })
      .getByRole("button", { name: "Open" })
      .click();
    await expect(page.getByPlaceholder(/Ask a question/)).toBeVisible();
  });

  test.afterEach(async () => {
    if (stub) {
      expect(await stub.unstubbed(), "page asked for a route with no fixture").toEqual([]);
    }
    stub = null;
  });

  test("selects the chat, highlights the turn's answer and the step, and clears the URL", async ({ page }) => {
    await page.goto(`/?chat=${SESSION_ID}&run=run-1&step=call-1`);

    await expect(page.locator(".chat-session-banner")).toBeVisible();
    const answer = page.locator("#msg-a-run-1");
    await expect(answer).toHaveClass(/turn-highlight/);
    await expect(answer).toBeInViewport();
    // The step is in Activity, which is now the tab on show.
    await expect(page.getByRole("tab", { name: /Activity/ })).toHaveAttribute("aria-selected", "true");
    const step = page.locator("#msg-s-run-1");
    await expect(step).toHaveClass(/turn-highlight/);
    await expect(step).toBeInViewport();
    // Only the named run's bubbles.
    await expect(page.locator("#msg-a-run-2")).not.toHaveClass(/turn-highlight/);

    // Selecting it was the same as choosing it from the list.
    const stored = await page.evaluate(() =>
      window.localStorage.getItem("vista.activeChatSession.molten-salt.v1"),
    );
    expect(stored).toBe(SESSION_ID);
    // The params are gone, so a reload does not jump again.
    await expect.poll(() => new URL(page.url()).search).toBe("");
    await page.reload();
    await expect(page.locator(".chat-session-banner")).toBeVisible();
    await expect(page.locator(".turn-highlight")).toHaveCount(0);
  });

  test("an unknown run opens the conversation at its end, with no highlight", async ({ page }) => {
    await page.goto(`/?chat=${SESSION_ID}&run=run-nope&step=call-1`);

    await expect(page.locator(".chat-session-banner")).toBeVisible();
    await expect(page.locator("#msg-a-run-4")).toBeInViewport();
    await expect(page.locator(".turn-highlight")).toHaveCount(0);
    await expect(page.getByText("Conversation not found")).toHaveCount(0);
    await expect.poll(() => new URL(page.url()).search).toBe("");
  });

  test("an unknown conversation says so and otherwise opens as plain /", async ({ page }) => {
    await page.goto("/?chat=00000000-0000-4000-8000-0000000000ff&run=run-1&step=call-1");

    await expect(page.getByText("Conversation not found")).toBeVisible();
    // The conversation list, as `/` shows with none open.
    await expect(page.getByRole("button", { name: /Density of FLiBe/ })).toBeVisible();
    await expect(page.locator(".chat-session-banner")).toHaveCount(0);
    await expect.poll(() => new URL(page.url()).search).toBe("");
  });

  test("a step from before steps carried an id falls back to the turn", async ({ page }) => {
    await page.goto(`/?chat=${SESSION_ID}&run=run-4&step=call-4`);

    await expect(page.locator("#msg-a-run-4")).toHaveClass(/turn-highlight/);
    // Nothing in Activity matches, so the tab does not move.
    await expect(page.getByRole("tab", { name: "Artifacts" })).toHaveAttribute("aria-selected", "true");
    await expect(page.locator(".activity-step.turn-highlight")).toHaveCount(0);
  });
});
