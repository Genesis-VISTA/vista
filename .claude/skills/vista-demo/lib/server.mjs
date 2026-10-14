// The server the private UI uses as its backend (design D4).
//
// replay: answers from a video's capture and script, and nothing else.
// capture: forwards to a live backend and records every answer.
//
// Both tie answers to the *action count*: how many UI actions (goto, click,
// type, press) have been done so far. The player, or the capture recorder,
// reports it at POST /__demo/action. Captions, pauses and camera moves don't
// count, so they can be added or removed without touching responses.jsonl.
import { appendFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import http from "node:http";
import path from "node:path";
import { PORTS } from "./paths.mjs";
import { runEvents } from "./script.mjs";
import { scrubHeaders, scrubValue } from "./scrub.mjs";

/** `a=1&b=2`, with keys sorted, so the same query always matches. */
export function normQuery(search) {
  const params = new URLSearchParams(search);
  return [...params.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([k, v]) => `${k}=${v}`).join("&");
}

export const keyOf = (method, pathname, query) => `${method.toUpperCase()} ${pathname}${query ? "?" + query : ""}`;

const AGENT_RUN = /^\/projects\/([^/]+)\/agent\/run$/;

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
  for (const list of byKey.values()) list.sort((a, b) => (a.action ?? 0) - (b.action ?? 0) || a.seq - b.seq);
  return byKey;
}

/** The capture for the current action count: the latest at or before it, else the earliest. */
export function pickResponse(list, action) {
  if (!list?.length) return null;
  const before = list.filter((r) => (r.action ?? 0) <= action);
  return before.length ? before[before.length - 1] : list[0];
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function readBody(req) {
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  return Buffer.concat(chunks);
}
const parseJson = (buf) => {
  try {
    return buf.length ? JSON.parse(buf.toString("utf-8")) : {};
  } catch {
    return {};
  }
};

function listen(server, port, state) {
  return new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(port, "127.0.0.1", () =>
      resolve({
        url: `http://127.0.0.1:${server.address().port}`,
        state,
        stop: () =>
          new Promise((r) => {
            server.closeAllConnections?.();
            server.close(() => r());
          }),
      }),
    );
  });
}

/** Shared /__demo endpoints. Returns true when it answered. */
async function demoEndpoint(req, res, url, state) {
  if (url.pathname === "/__demo/state") {
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify(state));
    return true;
  }
  if (url.pathname === "/__demo/action" && req.method === "POST") {
    state.action = Number(parseJson(await readBody(req)).action) || 0;
    res.writeHead(204);
    res.end();
    return true;
  }
  return false;
}

/**
 * Start a replay server for `plan` (lib/script.mjs) and `responses`
 * (loadResponses). Resolves to { url, state, stop }. `speed` scales every
 * delay (rehearsal plays runs faster).
 */
export function startReplayServer({ plan, responses, port = PORTS.server, speed = 1, log = () => {} }) {
  const state = { mode: "replay", action: 0, runsStarted: 0, runsFinished: 0, errors: [] };
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
    send("run_started", {
      event_kind: "run_started",
      run_id: crypto.randomUUID(),
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
    if (await demoEndpoint(req, res, url, state)) return;

    const run = url.pathname.match(AGENT_RUN);
    if (run && method === "POST") return streamRun(res, parseJson(await readBody(req)), decodeURIComponent(run[1]));
    // Re-attaching to a run: replayed runs are only ever watched live.
    if (/\/chat-sessions\/[^/]+\/run\/events$/.test(url.pathname)) {
      res.writeHead(204);
      return res.end();
    }

    const key = keyOf(method, url.pathname, normQuery(url.search));
    const hit = pickResponse(responses.get(key), state.action);
    if (!hit) {
      fail(`No captured response for ${key} (after action ${state.action})`);
      res.writeHead(404, { "content-type": "application/json" });
      return res.end(JSON.stringify({ detail: `vista-demo replay has no response for ${key}` }));
    }
    res.writeHead(hit.status ?? 200, { "content-type": hit.type ?? "application/json" });
    if (hit.encoding === "base64") res.end(Buffer.from(hit.body, "base64"));
    else res.end(typeof hit.body === "string" ? hit.body : JSON.stringify(hit.body));
  });

  return listen(server, port, state);
}

/** Split an SSE text buffer into complete blocks; returns [blocks, rest]. */
function sseBlocks(buffer) {
  const parts = buffer.split(/\r?\n\r?\n/);
  const rest = parts.pop();
  return [parts, rest];
}
function parseSseBlock(block) {
  let event = null;
  const data = [];
  for (const line of block.split(/\r?\n/)) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
  }
  if (!data.length) return null;
  try {
    const parsed = JSON.parse(data.join("\n"));
    return { event: event ?? parsed?.event_kind ?? null, data: parsed };
  } catch {
    return null;
  }
}

/**
 * Start a capture server in front of a live backend. Everything is written
 * under `<src>/capture/`, scrubbed first:
 *
 *   responses.jsonl  one line per answer: seq, action, method, path, query, status, type, body
 *   runs/<n>.jsonl   per agent run: a header line { run, action, prompt, started_at },
 *                    then { t, event, data } per event, t in seconds since the run began
 *
 * Requests under /mcp go to `mcpUrl`; the rest to `backendUrl`.
 */
export function startCaptureServer({ src, backendUrl, mcpUrl, port = PORTS.server, log = () => {} }) {
  const dir = path.join(src, "capture");
  mkdirSync(path.join(dir, "runs"), { recursive: true });
  const responsesFile = path.join(dir, "responses.jsonl");
  writeFileSync(responsesFile, "", { encoding: "utf-8", flag: "a" });
  const state = { mode: "capture", action: 0, runsStarted: 0, runsFinished: 0, errors: [], seq: 0 };

  const target = (pathname) => {
    if (mcpUrl && (pathname === "/mcp" || pathname.startsWith("/mcp/"))) {
      const base = new URL(mcpUrl);
      return new URL(base.pathname.replace(/\/mcp\/?$/, "") + pathname, base.origin);
    }
    return new URL(pathname, backendUrl);
  };

  function record(entry) {
    state.seq += 1;
    appendFileSync(responsesFile, JSON.stringify({ seq: state.seq, action: state.action, ...entry }) + "\n", "utf-8");
  }

  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url, "http://capture");
    const method = req.method.toUpperCase();
    if (await demoEndpoint(req, res, url, state)) return;

    const arrived = Date.now(); // `at`: lets finishCapture order this after the action that caused it
    const body = await readBody(req);
    const upstreamUrl = target(url.pathname);
    upstreamUrl.search = url.search;
    const headers = Object.fromEntries(
      Object.entries(req.headers).filter(([k]) => !["host", "connection", "content-length"].includes(k)),
    );
    let upstream;
    try {
      upstream = await fetch(upstreamUrl, {
        method,
        headers,
        body: method === "GET" || method === "HEAD" ? undefined : body,
        redirect: "manual",
      });
    } catch (e) {
      state.errors.push(`Backend unreachable for ${method} ${url.pathname}: ${e.message}`);
      res.writeHead(502, { "content-type": "application/json" });
      return res.end(JSON.stringify({ detail: `capture: backend unreachable: ${e.message}` }));
    }
    const type = upstream.headers.get("content-type") ?? "";
    const outHeaders = scrubHeaders(Object.fromEntries(upstream.headers.entries()));
    delete outHeaders["content-encoding"];
    delete outHeaders["content-length"];
    delete outHeaders["transfer-encoding"];

    const isRun = AGENT_RUN.test(url.pathname) && method === "POST";
    if (type.includes("text/event-stream") && upstream.body) {
      res.writeHead(upstream.status, outHeaders);
      if (!isRun) {
        // Other streams (re-attach, debates) pass through unrecorded: replay never needs them.
        for await (const chunk of upstream.body) res.write(chunk);
        return res.end();
      }
      const n = ++state.runsStarted;
      const runFile = path.join(dir, "runs", `${n}.jsonl`);
      const t0 = Date.now();
      const prompt = parseJson(body).user_prompt ?? "";
      writeFileSync(runFile, JSON.stringify({ run: n, action: state.action, at: arrived, prompt, started_at: new Date(t0).toISOString() }) + "\n", "utf-8");
      log(`capture: run ${n} started (${JSON.stringify(String(prompt).slice(0, 60))})`);
      const decoder = new TextDecoder();
      let buffer = "";
      const take = (text) => {
        buffer += text;
        const [blocks, rest] = sseBlocks(buffer);
        buffer = rest;
        for (const b of blocks) {
          const e = parseSseBlock(b);
          if (e) {
            const line = { t: Number(((Date.now() - t0) / 1000).toFixed(3)), event: e.event, data: scrubValue(e.data) };
            appendFileSync(runFile, JSON.stringify(line) + "\n", "utf-8");
          }
        }
      };
      try {
        for await (const chunk of upstream.body) {
          res.write(chunk);
          take(decoder.decode(chunk, { stream: true }));
        }
        take(decoder.decode() + "\n\n");
      } finally {
        res.end();
        state.runsFinished += 1;
        log(`capture: run ${n} finished`);
      }
      return;
    }

    const bytes = Buffer.from(await upstream.arrayBuffer());
    res.writeHead(upstream.status, outHeaders);
    res.end(bytes);
    const entry = { at: arrived, method, path: url.pathname, query: normQuery(url.search), status: upstream.status, type: type || null };
    if (type.includes("json")) {
      let parsed;
      try {
        parsed = JSON.parse(bytes.toString("utf-8"));
      } catch {
        parsed = bytes.toString("utf-8");
      }
      record({ ...entry, body: scrubValue(parsed) });
    } else if (/^text\/|xml|javascript/.test(type) || !bytes.length) {
      record({ ...entry, body: bytes.toString("utf-8") });
    } else {
      record({ ...entry, encoding: "base64", body: bytes.toString("base64") });
    }
  });

  return listen(server, port, state);
}
