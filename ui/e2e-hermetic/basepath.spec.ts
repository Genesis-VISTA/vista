import { expect, test } from "@playwright/test";
import { finishRun, installStub, startRun, streamOneTurn, ANSWER_CHUNKS, type Stub } from "./stub";

/**
 * The UI served under a path (openspec change `multi-user-hosted`, design D8).
 *
 * Runs only under `npm run test:e2e:basepath`, which builds with
 * `VISTA_BASE_PATH` set and passes the same value as `HERMETIC_BASE_PATH`. The
 * stub records any `/api/` request that did not carry the base path as
 * unstubbed, so one client call that skipped `apiUrl()` fails here.
 */
const BASE = process.env.HERMETIC_BASE_PATH ?? "";

test.describe("served under a base path", () => {
  test.skip(!BASE, "set HERMETIC_BASE_PATH (npm run test:e2e:basepath)");

  let stub: Stub | null = null;

  test.beforeEach(async ({ page }) => {
    stub = await installStub(page);
  });

  test.afterEach(async () => {
    if (stub) {
      expect(await stub.unstubbed(), "an API call missed the base path or a fixture").toEqual([]);
    }
    stub = null;
  });

  test("opens a project and streams a chat under the path", async ({ page }) => {
    await page.goto(`${BASE}/projects`);

    const card = page.locator(".project-card").filter({ hasText: "molten-salt" });
    await expect(card).toBeVisible();
    await card.getByRole("button", { name: "Open" }).click();

    const composer = page.getByPlaceholder(/Ask a question/);
    await expect(composer).toBeVisible();
    expect(new URL(page.url()).pathname.startsWith(BASE)).toBe(true);

    await composer.fill("What is the density of FLiBe at 873 K?");
    await page.getByRole("button", { name: "Start chat" }).click();

    await stub!.waitForStream();
    await streamOneTurn(stub!, {});
    await expect(page.getByText(ANSWER_CHUNKS.join(""))).toBeVisible();
  });

  test("a file link in the agent's answer resolves under the path", async ({ page }) => {
    await page.goto(`${BASE}/projects`);
    await page
      .locator(".project-card")
      .filter({ hasText: "molten-salt" })
      .getByRole("button", { name: "Open" })
      .click();
    const composer = page.getByPlaceholder(/Ask a question/);
    await composer.fill("Plot it");
    await page.getByRole("button", { name: "Start chat" }).click();

    await stub!.waitForStream();
    await startRun(stub!, "Plot it");
    const text = "See [the plot](/api/files/outputs/plot.png?project_name=molten-salt).";
    await stub!.send("part_start", { event_kind: "part_start", index: 0, part: { part_kind: "text", content: "" } });
    await stub!.send("part_delta", {
      event_kind: "part_delta",
      index: 0,
      delta: { part_delta_kind: "text", content_delta: text },
    });
    await stub!.send("part_end", { event_kind: "part_end", index: 0, part: { part_kind: "text", content: text } });
    await stub!.send("agent_run_result", { event_kind: "agent_run_result", result: { new_messages: [] } });
    await finishRun(stub!, "done");
    await stub!.end();

    await expect(page.getByRole("link", { name: "the plot" })).toHaveAttribute(
      "href",
      `${BASE}/api/files/outputs/plot.png?project_name=molten-salt`,
    );
  });
});
