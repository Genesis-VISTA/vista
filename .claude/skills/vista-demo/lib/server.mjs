// The server the private UI uses as its backend (design D4). In replay mode it
// answers from a video's capture and script and nothing else; capture mode
// (task group 2) will forward to a live backend and record.
import { existsSync, readFileSync } from "node:fs";
import http from "node:http";
import path from "node:path";
import { PORTS } from "./paths.mjs";
import { runEvents } from "./script.mjs";

/** `a=1&b=2`, with keys sorted, so the same query always matches. */
export function normQuery(search) {
  const params = new URLSearchParams(search);
  return [...params.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([k, v]) => `${k}=${v}`).join("&");
}

export const keyOf = (method, pathname, query) => `${method.toUpperCase()} ${pathname}${query ? "?" + query : ""}`;

/** Captured responses from `capture/responses.jsonl`, grouped by request key. */
export function loadResponses(src) {
  const file = path.join(src, "capture", "responses.jsonl");
  const byKey = new Map();
  if (!existsSync(file)) return byKey;
  for (const line of readFileSync(file, "utf-8").split("\n")) {
    if (!line.trim()) continue;
    const r = JSON.parse(line);
    const key = keyOf(r.method, r.path, normQuery(r.query ?? ""));
    if (!byKey.has(key)) byKey.set(key, []);
    byKey.get(key).push(r);
  }
  for (const list of byKey.values()) list.sort((a, b) => a.step - b.step || a.seq - b.seq);
  return byKey;
}

/** The capture of `key` for the player's current step: the latest at or before it, else the earliest. */
export function pickResponse(list, step) {
  if (!list?.length) return null;
  const before = list.filter((r) => r.step <= step);
  return before.length ? before[before.length - 1] : list[0];
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function readJson(req) {
  let raw = "";
  for await (const chunk of req) raw += chunk;
  try {
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

/**
 * Start a replay server for `plan` (from lib/script.mjs) and the responses of
 * `loadResponses`. Resolves to { url, state, stop }. `state` is also served at
 * GET /__demo/state so the player can watch runs and failures.
 *
 * `speed` scales every delay (rehearsal plays runs faster).
 */
export function startReplayServer({ plan, responses, port = PORTS.server, speed = 1, log = () => {} }) {
  const state = { step: 0, runsStarted: 0, runsFinished: 0, errors: [] };
  const fail = (message) => {
    state.errors.push(message);
    log(`replay: ${message}`);
  };

  async function streamRun(res, body, projectName) {
    const index = state.runsStarted;
    const run = plan.runs[index];
    if (!run) {
      fail(`The UI started agent run ${index + 1}, but the script has only ${plan.runs.length} (prompt: ${JSON.stringify(body.user_prompt)})`);
      res.writeHead(500, { "content-type": "application/json" });
      return res.end(JSON.stringify({ detail: "no run left in the script" }));
    }
    state.runsStarted += 1;
    res.writeHead(200, { "content-type": "text/event-stream", "cache-control": "no-cache", connection: "keep-alive" });
    let seq = 0;
    const send = (event, data) => {
      seq += 1;
      res.write(`id: ${seq}\nevent: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
    };
    const runId = crypto.randomUUID();
    send("run_started", {
      event_kind: "run_started",
      run_id: runId,
      user_prompt: body.user_prompt ?? "",
      chat_session_id: body.chat_session_id ?? null,
      project_name: projectName,
    });
    for (const e of runEvents(run.steps, plan.pace)) {
      if (e.delayMs) await sleep(e.delayMs * speed);
      if (res.destroyed) break;
      send(e.event, e.data);
    }
    res.end();
    state.runsFinished += 1;
  }

  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url, "http://replay");
    const method = req.method.toUpperCase();

    if (url.pathname === "/__demo/state") {
      res.writeHead(200, { "content-type": "application/json" });
      return res.end(JSON.stringify(state));
    }
    if (url.pathname === "/__demo/step" && method === "POST") {
      state.step = Number((await readJson(req)).step) || 0;
      res.writeHead(204);
      return res.end();
    }

    const run = url.pathname.match(/^\/projects\/([^/]+)\/agent\/run$/);
    if (run && method === "POST") return streamRun(res, await readJson(req), decodeURIComponent(run[1]));
    // Re-attaching to a run: replayed runs are only ever watched live.
    if (/\/chat-sessions\/[^/]+\/run\/events$/.test(url.pathname)) {
      res.writeHead(204);
      return res.end();
    }

    const key = keyOf(method, url.pathname, normQuery(url.search));
    const hit = pickResponse(responses.get(key), state.step);
    if (!hit) {
      fail(`No captured response for ${key} (at step ${state.step})`);
      res.writeHead(404, { "content-type": "application/json" });
      return res.end(JSON.stringify({ detail: `vista-demo replay has no response for ${key}` }));
    }
    const type = hit.type ?? "application/json";
    res.writeHead(hit.status ?? 200, { "content-type": type });
    res.end(typeof hit.body === "string" ? hit.body : JSON.stringify(hit.body));
  });

  return new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(port, "127.0.0.1", () =>
      resolve({
        url: `http://127.0.0.1:${server.address().port}`,
        state,
        stop: () => new Promise((r) => server.close(() => r())),
      }),
    );
  });
}
