import { expect, test, type Page } from "@playwright/test";
import { CHAT_SESSION, CHAT_SESSION_SUMMARY, SESSION_ID } from "./fixtures";
import {
  ANSWER_CHUNKS,
  RUN_ID,
  by,
  finishRun,
  installStub,
  startRun,
  streamAnswer,
  toolCall,
  type Stub,
} from "./stub";

/**
 * Chat runs that outlive the page (openspec change background-chat-runs).
 *
 * A turn belongs to the backend: the page only watches it. These flows check
 * that what the page shows follows from that: leaving a conversation stops the
 * watch and nothing more, coming back re-attaches and draws the run once, a
 * turn nobody watched is drawn like a live one, and prompts reappear.
 *
 * Like the rest of this folder, nothing outside the Next server runs.
 */
const QUESTION = "What is the density of FLiBe at 873 K?";
const ANSWER = ANSWER_CHUNKS.join("");
const SECOND_ID = "00000000-0000-4000-8000-000000000004";

const SECOND_SUMMARY = { ...CHAT_SESSION_SUMMARY, id: SECOND_ID, title: "Second thread" };
const SESSIONS = [CHAT_SESSION_SUMMARY, SECOND_SUMMARY];

/** `GET /api/chat/session`: the first conversation as given, the second always idle and empty. */
const sessionRoute = (first: Record<string, unknown> = {}) =>
  by("chat_session_id", {
    [SESSION_ID]: { ...CHAT_SESSION, ...first },
    [SECOND_ID]: { ...CHAT_SESSION, id: SECOND_ID, title: "Second thread" },
    default: { ...CHAT_SESSION, ...first },
  });

const ROUTES = {
  "GET /api/chat/sessions": SESSIONS,
  "GET /api/chat/session": sessionRoute(),
};

/** What the page saved of a run it was watching: the question and a first step. */
const PARTLY_SAVED = [
  { id: "saved-q", role: "user", content: QUESTION, run_id: RUN_ID },
  {
    id: "saved-step",
    role: "assistant",
    content: "Calling `rag_search`…",
    intermediate: true,
    run_id: RUN_ID,
  },
];

/** The compacted events the backend keeps for a run nobody watched. */
const storedRun = (state: "done" | "stopped" | "interrupted") => [
  { event: "run_started", data: { event_kind: "run_started", run_id: RUN_ID, user_prompt: QUESTION } },
  {
    event: "function_tool_call",
    data: {
      event_kind: "function_tool_call",
      part: { part_kind: "tool-call", tool_name: "rag_search", args: "{}" },
    },
  },
  {
    event: "function_tool_result",
    data: {
      event_kind: "function_tool_result",
      part: { part_kind: "tool-return", tool_name: "rag_search", content: "3 passages" },
    },
  },
  { event: "final_result", data: { event_kind: "final_result" } },
  {
    event: "part_start",
    data: { event_kind: "part_start", index: 0, part: { part_kind: "text", content: ANSWER } },
  },
  {
    event: "part_end",
    data: { event_kind: "part_end", index: 0, part: { part_kind: "text", content: ANSWER } },
  },
  {
    event: "agent_run_result",
    data: { event_kind: "agent_run_result", result: { new_messages: [] } },
  },
  { event: "run_finished", data: { event_kind: "run_finished", state } },
];

async function openProject(page: Page) {
  await page.goto("/projects");
  await page
    .locator(".project-card")
    .filter({ hasText: "molten-salt" })
    .getByRole("button", { name: "Open" })
    .click();
  await expect(page.getByPlaceholder(/Ask about molten salts/)).toBeVisible();
}

async function openConversation(page: Page, title: string) {
  await page.locator(".conversation-list-open").filter({ hasText: title }).click();
  await expect(page.locator(".chat-session-banner")).toHaveText(title);
}

async function ask(page: Page, text: string) {
  await page.getByPlaceholder(/Ask about molten salts/).fill(text);
  await page.getByRole("button", { name: "Send" }).click();
}

const rail = (page: Page) => page.getByRole("complementary", { name: "Primary navigation" });

/** Leave the chat page for Skills, and come back to the conversation list. */
async function goToSkillsAndBack(page: Page) {
  await rail(page).getByRole("link", { name: "Skills" }).click();
  await expect(page.locator(".page-topbar").getByRole("heading", { level: 1 })).toHaveText(
    "Skills",
  );
  await rail(page).getByRole("button", { name: "Chat" }).click();
  await expect(page.locator(".conversation-list-open").first()).toBeVisible();
}

const bubbles = (page: Page, role: "user" | "assistant" | "system") =>
  page.locator(`.chat-bubble.${role}`).filter({ hasNot: page.locator(".thinking-loader") });

test.describe("chat runs", () => {
  let stub: Stub | null = null;

  test.afterEach(async () => {
    if (stub) {
      expect(await stub.unstubbed(), "page asked for a route with no fixture").toEqual([]);
    }
    stub = null;
  });

  // -------------------------------------------------------------------------
  // Leaving a conversation stops watching, and shows nothing in the next one
  // -------------------------------------------------------------------------

  test("switching conversations mid-run shows no events in the wrong thread", async ({ page }) => {
    stub = await installStub(page, ROUTES);
    await openProject(page);
    await openConversation(page, "Density of FLiBe");

    await ask(page, QUESTION);
    await stub.waitForStream();
    await startRun(stub, QUESTION);
    await toolCall(stub);
    await expect(page.getByRole("status")).toHaveText("Searching the literature…");

    await page.getByRole("button", { name: "Back to conversations" }).click();
    await openConversation(page, "Second thread");

    // The page stopped watching, and only that: nothing cancelled the run.
    expect(await stub.aborted()).toEqual(["send"]);
    expect(await stub.requests()).not.toContain("POST /api/chat/run/stop");

    // Events that arrive after the switch have nowhere to go.
    await streamAnswer(stub);
    await expect(page.locator(".chat-list .chat-bubble")).toHaveCount(0);
    await expect(page.getByRole("status")).toHaveCount(0);
    await expect(page.locator(".composer-status")).toHaveCount(0);
    await expect(page.getByPlaceholder(/Ask about molten salts/)).toBeEnabled();
    await expect(page.getByText(ANSWER)).toHaveCount(0);
  });

  // -------------------------------------------------------------------------
  // Coming back
  // -------------------------------------------------------------------------

  test("navigating to Skills and back re-attaches, and the answer is drawn once", async ({
    page,
  }) => {
    stub = await installStub(page, ROUTES);
    await openProject(page);
    await openConversation(page, "Density of FLiBe");

    await ask(page, QUESTION);
    await stub.waitForStream();
    await startRun(stub, QUESTION);
    await toolCall(stub);
    await expect(page.getByRole("status")).toHaveText("Searching the literature…");

    await goToSkillsAndBack(page);

    // While away the run carried on. The conversation holds what the page saved
    // of it, and reports the run as still going.
    await stub.setRoute(
      "GET /api/chat/session",
      sessionRoute({ run_status: "working", run_state: "running", messages: PARTLY_SAVED }),
    );
    await stub.armAttach();
    await openConversation(page, "Density of FLiBe");

    await stub.waitForStream("attach");
    await expect(page.locator(".composer-status")).toHaveText("Agent working…");
    await startRun(stub, QUESTION, "attach");
    await toolCall(stub, "attach");
    await streamAnswer(stub, "attach");
    await finishRun(stub, "done", "attach");
    await stub.end("attach");

    await expect(page.getByText(ANSWER)).toHaveCount(1);
    await expect(page.locator(".chat-bubble.user").filter({ hasText: QUESTION })).toHaveCount(1);
    await expect(page.locator(".composer-status")).toHaveCount(0);

    // The page has watched the run to its end, so it acknowledges it once the
    // drawn thread is saved.
    await expect
      .poll(async () =>
        (await stub!.bodies("PUT /api/chat/session")).some((body) => body.ack_run === true),
      )
      .toBe(true);
    // And it never sends model history back.
    for (const body of await stub.bodies("PUT /api/chat/session")) {
      expect(body).not.toHaveProperty("message_history");
    }
  });

  test("a turn that finished while away is drawn like a live one, then acknowledged", async ({
    page,
  }) => {
    stub = await installStub(page, {
      ...ROUTES,
      "GET /api/chat/session": sessionRoute({
        run_state: "done",
        run_status: "done",
        run_unseen: true,
        run_events: storedRun("done"),
      }),
    });
    await openProject(page);
    await openConversation(page, "Density of FLiBe");

    await expect(page.getByText(ANSWER)).toHaveCount(1);
    await expect(page.locator(".chat-bubble.user").filter({ hasText: QUESTION })).toHaveCount(1);
    // The steps are in the Activity tab, as they would have been live.
    await page.getByRole("tab", { name: "Activity" }).click();
    await expect(page.locator(".activity-step")).not.toHaveCount(0);

    await expect
      .poll(async () =>
        (await stub!.bodies("PUT /api/chat/session")).some((body) => body.ack_run === true),
      )
      .toBe(true);
    // Nothing was running, so there was nothing to re-attach to.
    expect(await stub.requests()).not.toContain(
      `GET /api/chat/run/events?project_name=molten-salt&chat_session_id=${SESSION_ID}&after=0`,
    );
    await expect(page.getByPlaceholder(/Ask about molten salts/)).toBeEnabled();
  });

  // -------------------------------------------------------------------------
  // Stop, busy conversations, and how a turn ended
  // -------------------------------------------------------------------------

  test("a running conversation has its input disabled and a Stop that ends it as Stopped", async ({
    page,
  }) => {
    stub = await installStub(page, ROUTES);
    await openProject(page);
    await openConversation(page, "Density of FLiBe");

    await ask(page, QUESTION);
    await stub.waitForStream();
    await startRun(stub, QUESTION);
    await toolCall(stub);

    await expect(page.getByPlaceholder(/Ask about molten salts/)).toBeDisabled();
    await expect(page.getByRole("button", { name: "Send" })).toHaveCount(0);
    const stop = page.getByRole("button", { name: "Stop" });
    await expect(stop).toBeVisible();

    await stop.click();
    await expect
      .poll(async () => (await stub!.bodies("POST /api/chat/run/stop")).length)
      .toBe(1);
    expect((await stub.bodies("POST /api/chat/run/stop"))[0]).toEqual({
      project_name: "molten-salt",
      chat_session_id: SESSION_ID,
    });

    // The backend ends the turn and says so; the page marks it.
    await finishRun(stub, "stopped");
    await stub.end();
    await expect(bubbles(page, "system")).toHaveText("Stopped");
    await expect(page.getByRole("button", { name: "Stop" })).toHaveCount(0);
    await expect(page.getByPlaceholder(/Ask about molten salts/)).toBeEnabled();
    await expect(page.getByRole("button", { name: "Send" })).toBeVisible();
  });

  test("a turn cut off by VISTA quitting shows as interrupted when reopened", async ({ page }) => {
    stub = await installStub(page, {
      ...ROUTES,
      "GET /api/chat/session": sessionRoute({
        run_state: "interrupted",
        run_status: "interrupted",
        run_unseen: true,
        run_events: [
          storedRun("done")[0],
          { event: "run_finished", data: { event_kind: "run_finished", state: "interrupted" } },
        ],
      }),
    });
    await openProject(page);
    await openConversation(page, "Density of FLiBe");

    await expect(bubbles(page, "user")).toHaveText(QUESTION);
    await expect(bubbles(page, "system")).toHaveText("Interrupted when VISTA quit");
    await expect(page.getByPlaceholder(/Ask about molten salts/)).toBeEnabled();
  });

  test("sending into a conversation that is already running re-attaches to that run", async ({
    page,
  }) => {
    stub = await installStub(page, ROUTES);
    await openProject(page);
    await openConversation(page, "Density of FLiBe");

    await stub.armConflict();
    await stub.armAttach();
    await ask(page, "A second question");

    // The refused send is taken back: the unsent text returns to the composer and
    // the run that was already going is shown instead.
    await stub.waitForStream("attach");
    await startRun(stub, QUESTION, "attach");
    await toolCall(stub, "attach");
    await expect(page.locator(".chat-bubble.user").filter({ hasText: QUESTION })).toHaveCount(1);
    await expect(page.locator(".chat-bubble").filter({ hasText: "A second question" })).toHaveCount(
      0,
    );
    await expect(page.getByPlaceholder(/Ask about molten salts/)).toHaveValue("A second question");
    await expect(page.getByRole("status")).toHaveText("Searching the literature…");

    await streamAnswer(stub, "attach");
    await finishRun(stub, "done", "attach");
    await stub.end("attach");
    await expect(page.getByText(ANSWER)).toBeVisible();
  });

  // -------------------------------------------------------------------------
  // Prompts come back
  // -------------------------------------------------------------------------

  const SSH_SCHEMA = {
    type: "object",
    title: "SSH login",
    properties: {
      username: { type: "string", title: "Username" },
      password: { type: "string", title: "Password" },
    },
    required: ["username", "password"],
  };

  /** Start a turn, leave it for Skills, and re-attach with the run waiting on an SSH login. */
  async function returnToALoginPrompt(page: Page, stub: Stub) {
    await openProject(page);
    await openConversation(page, "Density of FLiBe");
    await ask(page, QUESTION);
    await stub.waitForStream();
    await startRun(stub, QUESTION);
    await toolCall(stub);

    await goToSkillsAndBack(page);

    await stub.setRoute(
      "GET /api/chat/session",
      sessionRoute({ run_status: "needs_you", run_state: "running", messages: PARTLY_SAVED }),
    );
    await stub.armAttach();
    await openConversation(page, "Density of FLiBe");
    await stub.waitForStream("attach");
    await startRun(stub, QUESTION, "attach");
    await toolCall(stub, "attach");
    await stub.send(
      "mcp_form_elicitation",
      {
        event_kind: "mcp_form_elicitation",
        mode: "form",
        elicitation_id: "ssh-login-1",
        message: "SSH login for Lux",
        requested_schema: SSH_SCHEMA,
      },
      "attach",
    );
  }

  test("the Lux SSH login is shown again after navigating back, and answering it submits", async ({
    page,
  }) => {
    stub = await installStub(page, ROUTES);
    await returnToALoginPrompt(page, stub);

    const modal = page.locator(".elicitation-modal");
    await expect(modal).toBeVisible();
    await expect(modal).toContainText("SSH login for Lux");

    await modal.getByLabel(/Username/).fill("ada");
    await modal.getByLabel(/Password/).fill("not-a-real-password");
    await modal.getByRole("button", { name: "Submit" }).click();

    await expect(modal).toHaveCount(0);
    await expect
      .poll(async () => (await stub!.bodies("POST /api/chat/elicitation")).length)
      .toBe(1);
    expect((await stub.bodies("POST /api/chat/elicitation"))[0]).toEqual({
      project_name: "molten-salt",
      id: "ssh-login-1",
      action: "accept",
      content: { username: "ada", password: "not-a-real-password" },
    });

    await stub.send(
      "prompt_resolved",
      { event_kind: "prompt_resolved", elicitation_id: "ssh-login-1" },
      "attach",
    );
    await streamAnswer(stub, "attach");
    await finishRun(stub, "done", "attach");
    await stub.end("attach");
    await expect(page.getByText(ANSWER)).toBeVisible();
  });

  test("a prompt answered in another view clears here", async ({ page }) => {
    stub = await installStub(page, ROUTES);
    await returnToALoginPrompt(page, stub);
    await expect(page.locator(".elicitation-modal")).toBeVisible();

    await stub.send(
      "prompt_resolved",
      { event_kind: "prompt_resolved", elicitation_id: "ssh-login-1" },
      "attach",
    );

    await expect(page.locator(".elicitation-modal")).toHaveCount(0);
    // Nothing was submitted from this view.
    expect(await stub.bodies("POST /api/chat/elicitation")).toEqual([]);
    await stub.end("attach");
  });

  test("a tool approval is shown again on re-attach and cleared when resolved elsewhere", async ({
    page,
  }) => {
    stub = await installStub(page, ROUTES);
    await openProject(page);
    await openConversation(page, "Density of FLiBe");
    await stub.setRoute(
      "GET /api/chat/session",
      sessionRoute({ run_status: "needs_you", run_state: "running" }),
    );
    // Reopen the conversation so it hydrates as a run that is waiting.
    await page.getByRole("button", { name: "Back to conversations" }).click();
    await stub.armAttach();
    await openConversation(page, "Density of FLiBe");
    await stub.waitForStream("attach");
    await startRun(stub, QUESTION, "attach");
    await stub.send(
      "mcp_tool_approval",
      {
        event_kind: "mcp_tool_approval",
        mode: "tool_approval",
        elicitation_id: "approval-1",
        tool_name: "submit_job",
        message: "Approve call to 'submit_job'?",
        args: { nodes: 4 },
      },
      "attach",
    );

    await expect(page.getByText("submit_job").first()).toBeVisible();
    await expect(page.getByRole("button", { name: /approve/i }).first()).toBeVisible();

    await stub.send(
      "prompt_resolved",
      { event_kind: "prompt_resolved", elicitation_id: "approval-1" },
      "attach",
    );
    await expect(page.getByRole("button", { name: /approve/i })).toHaveCount(0);
    await stub.end("attach");
  });
});
