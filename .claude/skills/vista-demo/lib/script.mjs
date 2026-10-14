// The video script (design D3): YAML steps in, a replay plan out.
//
// A plan is { video, pace, steps, runs }: `steps` are what the player does, in
// order; `runs` are the agent turns, in the order the replay server hands them
// out, each already turned into the SSE events the UI's run renderer draws.
import { readFileSync } from "node:fs";
import path from "node:path";
import YAML from "yaml";

export const DEFAULT_PACE = {
  typing: 1, // multiplier on human typing speed
  stream_wps: 40, // answer words per second
  tool_min_s: 1.2, // shortest time a tool call is shown running
  think_s: 0.6, // pause before the agent's first event
};

// `!asset name.png` in a script points at a file in the video's assets/.
export const assetTag = {
  tag: "!asset",
  identify: (v) => Boolean(v && typeof v === "object" && "__asset" in v),
  resolve: (value) => ({ __asset: String(value) }),
  stringify: (item) => String(item.value.__asset),
};

export const STEP_KINDS = new Set([
  "card", "chapter", "goto", "caption", "click", "type", "press", "run", "camera",
  "wait", "pause", "point", "spends", "readme",
]);

/** Steps that count as UI actions; see lib/server.mjs. */
export const ACTION_KINDS = new Set(["goto", "click", "type", "press"]);

const MIME = { ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".svg": "image/svg+xml", ".gif": "image/gif" };

/** Read an `!asset` reference as tool-return content. */
function loadAsset(ref, assetsDir) {
  const file = path.join(assetsDir, ref.__asset);
  const mime = MIME[path.extname(file).toLowerCase()];
  if (mime) {
    return [{ kind: "binary", media_type: mime, identifier: ref.__asset, data: readFileSync(file).toString("base64") }];
  }
  return readFileSync(file, "utf-8");
}

function resolveAssets(value, assetsDir) {
  if (value && typeof value === "object" && "__asset" in value) return loadAsset(value, assetsDir);
  if (Array.isArray(value)) return value.map((v) => resolveAssets(v, assetsDir));
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, resolveAssets(v, assetsDir)]));
  }
  return value;
}

/**
 * Turn a run's steps into timed SSE events, without run_started (which needs
 * the run id and prompt the server knows only when the UI asks).
 *
 * Each event is { event, data, delayMs }: wait delayMs, then send. Answers are
 * streamed word by word; the last answer that isn't `interim` is the final one.
 */
export function runEvents(runSteps, pace = DEFAULT_PACE, { newId = () => crypto.randomUUID() } = {}) {
  const events = [];
  const push = (event, data, delayMs = 0) => events.push({ event, data: { event_kind: event, ...data }, delayMs });
  const finalIndex = runSteps.findLastIndex((s) => "answer" in s && !s.interim);
  let partIndex = 0;
  let first = true;
  const lead = () => {
    const d = first ? Math.round(pace.think_s * 1000) : 250;
    first = false;
    return d;
  };

  runSteps.forEach((step, i) => {
    if ("tool" in step) {
      const id = newId();
      const callPart = { part_kind: "tool-call", tool_name: step.tool, args: step.args ?? {}, tool_call_id: id };
      push("function_tool_call", { part: callPart }, lead());
      const ms = Math.max(pace.tool_min_s, step.duration_s ?? 0) * 1000;
      const kind = step.failed ? "retry-prompt" : "tool-return";
      push(
        "function_tool_result",
        { part: { part_kind: kind, tool_name: step.tool, content: step.result ?? "", tool_call_id: id } },
        Math.round(ms),
      );
    } else if ("answer" in step) {
      const text = String(step.answer).replace(/\n$/, "");
      const index = partIndex++;
      if (i === finalIndex) push("final_result", { tool_name: null }, lead());
      push("part_start", { index, part: { part_kind: "text", content: "" } }, i === finalIndex ? 0 : lead());
      const words = text.match(/\S+\s*|\s+/g) ?? [];
      const perWord = Math.round(1000 / pace.stream_wps);
      for (const w of words) push("part_delta", { index, delta: { part_delta_kind: "text", content_delta: w } }, perWord);
      push("part_end", { index, part: { part_kind: "text", content: text } });
    } else {
      throw new Error(`A run step needs \`tool\` or \`answer\`: ${JSON.stringify(step)}`);
    }
  });
  push("agent_run_result", { result: { output: "" } }, 100);
  push("run_finished", { state: "done" });
  return events;
}

/** Parse a script's text into a plan. `assetsDir` resolves `!asset` references. */
export function parseScript(text, { assetsDir, file = "script.yaml" } = {}) {
  const doc = YAML.parse(text, { customTags: [assetTag] }) ?? {};
  if (!Array.isArray(doc.steps)) throw new Error(`${file}: needs a \`steps:\` list.`);
  const pace = { ...DEFAULT_PACE, ...(doc.pace ?? {}) };
  const runs = [];
  let actions = 0;
  const steps = doc.steps.map((raw, n) => {
    const kinds = Object.keys(raw ?? {}).filter((k) => STEP_KINDS.has(k));
    if (kinds.length !== 1) {
      throw new Error(`${file}: step ${n + 1} needs exactly one of ${[...STEP_KINDS].join(", ")}; got ${JSON.stringify(raw)}`);
    }
    const kind = kinds[0];
    const step = { n: n + 1, kind, value: raw[kind] };
    if (ACTION_KINDS.has(kind)) step.action = ++actions;
    if (kind === "run") {
      // `run: live` (storyboards only): the live agent supplies the turn.
      const live = step.value === "live";
      if (!live && !Array.isArray(step.value)) {
        throw new Error(`${file}: step ${n + 1}: \`run\` is a list of tool and answer steps, or \`live\` in a storyboard.`);
      }
      step.run = runs.length;
      step.live = live;
      runs.push({ step: n + 1, live, steps: live ? [] : resolveAssets(step.value, assetsDir) });
    }
    return step;
  });
  return { video: doc.video ?? path.basename(path.dirname(file)), pace, steps, runs };
}

export function loadScript(dir, file = "script.yaml") {
  const full = path.join(dir, file);
  return parseScript(readFileSync(full, "utf-8"), { assetsDir: path.join(dir, "assets"), file: full });
}
