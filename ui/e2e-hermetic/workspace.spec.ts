import { expect, test } from "@playwright/test";
import { installStub, streamOneTurn, type Stub } from "./stub";

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
  await expect(page.getByPlaceholder(/Ask about molten salts/)).toBeVisible();
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

    await page.getByPlaceholder(/Ask about molten salts/).fill("What is the density of FLiBe?");
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

    await expect(page.getByPlaceholder(/Ask about molten salts/)).toHaveValue(text!);
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

    await page.getByPlaceholder(/Ask about molten salts/).fill("Do something unusual");
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
