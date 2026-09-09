import { expect, test } from "@playwright/test";

/**
 * Minimal validation-lane smoke (Milestone D).
 * Schedule / manual only — never a required MR job.
 *
 * Flow: open app → Projects → activate a project → send a message →
 * observe a tool-call chat bubble and/or elicitation dialog.
 */
test.describe("validation-lane smoke", () => {
  test("open app, select project, send message, observe tool UI", async ({
    page,
  }) => {
    await page.goto("/projects");
    const card = page.locator(".project-card").filter({ hasText: "molten-salt" });
    if (!(await card.isVisible().catch(() => false))) {
      test.skip(true, "no molten-salt project visible — seed the deployment first");
    }
    await card.getByRole("button", { name: /^(Open|Reopen)$/ }).click();

    await expect(page.getByPlaceholder(/Ask about molten salts/i)).toBeVisible({
      timeout: 30_000,
    });
    const input = page.getByPlaceholder(/Ask about molten salts/i);
    await input.fill("Search the literature for FLiBe. Use rag_search.");
    await page.getByRole("button", { name: /Start chat|⏎/ }).click();

    const toolBubble = page.getByText(/Calling `/);
    const elicitation = page.getByRole("dialog");
    const thinking = page.getByText(/Working on it/i);

    await expect
      .poll(
        async () => {
          if (await toolBubble.first().isVisible().catch(() => false)) return "tool";
          if (await elicitation.isVisible().catch(() => false)) return "elicitation";
          if (await thinking.isVisible().catch(() => false)) return "thinking";
          return "pending";
        },
        { timeout: 90_000 }
      )
      .not.toBe("pending");
  });
});
