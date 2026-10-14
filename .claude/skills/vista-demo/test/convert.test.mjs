import assert from "node:assert/strict";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { captureToScript, retagByTime, runToSteps } from "../lib/capture.mjs";
import { loadScript, runEvents } from "../lib/script.mjs";

const PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==";
const ev = (t, event, data) => ({ t, event, data: { event_kind: event, ...data } });

// A run as lib/server.mjs's capture writes it (after the header line).
const RECORDED = [
  ev(0.0, "run_started", { run_id: "r1", user_prompt: "show me the phase diagram of BeF2-NaF-UF4" }),
  ev(0.1, "log", { level: "INFO", area: "Agent", message: "planning" }),
  ev(0.2, "part_start", { index: 0, part: { part_kind: "thinking", content: "I should plot it" } }),
  ev(0.3, "part_end", { index: 0, part: { part_kind: "thinking", content: "I should plot it" } }),
  ev(0.4, "part_start", { index: 1, part: { part_kind: "text", content: "" } }),
  ev(0.45, "part_delta", { index: 1, delta: { part_delta_kind: "text", content_delta: "Plotting " } }),
  ev(0.5, "part_delta", { index: 1, delta: { part_delta_kind: "text", content_delta: "it now." } }),
  ev(0.55, "part_end", { index: 1, part: { part_kind: "text", content: "Plotting it now." } }),
  ev(0.6, "function_tool_call", { part: { tool_name: "run_bash", args: '{"command": "python plot.py --bad"}', tool_call_id: "c1" } }),
  ev(1.1, "function_tool_result", { part: { part_kind: "retry-prompt", tool_name: "run_bash", content: "exit 2: unknown flag --bad", tool_call_id: "c1" } }),
  ev(1.2, "function_tool_call", { part: { tool_name: "run_bash", args: { command: "python plot.py" }, tool_call_id: "c2" } }),
  ev(3.4, "function_tool_result", { part: { part_kind: "tool-return", tool_name: "run_bash", content: "Wrote phase.png", tool_call_id: "c2" } }),
  ev(3.5, "function_tool_call", { part: { tool_name: "display_file", args: { path: "phase.png" }, tool_call_id: "c3" } }),
  ev(3.9, "function_tool_result", { part: { part_kind: "tool-return", tool_name: "display_file", content: [{ kind: "binary", media_type: "image/png", data: PNG }], tool_call_id: "c3" } }),
  ev(4.0, "final_result", { tool_name: null }),
  ev(4.0, "part_start", { index: 2, part: { part_kind: "text", content: "Here is " } }),
  ev(4.1, "part_delta", { index: 2, delta: { part_delta_kind: "text", content_delta: "the **liquidus** diagram." } }),
  ev(4.2, "part_end", { index: 2, part: { part_kind: "text", content: "Here is the **liquidus** diagram." } }),
  ev(4.3, "agent_run_result", { result: { output: "" } }),
  ev(4.3, "run_finished", { state: "done" }),
];

/** What a run shows: its tool calls and results, and its answers, in order. */
function visible(events) {
  const out = [];
  let text = "";
  for (const { event, data } of events) {
    if (event === "function_tool_call") {
      const args = typeof data.part.args === "string" ? JSON.parse(data.part.args) : data.part.args;
      out.push(["call", data.part.tool_name, args]);
    }
    if (event === "function_tool_result") out.push(["result", data.part.part_kind, data.part.content]);
    if (event === "part_delta" && data.delta.part_delta_kind === "text") text += data.delta.content_delta;
    if (event === "part_end" && data.part.part_kind === "text") {
      out.push(["answer", data.part.content.trim()]);
      text = "";
    }
  }
  return out;
}

function captureDir() {
  const src = mkdtempSync(path.join(os.tmpdir(), "vista-demo-convert-"));
  mkdirSync(path.join(src, "capture", "runs"), { recursive: true });
  const t0 = 1_760_000_000_000;
  const actions = [
    { kind: "goto", path: "/projects", at: t0, action: 1 },
    { kind: "click", spec: { role: "button", name: "Open", within: { css: ".project-card", text: "molten-salt" } }, at: t0 + 2000, action: 2 },
    { kind: "type", spec: { placeholder: "Ask a question" }, text: "show me the phase diagram of BeF2-NaF-UF4", at: t0 + 4000, action: 3 },
    { kind: "click", spec: { role: "button", name: "Send" }, at: t0 + 9000, action: 4 },
  ];
  writeFileSync(path.join(src, "capture", "actions.jsonl"), actions.map((a) => JSON.stringify(a)).join("\n") + "\n", "utf-8");
  // The run reached the server 5 ms after the Send click but was tagged with
  // the action before it, the race retagByTime exists for.
  const head = { run: 1, action: 3, at: t0 + 9005, prompt: "show me the phase diagram of BeF2-NaF-UF4" };
  writeFileSync(path.join(src, "capture", "runs", "1.jsonl"), [head, ...RECORDED].map((l) => JSON.stringify(l)).join("\n") + "\n", "utf-8");
  writeFileSync(path.join(src, "capture", "responses.jsonl"), JSON.stringify({ seq: 1, action: 3, at: t0 + 9010, method: "POST", path: "/projects/molten-salt/chat-sessions", body: {} }) + "\n", "utf-8");
  return src;
}

test("a converted run replays the same tool calls, results and answer", () => {
  const src = captureDir();
  retagByTime(src);
  captureToScript(src);
  const plan = loadScript(src, "script.original.yaml");
  assert.equal(plan.runs.length, 1);

  const replayed = visible(runEvents(plan.runs[0].steps, plan.pace).map((e) => ({ event: e.event, data: e.data })));
  const want = visible(RECORDED).map((v) =>
    v[0] === "result" && Array.isArray(v[2]) ? ["result", v[1], [{ kind: "binary", media_type: "image/png", data: PNG }]] : v,
  );
  const strip = (list) => list.map((v) => (v[0] === "result" && Array.isArray(v[2]) ? [v[0], v[1], v[2].map(({ identifier, ...rest }) => rest)] : v));
  assert.deepEqual(strip(replayed), strip(want));
});

test("the run is placed after the click that sent it, and responses are retagged", () => {
  const src = captureDir();
  retagByTime(src);
  captureToScript(src);
  const plan = loadScript(src, "script.original.yaml");
  const kinds = plan.steps.map((s) => s.kind);
  assert.deepEqual(kinds, ["card", "goto", "click", "type", "click", "run", "wait", "card"]);
  const row = JSON.parse(readFileSync(path.join(src, "capture", "responses.jsonl"), "utf-8"));
  assert.equal(row.action, 4, "a response arriving after the Send click counts it");
});

test("conversion keeps the readable parts and drops the noise", () => {
  const src = mkdtempSync(path.join(os.tmpdir(), "vista-demo-convert-"));
  const steps = runToSteps(RECORDED, { assetsDir: path.join(src, "assets"), run: 1 });
  assert.deepEqual(steps.map((s) => Object.keys(s)[0]), ["answer", "tool", "tool", "tool", "answer"]);
  assert.equal(steps[0].interim, true, "text before the final result is interim");
  assert.equal(steps[1].failed, true);
  assert.deepEqual(steps[1].args, { command: "python plot.py --bad" }, "string args are parsed");
  assert.equal(steps[2].duration_s, 2.2);
  assert.deepEqual(steps[3].result, { __asset: "run1-1.png" });
  assert.ok(existsSync(path.join(src, "assets", "run1-1.png")));
  assert.equal(steps[4].interim, undefined);
  assert.equal(steps[4].answer, "Here is the **liquidus** diagram.\n");
});

test("script.yaml is a copy to edit, left alone once it exists", () => {
  const src = captureDir();
  captureToScript(src);
  writeFileSync(path.join(src, "script.yaml"), "edited\n", "utf-8");
  captureToScript(src);
  assert.equal(readFileSync(path.join(src, "script.yaml"), "utf-8"), "edited\n");
  assert.match(readFileSync(path.join(src, "script.original.yaml"), "utf-8"), /never edited/);
});
