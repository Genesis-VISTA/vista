import assert from "node:assert/strict";
import { test } from "node:test";
import { keyOf, normQuery, pickResponse, startReplayServer } from "../lib/server.mjs";
import { parseScript } from "../lib/script.mjs";

const plan = parseScript(`
pace: { stream_wps: 1000, tool_min_s: 0, think_s: 0 }
steps:
  - run:
      - answer: first phase diagram
  - run:
      - answer: second phase diagram
`);

function responses(rows) {
  const map = new Map();
  for (const r of rows) {
    const key = keyOf(r.method, r.path, normQuery(r.query ?? ""));
    if (!map.has(key)) map.set(key, []);
    map.get(key).push(r);
  }
  return map;
}

async function answerOf(url, prompt) {
  const res = await fetch(`${url}/projects/p/agent/run`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ user_prompt: prompt }),
  });
  const text = await res.text();
  const end = text.split("\n\n").find((b) => b.includes("event: part_end"));
  return JSON.parse(end.split("data: ")[1]).part.content;
}

test("runs are served in script order, not by matching prompts", async () => {
  const server = await startReplayServer({ plan, responses: new Map(), port: 0 });
  try {
    assert.equal(await answerOf(server.url, "show me the phase diagram"), "first phase diagram");
    assert.equal(await answerOf(server.url, "show me the phase diagram"), "second phase diagram");
    assert.equal(server.state.runsFinished, 2);
  } finally {
    await server.stop();
  }
});

test("an unanswered request fails, naming its method and path", async () => {
  const server = await startReplayServer({ plan, responses: new Map(), port: 0 });
  try {
    const res = await fetch(`${server.url}/projects/molten-salt/uploads`);
    assert.equal(res.status, 404);
    const state = await (await fetch(`${server.url}/__demo/state`)).json();
    assert.match(state.errors[0], /GET \/projects\/molten-salt\/uploads/);
  } finally {
    await server.stop();
  }
});

test("one run too many is an error, not a hang", async () => {
  const server = await startReplayServer({ plan, responses: new Map(), port: 0 });
  try {
    await answerOf(server.url, "a");
    await answerOf(server.url, "b");
    const res = await fetch(`${server.url}/projects/p/agent/run`, { method: "POST", body: "{}" });
    assert.equal(res.status, 500);
    assert.match(server.state.errors[0], /only 2/);
  } finally {
    await server.stop();
  }
});

test("a response is picked by the action count", async () => {
  const rows = [
    { seq: 1, action: 0, method: "GET", path: "/projects/p/chat-sessions", body: [] },
    { seq: 2, action: 4, method: "GET", path: "/projects/p/chat-sessions", body: [{ id: "s1" }] },
  ];
  const server = await startReplayServer({ plan, responses: responses(rows), port: 0 });
  try {
    const list = async () => (await fetch(`${server.url}/projects/p/chat-sessions`)).json();
    assert.deepEqual(await list(), []);
    await fetch(`${server.url}/__demo/action`, { method: "POST", body: JSON.stringify({ action: 5 }) });
    assert.deepEqual(await list(), [{ id: "s1" }]);
  } finally {
    await server.stop();
  }
});

test("a conversation id the replay hands out is the one later requests carry", async () => {
  const S1 = "6f1c2b9e-0000-4000-8000-0000000000a1";
  const rows = [
    { seq: 1, action: 3, method: "POST", path: "/projects/p/chat-sessions", body: { id: S1, title: "New conversation" } },
    { seq: 2, action: 3, method: "GET", path: "/projects/p/chat-session", query: `chat_session_id=${S1}`, body: { id: S1, messages: [] } },
    { seq: 3, action: 3, method: "GET", path: "/projects/p/campaigns", query: `chat_session_id=${S1}`, body: [] },
  ];
  const server = await startReplayServer({ plan, responses: responses(rows), port: 0 });
  try {
    await fetch(`${server.url}/__demo/action`, { method: "POST", body: JSON.stringify({ action: 3 }) });
    // the UI takes the id from the creation response and asks with it
    const { id } = await (await fetch(`${server.url}/projects/p/chat-sessions`, { method: "POST", body: "{}" })).json();
    assert.equal(id, S1);
    for (const p of ["chat-session", "campaigns"]) {
      const res = await fetch(`${server.url}/projects/p/${p}?chat_session_id=${id}`);
      assert.equal(res.status, 200, p);
    }
    assert.deepEqual(server.state.errors, []);
  } finally {
    await server.stop();
  }
});

test("queries match regardless of parameter order", () => {
  assert.equal(normQuery("?b=2&a=1"), "a=1&b=2");
  assert.equal(pickResponse([{ action: 3, seq: 1 }], 0).action, 3);
});
