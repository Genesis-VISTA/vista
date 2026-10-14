import assert from "node:assert/strict";
import { mkdtempSync, readFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { after, before, test } from "node:test";
import { chromium } from "playwright";
import { RECORDER, startActionRecorder } from "../lib/actions.mjs";
import { toLocator } from "../lib/player.mjs";

const PAGE = `<!doctype html><html><body>
  <div class="project-card"><h3>alloy-design</h3><button>Open</button><button>Edit</button></div>
  <div class="project-card"><h3>molten-salt</h3><button>Open</button><button>Edit</button></div>
  <div class="project-card"><h3>NewProject</h3><p>Copied from molten-salt.</p><button>Open</button><button>Edit</button></div>
  <textarea placeholder="Ask a question… (e.g., 'What can you help me with?')"></textarea>
  <button aria-label="Send"><svg width="10" height="10"></svg></button>
  <div role="tablist"><button role="tab">Artifacts</button><button role="tab">Activity 2</button></div>
  <script>
    // Like VISTA's composer: Enter or Send sends and clears, without an input event.
    const box = document.querySelector("textarea");
    box.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); box.value = ""; }
    });
    document.querySelector("[aria-label=Send]").addEventListener("click", () => { box.value = ""; });
  </script>
</body></html>`;

let browser;
before(async () => {
  browser = await chromium.launch();
});
after(async () => browser?.close());

async function record(drive) {
  const file = path.join(mkdtempSync(path.join(os.tmpdir(), "vista-demo-actions-")), "actions.jsonl");
  const page = await browser.newPage();
  await page.addInitScript(RECORDER);
  const counts = [];
  const rec = await startActionRecorder(page, file, { onAction: async (n) => counts.push(n) });
  await page.setContent(PAGE);
  await page.evaluate(RECORDER); // setContent skips init scripts' navigation
  await drive(page);
  await page.waitForTimeout(150);
  rec.flush();
  const lines = readFileSync(file, "utf-8").trim().split("\n").map((l) => JSON.parse(l));
  return { lines, counts, page };
}

test("a click on a card's button is recorded within that card", async () => {
  const { lines, page } = await record(async (p) => {
    await p.locator(".project-card").nth(1).getByRole("button", { name: "Open" }).click();
  });
  assert.equal(lines.length, 1);
  // another card's description mentions molten-salt, so the scope names the title exactly
  assert.deepEqual(lines[0].spec, { role: "button", name: "Open", within: { css: ".project-card", has: { text: "molten-salt" } } });
  // and the recorded locator finds that same button again
  const found = await toLocator(page, lines[0].spec).evaluate((el) => el.closest(".project-card").querySelector("h3").innerText);
  assert.equal(found, "molten-salt");
  await page.close();
});

test("a name ending in a count is recorded without it", async () => {
  const { lines, page } = await record(async (p) => {
    await p.getByRole("tab", { name: "Activity 2" }).click();
  });
  assert.deepEqual(lines[0].spec, { role: "tab", name: "/^Activity/" });
  await page.evaluate(() => (document.querySelector("[role=tab]:last-child").textContent = "Activity 5"));
  assert.equal(await toLocator(page, lines[0].spec).textContent(), "Activity 5");
  await page.close();
});

test("clicking into a field and typing is one type step; Send and Enter follow", async () => {
  const { lines, counts, page } = await record(async (p) => {
    const box = p.getByPlaceholder(/^Ask a question/);
    await box.click();
    await box.pressSequentially("what is the most studied salt?");
    await p.getByRole("button", { name: "Send" }).click();
    await box.click();
    await box.pressSequentially("next");
    await box.press("Enter");
  });
  assert.deepEqual(
    lines.map((l) => l.kind),
    ["type", "click", "type", "press"],
  );
  assert.equal(lines[0].text, "what is the most studied salt?");
  assert.ok(lines[0].spec.placeholder?.startsWith("Ask a question"));
  assert.deepEqual(lines[1].spec, { role: "button", name: "Send" });
  assert.equal(lines[2].text, "next");
  assert.equal(lines[3].key, "Enter");
  assert.deepEqual(counts, [1, 2, 3, 4], "each action reported once, in order");
  assert.ok(lines.every((l) => l.at > 0 && !("x" in l) && !("y" in l)), "times, never coordinates");
  await page.close();
});
