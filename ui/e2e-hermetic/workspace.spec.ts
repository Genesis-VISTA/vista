import { expect, test } from "@playwright/test";
import { SESSION_ID } from "./fixtures";
import { by, finishRun, installStub, startRun, streamOneTurn, type Stub } from "./stub";

/**
 * The chat workspace: tabs instead of two stacked panes, one status line in
 * the thread instead of a growing stack of tool bubbles, and the step detail
 * in a place with room for it.
 */
async function openConversation(page: import("@playwright/test").Page) {
  await page.goto("/projects");
  await page
    .locator(".project-card")
    .filter({ hasText: "molten-salt" })
    .getByRole("button", { name: "Open" })
    .click();
  await expect(page.getByPlaceholder(/Ask a question/)).toBeVisible();
}

/** Open the existing conversation, so the thread rather than the list shows. */
async function openThread(page: import("@playwright/test").Page) {
  await openConversation(page);
  await page.getByRole("button", { name: /Density of FLiBe/ }).click();
  await expect(page.locator(".chat-session-banner")).toBeVisible();
}

test.describe("chat workspace", () => {
  let stub: Stub | null = null;

  test.beforeEach(async ({ page }) => {
    stub = await installStub(page);
  });

  test.afterEach(async () => {
    if (stub) {
      expect(await stub.unstubbed(), "page asked for a route with no fixture").toEqual([]);
    }
    stub = null;
  });

  test("opens on Artifacts, with no Jobs tab when there is no campaign", async ({ page }) => {
    await openConversation(page);

    const tabs = page.getByRole("tablist", { name: "Workspace" });
    await expect(tabs.getByRole("tab", { name: "Artifacts" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    await expect(tabs.getByRole("tab", { name: "Activity" })).toBeVisible();
    // A campaign is a SPLASH thing nearly every conversation lacks, and a
    // permanently empty tab reads worse than an absent one.
    await expect(tabs.getByRole("tab", { name: "Jobs" })).toHaveCount(0);
  });

  test("steps land in Activity, not in the conversation", async ({ page }) => {
    await openConversation(page);

    await page.getByPlaceholder(/Ask a question/).fill("What is the density of FLiBe?");
    await page.getByRole("button", { name: "Start chat" }).click();
    await stub!.waitForStream();
    await streamOneTurn(stub!);

    const thread = page.locator(".chat-list").first();
    await expect(thread).toContainText("What is the density of FLiBe?");
    // The step is recorded, but the conversation is user turns and answers.
    await expect(thread.getByText(/Calling/)).toHaveCount(0);

    await page.getByRole("tab", { name: /Activity/ }).click();
    const activity = page.locator(".activity-view");
    await expect(activity).toBeVisible();
    await expect(activity).toContainText("rag_search");
  });

  test("the raw log is behind a toggle and says it is live only", async ({ page }) => {
    await openConversation(page);
    await page.getByRole("tab", { name: /Activity/ }).click();

    const toggle = page.getByRole("button", { name: /Show raw log/ });
    await expect(toggle).toBeVisible();
    await expect(page.locator(".log-viewer")).toHaveCount(0);

    await toggle.click();
    const viewer = page.locator(".log-viewer");
    await expect(viewer).toBeVisible();
    // Naming the limitation beats letting someone conclude the log is broken.
    await expect(viewer).toContainText("empty after a reload");

    await page.getByRole("button", { name: /Hide raw log/ }).click();
    await expect(page.locator(".log-viewer")).toHaveCount(0);
  });

  test("an empty conversation offers openers that prefill rather than send", async ({ page }) => {
    await openThread(page);

    const chips = page.locator(".chat-opener-chips .quick-chip");
    await expect(chips).toHaveCount(3);

    const first = chips.first();
    const text = await first.textContent();
    await first.click();

    await expect(page.getByPlaceholder(/Ask a question/)).toHaveValue(text!);
    // Nothing was sent.
    expect(await stub!.requests()).not.toContain("POST /api/chat");
  });

  // Both bypassed the agent loop and its approval gate, built a shell command
  // out of unquoted user input, and were the only path that filled the result
  // panels — which is why the stdout port had to land first.
  test("the salt quick-actions are gone", async ({ page }) => {
    await openConversation(page);

    await expect(page.getByRole("button", { name: /Analyze salt/ })).toHaveCount(0);
    await expect(page.getByRole("button", { name: /Predict salt/ })).toHaveCount(0);
  });

  test("a tool with no label reads as its own name", async ({ page }) => {
    await openConversation(page);

    await page.getByPlaceholder(/Ask a question/).fill("Do something unusual");
    await page.getByRole("button", { name: "Start chat" }).click();
    await stub!.waitForStream();

    await stub!.send("function_tool_call", {
      event_kind: "function_tool_call",
      part: { part_kind: "tool-call", tool_name: "some_unmapped_tool", args: "{}" },
    });

    await expect(page.getByRole("status")).toHaveText("some_unmapped_tool…");
    await stub!.end();
  });
});

/** The artifacts feed: the files `display_file` showed in the open conversation. */
const WHEN = "2026-01-01T00:00:00+00:00";

function shown(path: string, n: number, status: "current" | "changed" | "missing" = "current") {
  const name = path.split("/").pop()!;
  const mime = name.endsWith(".png") ? "image/png" : "text/csv";
  return {
    kind: "outputs",
    path,
    name,
    mime_type: mime,
    run_id: `run-${n}`,
    tool_call_id: `call-${n}`,
    shown_at: WHEN,
    status,
  };
}

// A 1x1 transparent PNG, so the figures' <img> have something real to load.
const PIXEL = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==",
  "base64",
);

test.describe("artifacts feed", () => {
  let stub: Stub | null = null;

  test.afterEach(async () => {
    if (stub) {
      expect(await stub.unstubbed(), "page asked for a route with no fixture").toEqual([]);
    }
    stub = null;
  });

  async function setUp(
    page: import("@playwright/test").Page,
    list: unknown[],
    extra: Record<string, unknown> = {},
  ) {
    stub = await installStub(page, {
      ...extra,
      // Another conversation's displays are not in this one's answer.
      "GET /api/chat/displayed-files": by("chat_session_id", {
        [SESSION_ID]: list,
        default: [shown("other.png", 9)],
      }),
    });
    // Images are not fetches, so the stub cannot answer them.
    await page.route("**/api/files/outputs/**/*.png*", (route) =>
      route.fulfill({ status: 200, contentType: "image/png", body: PIXEL }),
    );
    await page.route("**/api/files/outputs/*.png*", (route) =>
      route.fulfill({ status: 200, contentType: "image/png", body: PIXEL }),
    );
  }

  test("lists three displays newest first, and the same after a reload", async ({ page }) => {
    await setUp(page, [shown("c.png", 3), shown("run/b.csv", 2), shown("a.png", 1)]);
    await openThread(page);

    const items = page.locator(".artifact-item");
    await expect(items).toHaveCount(3);
    await expect(items.nth(0).locator("img")).toHaveAttribute("alt", "c.png");
    await expect(items.nth(1)).toContainText("b.csv");
    await expect(items.nth(2).locator("img")).toHaveAttribute("alt", "a.png");
    // Other conversations' displays are not here, and the request named this one.
    await expect(page.getByText("other.png")).toHaveCount(0);
    expect(await stub!.requests()).toContain(
      `GET /api/chat/displayed-files?project_name=molten-salt&chat_session_id=${SESSION_ID}`,
    );

    await page.reload();
    await expect(page.locator(".chat-session-banner")).toBeVisible();
    await expect(items).toHaveCount(3);
    await expect(items.nth(0).locator("img")).toHaveAttribute("alt", "c.png");
    await expect(items.nth(1)).toContainText("b.csv");
    await expect(items.nth(2).locator("img")).toHaveAttribute("alt", "a.png");
  });

  test("an image opens full size, and another file expands into its preview", async ({ page }) => {
    // The preview of b.csv; the stub answers every route as JSON.
    await setUp(page, [shown("run/b.csv", 2), shown("a.png", 1)], {
      "GET /api/files/outputs/run/b.csv": "x,y",
    });
    await openThread(page);

    await page.getByRole("button", { name: "b.csv" }).click();
    await expect(page.locator(".artifact-item").first().locator(".file-preview")).toBeVisible();

    await page.locator(".artifact-item img").click();
    await expect(page.getByRole("dialog")).toBeVisible();
  });

  test("a changed file says so, with no preview, and still links to Files and its turn", async ({ page }) => {
    await setUp(page, [shown("a.png", 1, "changed")]);
    await openThread(page);

    const item = page.locator(".artifact-item");
    await expect(item).toContainText("a.png");
    await expect(item).toContainText("file has changed on disk");
    await expect(item.locator("img")).toHaveCount(0);
    await expect(item.getByRole("link", { name: "Open in Files" })).toHaveAttribute(
      "href",
      "/files?kind=outputs&path=a.png",
    );
    await expect(item.getByRole("button", { name: "Show in conversation" })).toBeVisible();
  });

  test("a missing file says so, with no preview and no Files link", async ({ page }) => {
    await setUp(page, [shown("a.png", 1, "missing")]);
    await openThread(page);

    const item = page.locator(".artifact-item");
    await expect(item).toContainText("a.png");
    await expect(item).toContainText("file no longer exists");
    await expect(item.locator("img")).toHaveCount(0);
    await expect(item.getByRole("link", { name: "Open in Files" })).toHaveCount(0);
    await expect(item.getByRole("button", { name: "Show in conversation" })).toBeVisible();
  });

  test("a file shown during a run appears at the top without a reload", async ({ page }) => {
    await setUp(page, [shown("a.png", 1)]);
    await openThread(page);
    await expect(page.locator(".artifact-item")).toHaveCount(1);

    await page.getByPlaceholder(/Ask a question/).fill("Plot it");
    await page.getByRole("button", { name: "Send" }).click();
    await stub!.waitForStream();
    await startRun(stub!, "Plot it", "send", "run-2");
    await stub!.send("function_tool_call", {
      event_kind: "function_tool_call",
      part: { part_kind: "tool-call", tool_name: "display_file", args: "{}", tool_call_id: "call-2" },
    });
    await stub!.send("function_tool_result", {
      event_kind: "function_tool_result",
      part: {
        part_kind: "tool-return",
        tool_name: "display_file",
        tool_call_id: "call-2",
        content: {
          uri: "/api/files/outputs/new.png?project_name=molten-salt",
          mime_type: "image/png",
          filename: "new.png",
        },
      },
    });

    const items = page.locator(".artifact-item");
    await expect(items).toHaveCount(2);
    await expect(items.first().locator("img")).toHaveAttribute("alt", "new.png");

    // The run ends and the record is read back, with the new display in it.
    await stub!.setRoute("GET /api/chat/displayed-files", [shown("new.png", 2), shown("a.png", 1)]);
    await finishRun(stub!, "done");
    await stub!.end();
    await expect
      .poll(
        async () =>
          (await stub!.requests()).filter((r) => r.startsWith("GET /api/chat/displayed-files")).length,
      )
      .toBeGreaterThanOrEqual(2);
    await expect(items).toHaveCount(2);
  });
});
