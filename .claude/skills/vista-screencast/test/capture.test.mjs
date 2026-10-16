import assert from "node:assert/strict";
import { mkdtempSync, readFileSync } from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { startCaptureServer } from "../lib/server.mjs";
import { SCRUBBED } from "../lib/scrub.mjs";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const EVENTS = [
  ["run_started", { event_kind: "run_started", run_id: "r1", user_prompt: "hi" }],
  ["function_tool_call", { event_kind: "function_tool_call", part: { tool_name: "run_bash", args: { command: "ls" }, tool_call_id: "c1" } }],
  ["function_tool_result", { event_kind: "function_tool_result", part: { part_kind: "tool-return", tool_name: "run_bash", content: "a\nb", tool_call_id: "c1" } }],
  ["part_start", { event_kind: "part_start", index: 0, part: { part_kind: "text", content: "" } }],
  ["part_delta", { event_kind: "part_delta", index: 0, delta: { part_delta_kind: "text", content_delta: "Two " } }],
  ["part_delta", { event_kind: "part_delta", index: 0, delta: { part_delta_kind: "text", content_delta: "files." } }],
  ["part_end", { event_kind: "part_end", index: 0, part: { part_kind: "text", content: "Two files." } }],
  ["run_finished", { event_kind: "run_finished", state: "done" }],
];
const sse = (event, data, id) => `id: ${id}\nevent: ${event}\ndata: ${JSON.stringify(data)}\n\n`;

/** A stand-in for vista-backend (and the MCP server, under /mcp). */
function stubUpstream() {
  const seen = [];
  const server = http.createServer(async (req, res) => {
    seen.push(`${req.method} ${req.url}`);
    if (req.url.endsWith("/agent/run")) {
      res.writeHead(200, { "content-type": "text/event-stream" });
      for (const [i, [event, data]] of EVENTS.entries()) {
        res.write(sse(event, data, i + 1));
        await sleep(20);
      }
      return res.end();
    }
    if (req.url.startsWith("/users/me")) {
      res.writeHead(200, { "content-type": "application/json", "set-cookie": "s=secret" });
      return res.end(JSON.stringify({ email: "r@example.org", inference_api_key: "sk-not-real-0123456789abcdef" }));
    }
    if (req.url.startsWith("/mcp")) {
      res.writeHead(200, { "content-type": "application/json" });
      return res.end(JSON.stringify({ from: "mcp" }));
    }
    res.writeHead(200, { "content-type": "application/pdf" });
    res.end(Buffer.from([0x25, 0x50, 0x44, 0x46, 0x00, 0xff]));
  });
  return new Promise((r) => server.listen(0, "127.0.0.1", () => r({ url: `http://127.0.0.1:${server.address().port}`, seen, close: () => server.close() })));
}

test("a streamed run is forwarded unchanged and recorded event by event", async () => {
  const up = await stubUpstream();
  const src = mkdtempSync(path.join(os.tmpdir(), "vista-screencast-capture-"));
  const cap = await startCaptureServer({ src, backendUrl: up.url, mcpUrl: `${up.url}/mcp`, port: 0 });
  try {
    await fetch(`${cap.url}/__screencast/action`, { method: "POST", body: JSON.stringify({ action: 4 }) });
    const res = await fetch(`${cap.url}/projects/p/agent/run`, { method: "POST", body: JSON.stringify({ user_prompt: "list files" }) });
    const text = await res.text();
    assert.equal(text, EVENTS.map(([e, d], i) => sse(e, d, i + 1)).join(""), "the UI gets the stream byte for byte");

    const lines = readFileSync(path.join(src, "capture", "runs", "1.jsonl"), "utf-8").trim().split("\n").map((l) => JSON.parse(l));
    assert.deepEqual(lines[0].prompt, "list files");
    assert.equal(lines[0].action, 4);
    assert.deepEqual(lines.slice(1).map((l) => l.event), EVENTS.map(([e]) => e));
    const times = lines.slice(1).map((l) => l.t);
    assert.ok(times.every((t, i) => i === 0 || t >= times[i - 1]), "arrival times are in order");
    assert.ok(times.at(-1) > times[0], "and spread out");
    assert.equal(cap.state.runsFinished, 1);
  } finally {
    await cap.stop();
    up.close();
  }
});

test("responses are recorded with the action count, scrubbed, and binaries kept", async () => {
  const up = await stubUpstream();
  const src = mkdtempSync(path.join(os.tmpdir(), "vista-screencast-capture-"));
  const cap = await startCaptureServer({ src, backendUrl: up.url, mcpUrl: `${up.url}/mcp`, port: 0 });
  try {
    const me = await (await fetch(`${cap.url}/users/me?config=true`, { headers: { cookie: "x=1" } })).json();
    assert.equal(me.inference_api_key, "sk-not-real-0123456789abcdef", "the live UI still sees real values");
    await fetch(`${cap.url}/__screencast/action`, { method: "POST", body: JSON.stringify({ action: 2 }) });
    await (await fetch(`${cap.url}/knowledge-bases/kb/publications/a.pdf`)).arrayBuffer();
    assert.deepEqual(await (await fetch(`${cap.url}/mcp`, { method: "POST", body: "{}" })).json(), { from: "mcp" });

    const rows = readFileSync(path.join(src, "capture", "responses.jsonl"), "utf-8").trim().split("\n").map((l) => JSON.parse(l));
    const user = rows.find((r) => r.path === "/users/me");
    assert.equal(user.action, 0);
    assert.equal(user.query, "config=true");
    assert.equal(user.body.inference_api_key, SCRUBBED);
    const pdf = rows.find((r) => r.path.endsWith(".pdf"));
    assert.equal(pdf.action, 2);
    assert.equal(pdf.encoding, "base64");
    assert.deepEqual([...Buffer.from(pdf.body, "base64")], [0x25, 0x50, 0x44, 0x46, 0x00, 0xff]);
    assert.ok(up.seen.includes("POST /mcp"));
  } finally {
    await cap.stop();
    up.close();
  }
});
