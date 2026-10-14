#!/usr/bin/env node
// vista-demo: capture, edit and replay VISTA sessions into demo videos.
// See ../SKILL.md for how Claude uses it.
import { existsSync } from "node:fs";
import path from "node:path";
import { parseArgs } from "node:util";
import { assembleVideo, contactSheet, probe } from "../lib/assemble.mjs";
import { startFrames } from "../lib/frames.mjs";
import { DEMO_HOME, PORTS, captureDirOf, videoDirs } from "../lib/paths.mjs";
import { play } from "../lib/player.mjs";
import { loadScript } from "../lib/script.mjs";
import { LIVE_DEFAULTS, captureSession, finishCapture } from "../lib/session.mjs";
import { loadResponses, startReplayServer } from "../lib/server.mjs";
import { ensureUiBuild, startUi } from "../lib/ui.mjs";

const HELP = `vista-demo <command> [video] [options]

  ui                 build (or reuse) the private UI under ${DEMO_HOME}/_ui
  capture <video> --by-hand
                     record a live session you drive in a browser window;
                     close the window to finish
  capture <video> --storyboard <file.yaml> [--allow-spend]
                     play a storyboard against the live stack and record it;
                     stops at a \`spends:\` step unless --allow-spend
      capture options: --backend <url> (default ${LIVE_DEFAULTS.backend}),
                     --mcp <url> (default ${LIVE_DEFAULTS.mcp}),
                     --again (replace an existing capture)
  script <video>     regenerate script.original.yaml from capture/
  rehearse <video>   replay fast, one screenshot per step + out/contact.jpg
  record <video>     replay at human pace into out/frames/
  assemble <video>   out/frames → out/video.mp4
  selftest           rehearse, record and assemble the bundled self-test

<video> is a folder name under ${DEMO_HOME}, an absolute path, or \`selftest\`.
`;

function report({ original, script, runs, scrubbed }) {
  console.log(`captured ${runs} run(s)`);
  console.log(`  ${original} (never edit)`);
  console.log(`  ${script} (edit this)`);
  console.log(
    scrubbed.length
      ? `scrubbed: ${scrubbed.map(([k, n]) => `${k} ×${n}`).join(", ")}`
      : "scrubbed: nothing needed scrubbing",
  );
}

async function replay(name, mode) {
  const dirs = videoDirs(name);
  const plan = loadScript(dirs.src);
  const responses = loadResponses(dirs.src);
  const server = await startReplayServer({
    plan,
    responses,
    port: PORTS.server,
    speed: mode === "rehearse" ? 0.15 : 1,
    log: (m) => console.error(m),
  });
  const ui = await startUi({ backendUrl: server.url });
  console.log(`${mode}: ${dirs.name} (${plan.steps.length} steps, ${plan.runs.length} runs)`);
  try {
    const { result, shots } = await play({
      plan,
      uiUrl: ui.url,
      serverUrl: server.url,
      mode,
      out: dirs.out,
      frames: mode === "record" ? (page) => startFrames(page, path.join(dirs.out, "frames")) : null,
    });
    if (shots) console.log(`contact sheet: ${contactSheet(shots, path.join(dirs.out, "contact.jpg"))}`);
    if (result) {
      console.log(`frames: ${result.count} over ${result.seconds.toFixed(1)} s in ${path.join(dirs.out, "frames")}`);
      console.log(`step start times: ${path.join(dirs.out, "frames", "marks.json")}`);
    }
    return dirs;
  } finally {
    ui.stop();
    await server.stop();
  }
}

function assemble(name) {
  const dirs = videoDirs(name);
  const file = assembleVideo(dirs.out);
  const p = probe(file);
  const v = p.streams.find((s) => s.type === "video");
  console.log(`${file}: ${p.duration.toFixed(1)} s, ${v.width}×${v.height} ${v.codec}, audio streams: ${p.streams.filter((s) => s.type === "audio").length}`);
  return file;
}

const { values: opts, positionals } = parseArgs({
  allowPositionals: true,
  options: {
    "by-hand": { type: "boolean" },
    storyboard: { type: "string" },
    "allow-spend": { type: "boolean" },
    again: { type: "boolean" },
    backend: { type: "string" },
    mcp: { type: "string" },
    driver: { type: "string" }, // a module standing in for the person at the window
    help: { type: "boolean", short: "h" },
  },
});
const [cmd, name] = opts.help ? ["help"] : positionals;
try {
  switch (cmd) {
    case "ui":
      console.log(await ensureUiBuild());
      break;
    case "rehearse":
    case "record":
      await replay(name, cmd);
      break;
    case "assemble":
      assemble(name);
      break;
    case "selftest":
      await replay("selftest", "rehearse");
      await replay("selftest", "record");
      assemble("selftest");
      break;
    case "capture": {
      const src = captureDirOf(name);
      const mode = opts.storyboard ? "storyboard" : opts["by-hand"] ? "by-hand" : null;
      if (!mode) throw new Error("Say how to drive the capture: --by-hand, or --storyboard <file.yaml>.");
      if (opts.storyboard && !existsSync(opts.storyboard)) throw new Error(`No storyboard at ${opts.storyboard}.`);
      const result = await captureSession({
        src,
        mode,
        storyboard: opts.storyboard && path.resolve(opts.storyboard),
        allowSpend: opts["allow-spend"],
        again: opts.again,
        backend: opts.backend ?? LIVE_DEFAULTS.backend,
        mcp: opts.mcp ?? LIVE_DEFAULTS.mcp,
        driver: opts.driver,
      });
      if (result.stopped) {
        console.error(`vista-demo: ${result.stopped}`);
        process.exit(3);
      }
      report(result);
      break;
    }
    case "script":
      report(finishCapture(captureDirOf(name)));
      break;
    case undefined:
    case "help":
      console.log(HELP);
      break;
    default:
      throw new Error(`Unknown command \`${cmd}\`.\n\n${HELP}`);
  }
} catch (e) {
  console.error(`vista-demo: ${e.message}`);
  process.exit(1);
}
