#!/usr/bin/env node
// vista-demo: capture, edit and replay VISTA sessions into demo videos.
// See ../SKILL.md for how Claude uses it.
import path from "node:path";
import { assembleVideo, contactSheet, probe } from "../lib/assemble.mjs";
import { startFrames } from "../lib/frames.mjs";
import { DEMO_HOME, PORTS, videoDirs } from "../lib/paths.mjs";
import { play } from "../lib/player.mjs";
import { loadScript } from "../lib/script.mjs";
import { loadResponses, startReplayServer } from "../lib/server.mjs";
import { ensureUiBuild, startUi } from "../lib/ui.mjs";

const HELP = `vista-demo <command> [video]

  ui                 build (or reuse) the private UI under ${DEMO_HOME}/_ui
  rehearse <video>   replay fast, one screenshot per step + out/contact.jpg
  record <video>     replay at human pace into out/frames/
  assemble <video>   out/frames → out/video.mp4
  capture <video>    record a live session (task group 2)
  script <video>     capture → script.original.yaml (task group 2)
  selftest           rehearse, record and assemble the bundled self-test

<video> is a folder name under ${DEMO_HOME}, an absolute path, or \`selftest\`.
`;

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

const [cmd, name] = process.argv.slice(2);
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
    case "capture":
    case "script":
      throw new Error(`\`${cmd}\` arrives with task group 2.`);
    case undefined:
    case "help":
    case "--help":
    case "-h":
      console.log(HELP);
      break;
    default:
      throw new Error(`Unknown command \`${cmd}\`.\n\n${HELP}`);
  }
} catch (e) {
  console.error(`vista-demo: ${e.message}`);
  process.exit(1);
}
