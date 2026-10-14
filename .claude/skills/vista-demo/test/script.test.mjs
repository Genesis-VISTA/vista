import assert from "node:assert/strict";
import path from "node:path";
import { test } from "node:test";
import { SKILL_DIR } from "../lib/paths.mjs";
import { loadScript, parseScript, runEvents } from "../lib/script.mjs";

test("the self-test script parses into steps and one run", () => {
  const plan = loadScript(path.join(SKILL_DIR, "selftest"));
  assert.equal(plan.video, "selftest");
  assert.equal(plan.runs.length, 1);
  assert.equal(plan.steps.find((s) => s.kind === "run").run, 0);
  assert.equal(plan.pace.tool_min_s, 1.0);
});

test("a run becomes the events run-renderer.ts draws, in order", () => {
  const plan = loadScript(path.join(SKILL_DIR, "selftest"));
  const kinds = runEvents(plan.runs[0].steps, plan.pace).map((e) => e.event);
  const collapsed = kinds.filter((k, i) => k !== kinds[i - 1]);
  assert.deepEqual(collapsed, [
    "function_tool_call", "function_tool_result",
    "function_tool_call", "function_tool_result",
    "final_result", "part_start", "part_delta", "part_end",
    "agent_run_result", "run_finished",
  ]);
  for (const e of runEvents(plan.runs[0].steps, plan.pace)) assert.equal(e.data.event_kind, e.event);
});

test("streamed words add up to the answer", () => {
  const events = runEvents([{ answer: "Two words.\n" }]);
  const text = events.filter((e) => e.event === "part_delta").map((e) => e.data.delta.content_delta).join("");
  assert.equal(text, "Two words.");
  assert.equal(events.find((e) => e.event === "part_end").data.part.content, "Two words.");
});

test("an !asset image becomes a binary tool return", () => {
  const plan = loadScript(path.join(SKILL_DIR, "selftest"));
  const display = plan.runs[0].steps.find((s) => s.tool === "display_file");
  assert.equal(display.result[0].kind, "binary");
  assert.equal(display.result[0].media_type, "image/svg+xml");
  assert.ok(display.result[0].data.length > 100);
});

test("only the last non-interim answer is final", () => {
  const events = runEvents([{ answer: "Working on it", interim: true }, { tool: "x" }, { answer: "Done" }]);
  const finals = events.filter((e) => e.event === "final_result");
  assert.equal(finals.length, 1);
  const order = events.map((e) => e.event);
  assert.ok(order.indexOf("final_result") > order.indexOf("function_tool_result"));
});

test("a step with no known kind is refused, naming its number", () => {
  assert.throws(() => parseScript("steps:\n  - goto: /x\n  - jump: 3\n"), /step 2 needs exactly one of/);
});
