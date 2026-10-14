// From a capture to an editable script (design D2, D3), and the steps that
// finish a capture: action tags fixed from timestamps, then the credential scan.
import { copyFileSync, existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import YAML from "yaml";
import { DEFAULT_PACE, assetTag } from "./script.mjs";
import { scanDir } from "./scrub.mjs";

const readJsonl = (file) =>
  existsSync(file)
    ? readFileSync(file, "utf-8").split("\n").filter((l) => l.trim()).map((l) => JSON.parse(l))
    : [];
const writeJsonl = (file, rows) => writeFileSync(file, rows.map((r) => JSON.stringify(r)).join("\n") + (rows.length ? "\n" : ""), "utf-8");

export function runFiles(src) {
  const dir = path.join(src, "capture", "runs");
  if (!existsSync(dir)) return [];
  return readdirSync(dir)
    .filter((f) => /^\d+\.jsonl$/.test(f))
    .sort((a, b) => parseInt(a) - parseInt(b))
    .map((f) => path.join(dir, f));
}

/**
 * Tag every response and run with the number of actions done before it, by
 * time. A request a click triggers can reach the capture server before the
 * recorder has reported the click; times put it after the click regardless.
 */
export function retagByTime(src) {
  const actions = readJsonl(path.join(src, "capture", "actions.jsonl"));
  if (!actions.length) return;
  const ats = actions.map((a) => a.at).sort((a, b) => a - b);
  const countBefore = (at) => ats.filter((t) => t <= at).length;
  const responsesFile = path.join(src, "capture", "responses.jsonl");
  writeJsonl(responsesFile, readJsonl(responsesFile).map((r) => (r.at ? { ...r, action: countBefore(r.at) } : r)));
  for (const file of runFiles(src)) {
    const [head, ...events] = readJsonl(file);
    writeJsonl(file, [head.at ? { ...head, action: countBefore(head.at) } : head, ...events]);
  }
}

/** Fail with every credential-shaped string left in the capture, by file and line. */
export function checkScrubbed(src) {
  const found = scanDir(path.join(src, "capture"));
  if (found.length) {
    const list = found.map((f) => `  capture/${f.file}:${f.line}: ${f.what}`).join("\n");
    throw new Error(`The capture still holds something that looks like a credential:\n${list}\nRemove it before the capture is used.`);
  }
}

const EXT = { "image/png": ".png", "image/jpeg": ".jpg", "image/svg+xml": ".svg", "image/gif": ".gif", "image/webp": ".webp" };
const isImage = (v) => v && typeof v === "object" && v.kind === "binary" && typeof v.media_type === "string" && v.media_type.startsWith("image/");

/**
 * One captured run's events as script run steps: each tool call with its
 * arguments and result, each answer as whole text. Images in tool results
 * are written to `assetsDir` and referenced with `!asset`. Log and thinking
 * events are dropped; a failed tool call is kept, marked `failed: true`.
 */
export function runToSteps(events, { assetsDir, run = 1 } = {}) {
  const steps = [];
  const calls = new Map(); // tool_call_id → { step, t }
  const texts = new Map(); // part index → { step, final }
  let finalSeen = false;
  let images = 0;

  const saveImages = (content) => {
    if (isImage(content)) {
      images += 1;
      const name = `run${run}-${images}${EXT[content.media_type] ?? ".bin"}`;
      if (assetsDir) {
        mkdirSync(assetsDir, { recursive: true });
        writeFileSync(path.join(assetsDir, name), Buffer.from(content.data, "base64"));
      }
      return { __asset: name };
    }
    if (Array.isArray(content)) {
      const out = content.map(saveImages);
      // a figure on its own reads as `result: !asset name`
      return out.length === 1 && out[0]?.__asset ? out[0] : out;
    }
    return content;
  };

  for (const { t = 0, event, data } of events) {
    if (event === "function_tool_call") {
      const part = data.part ?? {};
      let args = part.args ?? {};
      if (typeof args === "string") {
        try {
          args = JSON.parse(args);
        } catch {
          // keep the string as given
        }
      }
      const step = { tool: part.tool_name ?? "tool", args };
      steps.push(step);
      calls.set(part.tool_call_id ?? `#${steps.length}`, { step, t });
    } else if (event === "function_tool_result") {
      const part = data.part ?? data.result ?? {};
      const call = calls.get(part.tool_call_id) ?? [...calls.values()].findLast((c) => c.step.tool === part.tool_name && !("result" in c.step));
      const step = call?.step ?? (steps.push({ tool: part.tool_name ?? "tool", args: {} }), steps[steps.length - 1]);
      const dur = call ? Math.round((t - call.t) * 10) / 10 : 0;
      if (dur > 0) step.duration_s = dur;
      if (part.part_kind === "retry-prompt") {
        step.failed = true;
        step.result = typeof part.content === "string" ? part.content : JSON.stringify(part.content);
      } else {
        step.result = saveImages(part.content ?? "");
      }
    } else if (event === "part_start" && data.part?.part_kind === "text") {
      const step = { answer: data.part.content ?? "" };
      steps.push(step);
      texts.set(data.index, { step, final: finalSeen });
    } else if (event === "part_delta" && data.delta?.part_delta_kind === "text") {
      const acc = texts.get(data.index);
      if (acc) acc.step.answer += data.delta.content_delta ?? "";
    } else if (event === "part_end" && texts.has(data.index)) {
      const acc = texts.get(data.index);
      if (typeof data.part?.content === "string") acc.step.answer = data.part.content;
      if (!acc.final) acc.step.interim = true;
      texts.delete(data.index);
    } else if (event === "final_result") {
      finalSeen = true;
      // a text part already streaming is the final answer
      for (const acc of texts.values()) acc.final = true;
    }
  }
  // Text the model streamed with no final_result at all is still its answer.
  if (!finalSeen) {
    const last = steps.findLast((s) => "answer" in s);
    if (last) delete last.interim;
  }
  for (const s of steps) if ("answer" in s) s.answer = s.answer.replace(/\s+$/, "") + "\n";
  return steps.filter((s) => !("answer" in s) || s.answer.trim());
}

function actionStep(a) {
  if (a.kind === "goto") return { goto: a.path };
  if (a.kind === "click") return { click: a.spec };
  if (a.kind === "type") return { type: { ...a.spec, text: a.text } };
  if (a.kind === "press") return { press: a.key };
  throw new Error(`Unknown captured action ${JSON.stringify(a)}`);
}

/** The captured runs as { action, prompt, steps }, in order. */
export function capturedRuns(src) {
  const assetsDir = path.join(src, "assets");
  return runFiles(src).map((file, i) => {
    const [head, ...events] = readJsonl(file);
    return { action: head.action ?? 0, prompt: head.prompt ?? "", steps: runToSteps(events, { assetsDir, run: i + 1 }) };
  });
}

export const stringifyScript = (doc, header) =>
  header +
  YAML.stringify(doc, { customTags: [assetTag], blockQuote: "literal", lineWidth: 0, flowCollectionPadding: true });

/**
 * Write `script.original.yaml` from the capture, and `script.yaml` as a copy
 * to edit if there isn't one yet. A storyboard capture keeps the storyboard's
 * steps and fills each `run: live` with the run captured for it; a hand
 * capture turns each recorded action into a step and puts each run after the
 * action that started it.
 */
export function captureToScript(src, { name = path.basename(src) } = {}) {
  const runs = capturedRuns(src);
  const storyboardFile = path.join(src, "capture", "storyboard.yaml");
  let doc;
  if (existsSync(storyboardFile)) {
    doc = YAML.parse(readFileSync(storyboardFile, "utf-8"), { customTags: [assetTag] });
    let k = 0;
    doc.steps = doc.steps.map((s) => {
      if (s.run !== "live") return s;
      const run = runs[k++];
      if (!run) throw new Error(`The storyboard has more \`run: live\` steps than the capture has runs (${runs.length}).`);
      return { run: run.steps };
    });
    if (k < runs.length) throw new Error(`The capture has ${runs.length} runs but the storyboard asked for ${k}.`);
    doc.video = name;
  } else {
    const actions = readJsonl(path.join(src, "capture", "actions.jsonl"));
    if (!actions.length) throw new Error(`Nothing captured in ${path.join(src, "capture")}.`);
    const steps = [{ card: "intro" }];
    const pending = [...runs];
    for (const a of actions) {
      steps.push(actionStep(a));
      while (pending.length && pending[0].action <= a.action) {
        steps.push({ run: pending.shift().steps }, { wait: "run" });
      }
    }
    for (const r of pending) steps.push({ run: r.steps }, { wait: "run" });
    steps.push({ card: "outro" });
    doc = { video: name, pace: { ...DEFAULT_PACE }, steps };
  }
  const header =
    `# Generated from capture/ on ${new Date().toISOString().slice(0, 10)}; never edited.\n` +
    "# Edit script.yaml; `git diff --no-index script.original.yaml script.yaml` shows every change.\n";
  const original = path.join(src, "script.original.yaml");
  writeFileSync(original, stringifyScript(doc, header), "utf-8");
  const script = path.join(src, "script.yaml");
  if (!existsSync(script)) copyFileSync(original, script);
  return { original, script, runs: runs.length };
}
