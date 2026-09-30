import { describe, expect, it } from "vitest";
import {
  createRunRenderer,
  FINISH_NOTES,
  type RunHost,
  type RunPrompt,
} from "@/lib/run-renderer";
import { readSseStream, type SseEvent } from "@/lib/run-stream";
import type { ChatMessage, ExecutionResult } from "@/lib/types";

/** A host that keeps the page's state in plain variables. */
function makeHost(initial: ChatMessage[] = []) {
  const state = {
    messages: initial,
    latestIntermediateId: null as string | null,
    liveStatus: null as string | null,
    latestResult: null as ExecutionResult | null,
    logs: [] as string[],
    prompts: [] as RunPrompt[],
    resolved: [] as string[],
  };
  const host: RunHost = {
    setMessages: (update) => {
      state.messages = update(state.messages);
    },
    setLatestIntermediateId: (update) => {
      state.latestIntermediateId = update(state.latestIntermediateId);
    },
    setLiveStatus: (status) => {
      state.liveStatus = status;
    },
    setLatestResult: (update) => {
      state.latestResult = update(state.latestResult);
    },
    pushLog: (_level, _area, message) => {
      state.logs.push(message);
    },
    prompt: (prompt) => {
      state.prompts.push(prompt);
    },
    promptResolved: (id) => {
      state.resolved.push(id);
    },
    scrollToLatest: () => {},
  };
  return { state, host };
}

function counter() {
  let n = 0;
  return () => `id-${++n}`;
}

const runStarted = (runId = "run-1", prompt = "What is FLiBe?") => ({
  event_kind: "run_started",
  run_id: runId,
  user_prompt: prompt,
});

/** The events of a turn: one tool call, then a streamed answer. */
function turn(dispatch: (name: string, data: unknown) => void, answer = ["FLiBe is ", "a salt."]) {
  dispatch("function_tool_call", {
    event_kind: "function_tool_call",
    part: { part_kind: "tool-call", tool_name: "rag_search", args: "{}" },
  });
  dispatch("function_tool_result", {
    event_kind: "function_tool_result",
    part: { part_kind: "tool-return", tool_name: "rag_search", content: "3 passages" },
  });
  dispatch("final_result", { event_kind: "final_result" });
  dispatch("part_start", {
    event_kind: "part_start",
    index: 0,
    part: { part_kind: "text", content: "" },
  });
  for (const chunk of answer) {
    dispatch("part_delta", {
      event_kind: "part_delta",
      index: 0,
      delta: { part_delta_kind: "text", content_delta: chunk },
    });
  }
  dispatch("part_end", {
    event_kind: "part_end",
    index: 0,
    part: { part_kind: "text", content: answer.join("") },
  });
  dispatch("agent_run_result", { event_kind: "agent_run_result", result: { new_messages: [] } });
}

describe("run renderer: a live send", () => {
  it("tags the bubble the page drew, and every bubble after it, with the run's id", () => {
    const user: ChatMessage = { id: "u1", role: "user", content: "What is FLiBe?" };
    const { state, host } = makeHost([user]);
    const renderer = createRunRenderer(host, { userBubbleId: "u1", newId: counter() });

    renderer.dispatch("run_started", runStarted("run-1"));
    turn((name, data) => renderer.dispatch(name, data));

    expect(renderer.runId).toBe("run-1");
    expect(state.messages.every((m) => m.run_id === "run-1")).toBe(true);
    expect(state.messages.filter((m) => m.role === "user")).toHaveLength(1);
    const answer = state.messages.find((m) => m.content === "FLiBe is a salt.");
    expect(answer).toMatchObject({ role: "assistant", intermediate: false });
    expect(state.messages.find((m) => m.content.startsWith("Calling"))?.intermediate).toBe(true);
  });

  it("shows what the agent is doing, and clears it when the answer lands", () => {
    const { state, host } = makeHost();
    const renderer = createRunRenderer(host, { newId: counter() });
    renderer.dispatch("function_tool_call", {
      part: { part_kind: "tool-call", tool_name: "rag_search", args: "{}" },
    });
    expect(state.liveStatus).toBe("Searching the literature");
    renderer.dispatch("agent_run_result", { result: { new_messages: [] } });
    expect(state.liveStatus).toBeNull();
    expect(renderer.sawResult).toBe(true);
  });

  it("puts a figure from display_file into the latest result", () => {
    const { state, host } = makeHost();
    const renderer = createRunRenderer(host, { newId: counter() });
    renderer.dispatch("function_tool_result", {
      part: {
        part_kind: "tool-return",
        tool_name: "rag_search",
        content: "3 passages",
      },
    });
    expect(state.latestResult).toMatchObject({ ok: true, stdout: "3 passages" });
  });
});

describe("run renderer: drawing a run the page did not start", () => {
  it("draws the researcher's question from run_started", () => {
    const { state, host } = makeHost();
    const renderer = createRunRenderer(host, { drawUserPrompt: true, newId: counter() });

    renderer.dispatch("run_started", runStarted("run-1", "What is FLiBe?"));
    turn((name, data) => renderer.dispatch(name, data));

    expect(state.messages[0]).toMatchObject({
      role: "user",
      content: "What is FLiBe?",
      run_id: "run-1",
    });
    expect(state.messages.map((m) => m.role)).toEqual(["user", "assistant", "assistant"]);
  });

  it("redraws the run from scratch, so bubbles saved from an earlier watch are not doubled", () => {
    const saved: ChatMessage[] = [
      { id: "old-0", role: "user", content: "An earlier turn" },
      { id: "old-1", role: "assistant", content: "Its answer", run_id: "run-0" },
      { id: "old-2", role: "user", content: "What is FLiBe?", run_id: "run-1" },
      { id: "old-3", role: "assistant", content: "FLiBe is", run_id: "run-1", intermediate: true },
    ];
    const { state, host } = makeHost(saved);
    const renderer = createRunRenderer(host, { drawUserPrompt: true, newId: counter() });

    renderer.dispatch("run_started", runStarted("run-1"));
    turn((name, data) => renderer.dispatch(name, data));

    const questions = state.messages.filter((m) => m.content === "What is FLiBe?");
    expect(questions).toHaveLength(1);
    expect(state.messages.filter((m) => m.content === "FLiBe is a salt.")).toHaveLength(1);
    expect(state.messages.some((m) => m.id.startsWith("old-") && m.run_id === "run-1")).toBe(false);
    expect(state.messages.slice(0, 2).map((m) => m.content)).toEqual([
      "An earlier turn",
      "Its answer",
    ]);
  });

  it("does not double an untagged question left by a send that died before run_started", () => {
    const saved: ChatMessage[] = [{ id: "u", role: "user", content: "What is FLiBe?" }];
    const { state, host } = makeHost(saved);
    const renderer = createRunRenderer(host, { drawUserPrompt: true, newId: counter() });

    renderer.dispatch("run_started", runStarted("run-1", "What is FLiBe?"));

    expect(state.messages).toHaveLength(1);
    expect(state.messages[0].run_id).toBe("run-1");
  });

  it("keeps an earlier turn that asked the same question", () => {
    const saved: ChatMessage[] = [
      { id: "a", role: "user", content: "What is FLiBe?", run_id: "run-0" },
      { id: "b", role: "assistant", content: "A salt.", run_id: "run-0" },
    ];
    const { state, host } = makeHost(saved);
    const renderer = createRunRenderer(host, { drawUserPrompt: true, newId: counter() });

    renderer.dispatch("run_started", runStarted("run-1", "What is FLiBe?"));

    expect(state.messages).toHaveLength(3);
  });
});

describe("run renderer: how a run ends", () => {
  it("adds nothing when the turn finished normally", () => {
    const { state, host } = makeHost();
    const renderer = createRunRenderer(host, { newId: counter() });
    renderer.dispatch("run_finished", { event_kind: "run_finished", state: "done" });
    expect(state.messages).toEqual([]);
    expect(renderer.finishedState).toBe("done");
  });

  it.each([
    ["stopped", "Stopped"],
    ["interrupted", "Interrupted when VISTA quit"],
    ["failed", "Agent run failed"],
  ] as const)("marks a %s turn in the transcript", (state, note) => {
    const page = makeHost();
    const renderer = createRunRenderer(page.host, { drawUserPrompt: true, newId: counter() });
    renderer.dispatch("run_started", runStarted("run-1"));
    renderer.dispatch("run_finished", { event_kind: "run_finished", state });

    const last = page.state.messages[page.state.messages.length - 1];
    expect(last).toMatchObject({ role: "system", content: note, run_id: "run-1" });
    expect(FINISH_NOTES[state]).toBe(note);
    expect(page.state.liveStatus).toBeNull();
    expect(renderer.finishedState).toBe(state);
  });

  it("ignores a finish state it does not know", () => {
    const { state, host } = makeHost();
    const renderer = createRunRenderer(host, { newId: counter() });
    renderer.dispatch("run_finished", { state: "mystery" });
    expect(renderer.finishedState).toBeNull();
    expect(state.messages).toEqual([]);
  });
});

describe("run renderer: prompts", () => {
  it("hands a form elicitation, like the Lux SSH login, to the page", () => {
    const { state, host } = makeHost();
    const renderer = createRunRenderer(host, { newId: counter() });
    const schema = {
      type: "object",
      properties: { username: { type: "string" }, password: { type: "string" } },
    };
    renderer.dispatch("mcp_form_elicitation", {
      event_kind: "mcp_form_elicitation",
      mode: "form",
      elicitation_id: "e1",
      message: "SSH login for Lux",
      requested_schema: schema,
    });
    expect(state.prompts).toEqual([
      { kind: "form", id: "e1", message: "SSH login for Lux", schema },
    ]);
  });

  it("hands over a URL elicitation and a tool approval", () => {
    const { state, host } = makeHost();
    const renderer = createRunRenderer(host, { newId: counter() });
    renderer.dispatch("mcp_url_elicitation", {
      elicitation_id: "e2",
      message: "Open Globus",
      url: "https://globus.example/auth",
    });
    renderer.dispatch("mcp_tool_approval", {
      elicitation_id: "e3",
      tool_name: "submit_job",
      message: "Approve call to 'submit_job'?",
      args: { nodes: 4 },
      decision_metadata: { tier: "fast" },
    });
    expect(state.prompts.map((p) => [p.kind, p.id])).toEqual([
      ["url", "e2"],
      ["approval", "e3"],
    ]);
    expect(state.prompts[1]).toMatchObject({
      toolName: "submit_job",
      args: { nodes: 4 },
      decisionMetadata: { tier: "fast" },
    });
  });

  it("tells the page when another view answered a prompt", () => {
    const { state, host } = makeHost();
    const renderer = createRunRenderer(host, { newId: counter() });
    renderer.dispatch("prompt_resolved", { event_kind: "prompt_resolved", elicitation_id: "e1" });
    expect(state.resolved).toEqual(["e1"]);
  });
});

describe("readSseStream", () => {
  function streamOf(...chunks: string[]): ReadableStream<Uint8Array> {
    const encoder = new TextEncoder();
    return new ReadableStream({
      start(controller) {
        for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
        controller.close();
      },
    });
  }

  async function read(...chunks: string[]): Promise<SseEvent[]> {
    const events: SseEvent[] = [];
    await readSseStream(streamOf(...chunks), (e) => events.push(e));
    return events;
  }

  it("reads the event name, the id and the JSON data", async () => {
    const events = await read('id: 4\nevent: log\ndata: {"a":1}\n\n');
    expect(events).toEqual([{ event: "log", id: "4", data: { a: 1 } }]);
  });

  it("handles an event split across chunks, with CRLF line ends", async () => {
    const events = await read('event: lo', 'g\r\ndata: {"a"', ":1}\r\n\r\n", "event: x\ndata: {}\n\n");
    expect(events.map((e) => e.event)).toEqual(["log", "x"]);
    expect(events[0].data).toEqual({ a: 1 });
  });

  it("skips keep-alive comments and drops blocks that are not JSON", async () => {
    const events = await read(": ping\n\nevent: bad\ndata: nope\n\nevent: ok\ndata: {}\n\n");
    expect(events.map((e) => e.event)).toEqual(["ok"]);
  });

  it("flushes a trailing block that has no closing blank line", async () => {
    const events = await read("event: last\ndata: {}");
    expect(events.map((e) => e.event)).toEqual(["last"]);
  });

  it("rejects when the stream errors, so the caller can tell it from a clean end", async () => {
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.error(new Error("aborted"));
      },
    });
    await expect(readSseStream(stream, () => {})).rejects.toThrow("aborted");
  });
});
