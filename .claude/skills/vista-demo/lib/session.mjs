// A capture session (design D4, D6, D7): the capture server in front of the
// live backend, the private UI pointed at it, and either a browser the
// researcher drives (by hand) or the player driving a storyboard (live).
import { copyFileSync, existsSync, mkdirSync, readFileSync, readdirSync, renameSync, rmSync } from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { chromium } from "playwright";
import { RECORDER, startActionRecorder } from "./actions.mjs";
import { captureToScript, checkScrubbed, retagByTime } from "./capture.mjs";
import { PORTS } from "./paths.mjs";
import { SpendStop, VIEWPORT, play } from "./player.mjs";
import { parseScript } from "./script.mjs";
import { SCRUBBED } from "./scrub.mjs";
import { startCaptureServer } from "./server.mjs";
import { startUi } from "./ui.mjs";

export const LIVE_DEFAULTS = {
  backend: process.env.VISTA_BACKEND_URL || "http://127.0.0.1:8001",
  mcp: process.env.VISTA_MCP_URL || "http://127.0.0.1:8000/mcp",
};

async function reachable(url) {
  try {
    await fetch(url, { signal: AbortSignal.timeout(3000) });
    return true;
  } catch {
    return false;
  }
}

/** Which credential-named fields were scrubbed, and how often: [[name, count]]. */
export function scrubbedFields(src) {
  const counts = new Map();
  const walk = (d) => {
    for (const f of readdirSync(d, { withFileTypes: true })) {
      const full = path.join(d, f.name);
      if (f.isDirectory()) walk(full);
      else if (f.name.endsWith(".jsonl")) {
        for (const m of readFileSync(full, "utf-8").matchAll(new RegExp(`"([^"]+)":"${SCRUBBED}"`, "g"))) {
          counts.set(m[1], (counts.get(m[1]) ?? 0) + 1);
        }
      }
    }
  };
  walk(path.join(src, "capture"));
  return [...counts.entries()].sort((a, b) => b[1] - a[1]);
}

/** Retag, scan and convert a capture; returns the summary capture prints. */
export function finishCapture(src) {
  retagByTime(src);
  checkScrubbed(src);
  const { original, script, runs } = captureToScript(src);
  return { original, script, runs, scrubbed: scrubbedFields(src) };
}

/**
 * Capture a session into `src`/capture. `mode` is "by-hand" or "storyboard".
 * `driver` (tests, or Claude standing in for the researcher) is a module path
 * exporting `default async (page, { uiUrl })`, run headless in place of a
 * person at a visible window.
 */
export async function captureSession({ src, mode, storyboard, allowSpend = false, again = false, backend, mcp, driver, log = console.log }) {
  const captureDir = path.join(src, "capture");
  if (existsSync(captureDir) && readdirSync(captureDir).length) {
    if (!again) throw new Error(`${captureDir} already holds a capture; pass --again to replace it.`);
    rmSync(captureDir, { recursive: true, force: true });
    // The old script belongs to the old capture: keep its edits, but out of the way.
    const script = path.join(src, "script.yaml");
    if (existsSync(script)) {
      const kept = path.join(src, `script.before-${new Date().toISOString().replace(/[:.]/g, "-")}.yaml`);
      renameSync(script, kept);
      log(`capture: the previous script.yaml is now ${path.basename(kept)}`);
    }
  }
  if (!(await reachable(new URL("/openapi.json", backend)))) {
    throw new Error(`No VISTA backend answers at ${backend}. Start one (./launch.sh logs --no-electron) or pass --backend.`);
  }
  mkdirSync(captureDir, { recursive: true });

  const server = await startCaptureServer({ src, backendUrl: backend, mcpUrl: mcp, port: PORTS.server, log });
  const ui = await startUi({ backendUrl: server.url, log });
  const setAction = (action) => fetch(server.url + "/__demo/action", { method: "POST", body: JSON.stringify({ action }) });
  let stopped = null;
  try {
    if (mode === "storyboard") {
      copyFileSync(storyboard, path.join(captureDir, "storyboard.yaml"));
      const plan = parseScript(readFileSync(storyboard, "utf-8"), { assetsDir: path.join(src, "assets"), file: storyboard });
      log(`capture: playing ${plan.steps.length} storyboard steps against the live stack`);
      try {
        await play({ plan, uiUrl: ui.url, serverUrl: server.url, mode: "live", out: path.join(src, "out"), allowSpend, log });
      } catch (e) {
        if (!(e instanceof SpendStop)) throw e;
        stopped = e;
      }
    } else {
      const headless = Boolean(driver);
      const browser = await chromium.launch({ headless });
      try {
        const context = await browser.newContext({ viewport: VIEWPORT });
        await context.addInitScript(RECORDER);
        const page = await context.newPage();
        const rec = await startActionRecorder(page, path.join(captureDir, "actions.jsonl"), { onAction: setAction, log });
        await rec.goto("/projects");
        await page.goto(ui.url + "/projects");
        if (driver) {
          const mod = await import(pathToFileURL(path.resolve(driver)).href);
          await mod.default(page, { uiUrl: ui.url });
        } else {
          log("capture: use VISTA in the window that opened. Close the window when you are done.");
          await page.waitForEvent("close", { timeout: 0 });
        }
        // let the last run finish streaming before the server goes away
        for (let i = 0; i < 600 && server.state.runsFinished < server.state.runsStarted; i++) await new Promise((r) => setTimeout(r, 500));
        rec.flush();
      } finally {
        await browser.close().catch(() => {});
      }
    }
  } finally {
    ui.stop();
    await server.stop();
  }
  if (server.state.errors.length) log(`capture: ${server.state.errors.length} request(s) failed: ${server.state.errors[0]}`);
  if (stopped) return { stopped: stopped.message };
  return finishCapture(src);
}
