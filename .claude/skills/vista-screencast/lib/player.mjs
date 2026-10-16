// Plays a script in a browser (design D7). Modes: `record` (human pace,
// screencast frames) and `rehearse` (fast, a screenshot per step) against the
// replay server; `live` (fast, a screenshot per step) against the capture
// server, driving a real session from a storyboard.
import { mkdirSync, rmSync } from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { chromium } from "playwright";
import { OVERLAY } from "./overlay.mjs";
import { REPO_ROOT, SKILL_DIR } from "./paths.mjs";

export const VIEWPORT = { width: 1440, height: 810 }; // ×4/3 = 1920×1080 frames
const CARD = pathToFileURL(path.join(SKILL_DIR, "templates", "card.html")).href;
const ICON = pathToFileURL(path.join(REPO_ROOT, "electron", "assets", "icon.svg")).href;
const FIND_MS = 6_000;

export class StepError extends Error {}
/** A storyboard reached a `spends:` step without --allow-spend. */
export class SpendStop extends Error {}

/** A step's locator spec, as Playwright locator. See SCRIPT.md for the fields. */
export function toLocator(page, spec, base = page, { pick = true } = {}) {
  if (typeof spec === "string") spec = { text: spec };
  const scope = spec.within ? toLocator(page, spec.within, base) : base;
  const pattern = (s) => {
    const m = typeof s === "string" && s.match(/^\/(.*)\/([a-z]*)$/);
    return m ? new RegExp(m[1], m[2]) : s;
  };
  let loc;
  if (spec.role) {
    const name = pattern(spec.name);
    loc = scope.getByRole(spec.role, name === undefined ? {} : { name, exact: typeof name === "string" });
  } else if (spec.placeholder) loc = scope.getByPlaceholder(pattern(spec.placeholder));
  else if (spec.label) loc = scope.getByLabel(pattern(spec.label));
  else if (spec.css) loc = scope.locator(spec.css);
  else if (spec.text) loc = scope.getByText(pattern(spec.text), { exact: typeof pattern(spec.text) === "string" });
  else throw new Error(`A locator needs role, placeholder, label, css or text: ${JSON.stringify(spec)}`);
  if (spec.css && spec.text) loc = loc.filter({ hasText: pattern(spec.text) });
  if (spec.has) loc = loc.filter({ has: toLocator(page, spec.has, page, { pick: false }) });
  return pick ? loc.nth(spec.nth ?? 0) : loc;
}

const describe = (spec) => JSON.stringify(spec);

export async function play({
  plan, uiUrl, serverUrl, mode = "record", out, frames: startFrames, allowSpend = false, log = console.log,
}) {
  const live = mode === "live";
  const fast = mode === "rehearse" || live;
  const runLimitMs = live ? 15 * 60_000 : 120_000;
  const browser = await chromium.launch();
  const context = await browser.newContext({ viewport: VIEWPORT, deviceScaleFactor: fast ? 1 : 4 / 3 });
  await context.addInitScript(OVERLAY);
  const page = await context.newPage();
  const shots = path.join(out, "rehearse");
  if (fast) {
    rmSync(shots, { recursive: true, force: true });
    mkdirSync(shots, { recursive: true });
  }

  const sleep = (s) => page.waitForTimeout(Math.round(s * 1000 * (fast ? 0.25 : 1)));
  const overlay = (fn, ...args) => page.evaluate(([f, a]) => window.__screencast?.[f](...a), [fn, args]);
  const state = async () => (await fetch(serverUrl + "/__screencast/state")).json();
  const checkServer = async (step) => {
    const s = await state();
    if (s.errors.length) throw new StepError(`Step ${step.n} (${step.kind}): ${s.errors[0]}`);
    return s;
  };

  async function find(step, spec) {
    const loc = toLocator(page, spec);
    try {
      await loc.waitFor({ state: "visible", timeout: FIND_MS });
    } catch {
      throw new StepError(`Step ${step.n} (${step.kind}): nothing on the page matches ${describe(spec)}`);
    }
    return loc;
  }
  async function moveTo(loc, ms = 800) {
    const box = await loc.boundingBox();
    await overlay("move", box.x + box.width / 2, box.y + box.height / 2, fast ? 120 : ms);
    return box;
  }
  async function cameraTarget(zoom) {
    if (zoom === "latest") {
      return page.evaluate(() => {
        const list = document.querySelector("section.panel .chat-list")?.getBoundingClientRect();
        if (!list) return null;
        const users = document.querySelectorAll("section.panel .chat-list .chat-bubble.user");
        const last = users[users.length - 1]?.getBoundingClientRect();
        const top = last ? Math.max(list.top, last.top - 20) : list.top;
        return { x: list.left, y: top, width: list.width, height: Math.max(260, list.bottom - top) };
      });
    }
    const loc = toLocator(page, zoom);
    return (await loc.count()) ? loc.evaluate((el) => el.getBoundingClientRect().toJSON()) : null;
  }
  const focusedRect = () =>
    page.evaluate(() => {
      const el = document.activeElement;
      return el && el !== document.body ? el.getBoundingClientRect().toJSON() : null;
    });

  let cameraRect = null;
  let frames = null;
  let cardsShown = 0;

  const steps = {
    async card(step) {
      const v = step.value;
      const params = typeof v === "string" ? { kind: v } : { kind: "custom", ...v };
      params.icon = ICON;
      await page.goto(`${CARD}?${new URLSearchParams(params)}`);
      await overlay("showCursor", false);
      await sleep(params.kind === "intro" ? 4.5 : 3.2);
      await page.evaluate(() => document.body.classList.add("out"));
      await sleep(0.65);
      cardsShown += 1;
    },
    async chapter(step) {
      await steps.card({ ...step, value: { kind: "chapter", title: step.value } });
    },
    async goto(step) {
      await page.goto(uiUrl + step.value, { waitUntil: "load" });
      await overlay("showCursor", true);
      await page.waitForLoadState("networkidle", { timeout: 10_000 }).catch(() => {});
      await sleep(0.6);
    },
    async caption(step) {
      const v = step.value;
      if (!v || v === "off") return overlay("caption", null);
      const [num, text, sub] = Array.isArray(v) ? (v.length === 1 ? [null, v[0]] : v) : [null, v];
      await overlay("avoid", [await focusedRect(), cameraRect]);
      await overlay("caption", num == null ? null : String(num), text, sub ?? null);
      await sleep(0.5);
    },
    async click(step) {
      const loc = await find(step, step.value);
      await moveTo(loc);
      await sleep(0.12);
      await overlay("ripple");
      await loc.click();
      await sleep(0.3);
    },
    async type(step) {
      const { text, ...spec } = step.value;
      const loc = await find(step, spec);
      await moveTo(loc, 700);
      await overlay("ripple");
      await loc.click();
      const field = await loc.evaluate((el) => el.getBoundingClientRect().toJSON());
      await overlay("avoid", [field, cameraRect]);
      await page.waitForTimeout(550); // let the caption settle into its slot
      const cap = await overlay("captionRect");
      if (cap && cap.x < field.x + field.width && field.x < cap.x + cap.width && cap.y < field.y + field.height && field.y < cap.y + cap.height) {
        throw new StepError(`Step ${step.n} (type): the caption covers the field being typed into`);
      }
      // move the cursor off the field so the text stays readable
      const box = await loc.boundingBox();
      await overlay("move", box.x + box.width * 0.85, box.y + box.height + 30, fast ? 80 : 500);
      const pace = plan.pace.typing || 1;
      for (const ch of String(text)) {
        await loc.pressSequentially(ch);
        if (!fast) await page.waitForTimeout(((ch === " " ? 55 : 28) + Math.random() * 50) / pace);
      }
      await sleep(0.25);
    },
    async run(step) {
      const until = Date.now() + (live ? 60_000 : 15_000);
      while ((await checkServer(step)).runsStarted <= step.run) {
        if (Date.now() > until) {
          throw new StepError(`Step ${step.n} (run): the UI never started agent run ${step.run + 1}; did a Send click come before it?`);
        }
        await page.waitForTimeout(100);
      }
      await overlay("move", VIEWPORT.width * 0.55, VIEWPORT.height * 0.72, fast ? 100 : 900);
    },
    async camera(step) {
      const v = step.value;
      if (!v || v === "off" || v.zoom === "off") {
        cameraRect = null;
        await overlay("camera", null, 1, fast ? 200 : 1100);
        await overlay("avoid", [await focusedRect()]);
        return sleep(1.2);
      }
      const rect = await cameraTarget(v.zoom ?? "latest");
      if (!rect) throw new StepError(`Step ${step.n} (camera): nothing to zoom on for ${describe(v.zoom)}`);
      await overlay("camera", rect, v.scale ?? 1.3, fast ? 200 : 1100);
      await sleep(1.2);
      // where the target sits on screen now
      cameraRect = await cameraTarget(v.zoom ?? "latest");
      await overlay("avoid", [cameraRect]);
    },
    async wait(step) {
      if (typeof step.value === "number") return sleep(step.value);
      if (step.value !== "run") throw new StepError(`Step ${step.n} (wait): expected \`run\` or seconds`);
      const until = Date.now() + runLimitMs;
      for (;;) {
        const s = await checkServer(step);
        if (s.runsFinished >= s.runsStarted) break;
        if (Date.now() > until) throw new StepError(`Step ${step.n} (wait): the run did not finish in ${runLimitMs / 60_000} minutes`);
        await page.waitForTimeout(150);
      }
      // the composer's "Agent working…" goes when the page has drawn the end
      await page.locator(".composer-status").waitFor({ state: "hidden", timeout: 10_000 }).catch(() => {});
      if (cameraRect) {
        cameraRect = await cameraTarget("latest").catch(() => cameraRect);
      }
    },
    async pause(step) {
      await sleep(Number(step.value) || 1);
    },
    async point(step) {
      const loc = await find(step, step.value);
      await moveTo(loc, 1000);
      await sleep(0.6);
    },
    async spends(step) {
      // Only a live capture stops here (design D7); a replay spends nothing.
      if (live && !allowSpend) {
        throw new SpendStop(
          `Step ${step.n} (spends: ${step.value}): the next run spends real allocation. ` +
            "Ask the researcher; if they agree, capture again with --again --allow-spend.",
        );
      }
    },
    async press(step) {
      await page.keyboard.press(String(step.value));
      await sleep(0.3);
    },
    async readme(step) {
      frames?.mark(`readme-${step.value}`);
    },
  };

  try {
    await page.goto("about:blank");
    if (startFrames) frames = await startFrames(page);
    for (const step of plan.steps) {
      if (step.action) await fetch(serverUrl + "/__screencast/action", { method: "POST", body: JSON.stringify({ action: step.action }) });
      const brief = step.kind === "run" ? `run ${step.run + 1}` : JSON.stringify(step.value ?? null);
      log(`  ${String(step.n).padStart(3)} ${step.kind.padEnd(8)} ${brief.length > 90 ? brief.slice(0, 87) + "…" : brief}`);
      frames?.mark(`step ${step.n} ${step.kind}`);
      await steps[step.kind](step);
      await checkServer(step);
      if (fast) await page.screenshot({ path: path.join(shots, `s${String(step.n).padStart(3, "0")}-${step.kind}.png`) });
    }
    const result = frames ? await frames.stop() : null;
    return { result, shots: fast ? shots : null, cardsShown };
  } finally {
    await browser.close();
  }
}
