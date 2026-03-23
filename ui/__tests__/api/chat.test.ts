/**
 * @jest-environment node
 */
import {
  startMockOpenAI,
  stopMockOpenAI,
  setCompletion,
  setCompletionSequence,
  makeTextResponse,
  makeToolCallResponse,
  resetMockOpenAI,
  getRequestLog,
  clearRequestLog,
} from "../helpers/mock-openai-server";

// Mock callMcpRpc so we don't need a real MCP server
jest.mock("@/lib/mcp", () => ({
  callMcpRpc: jest.fn(),
  getMcpBaseUrl: jest.fn(() => "http://mock-mcp:8000/mcp"),
}));

// Mock skills so buildSystemPrompt doesn't need real files
jest.mock("@/lib/skills", () => ({
  findSkills: jest.fn(() => []),
  toPrompt: jest.fn(() => ""),
  readProperties: jest.fn(() => ({ name: "test", description: "test skill" })),
  findSkillMd: jest.fn(() => null),
}));

import { callMcpRpc } from "@/lib/mcp";
const mockCallMcpRpc = callMcpRpc as jest.MockedFunction<typeof callMcpRpc>;

let openaiBaseUrl: string;

beforeAll(async () => {
  openaiBaseUrl = await startMockOpenAI();
});

afterAll(async () => {
  await stopMockOpenAI();
});

beforeEach(() => {
  resetMockOpenAI();
  clearRequestLog();
  mockCallMcpRpc.mockReset();

  // Set env for OpenAI mock server
  process.env.OPENAI_API_KEY = "test-key-123";
  process.env.OPENAI_BASE_URL = openaiBaseUrl;
  process.env.OPENAI_MODEL = "test-model";
  delete process.env.AZURE_OPENAI_ENDPOINT;
  delete process.env.AZURE_OPENAI_API_KEY;
  delete process.env.AZURE_OPENAI_DEPLOYMENT_NAME;

  // Default MCP mock: return tools for tools/list
  mockCallMcpRpc.mockImplementation(async (method: string) => {
    if (method === "tools/list") {
      return {
        response: new Response(null, { status: 200 }),
        text: "{}",
        json: {
          result: {
            tools: [
              {
                name: "bash",
                description: "Run a bash command",
                inputSchema: { type: "object", properties: { command: { type: "string" } }, required: ["command"] },
              },
              {
                name: "rag_search",
                description: "Search literature",
                inputSchema: { type: "object", properties: { query: { type: "string" } } },
              },
            ],
          },
        },
      };
    }
    // Default for tools/call
    return {
      response: new Response(null, { status: 200 }),
      text: "",
      json: {
        result: {
          content: [{ type: "text", text: "tool output" }],
        },
      },
    };
  });
});

async function callChat(body: unknown): Promise<Response> {
  // Must re-import to pick up env var changes (getAzureConfig reads them at call time)
  const { POST } = await import("@/app/api/chat/route");
  const request = new Request("http://localhost:3000/api/chat", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  return POST(request);
}

describe("POST /api/chat", () => {
  it("returns 400 for invalid JSON", async () => {
    const { POST } = await import("@/app/api/chat/route");
    const request = new Request("http://localhost:3000/api/chat", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: "not json",
    });
    const res = await POST(request);
    expect(res.status).toBe(400);
    const data = await res.json();
    expect(data.ok).toBe(false);
    expect(data.error).toContain("Invalid JSON");
  });

  it("returns 400 for empty message", async () => {
    const res = await callChat({ message: "" });
    expect(res.status).toBe(400);
    const data = await res.json();
    expect(data.ok).toBe(false);
    expect(data.error).toContain("required");
  });

  it("returns 400 for missing message field", async () => {
    const res = await callChat({ foo: "bar" });
    expect(res.status).toBe(400);
    const data = await res.json();
    expect(data.ok).toBe(false);
  });

  it("returns a simple text response from the model", async () => {
    setCompletion("The melting point of NaCl is 801°C.");

    const res = await callChat({ message: "What is the melting point of NaCl?" });
    expect(res.status).toBe(200);

    const data = await res.json();
    expect(data.ok).toBe(true);
    expect(data.response).toBe("The melting point of NaCl is 801°C.");
    expect(data.toolCalls).toEqual([]);
  });

  it("forwards conversation history to the model", async () => {
    setCompletion("Here is more info.");

    const res = await callChat({
      message: "Tell me more",
      history: [
        { role: "user", content: "What is NaCl?" },
        { role: "assistant", content: "NaCl is sodium chloride." },
      ],
    });
    expect(res.status).toBe(200);

    const log = getRequestLog();
    expect(log.length).toBe(1);
    const messages = log[0].messages as Array<{ role: string; content: string }>;
    // system + 2 history + 1 user = 4
    expect(messages.length).toBe(4);
    expect(messages[0].role).toBe("system");
    expect(messages[1]).toEqual({ role: "user", content: "What is NaCl?" });
    expect(messages[2]).toEqual({ role: "assistant", content: "NaCl is sodium chloride." });
    expect(messages[3]).toEqual({ role: "user", content: "Tell me more" });
  });

  it("executes tool calls and returns results", async () => {
    // First LLM call: request a tool call
    // Second LLM call: return final text
    setCompletionSequence([
      makeToolCallResponse([{ name: "bash", arguments: { command: "echo hello" } }]),
      makeTextResponse("The command output was: hello"),
    ]);

    // Mock MCP tools/call to return tool output
    mockCallMcpRpc.mockImplementation(async (method: string, params?: Record<string, unknown>) => {
      if (method === "tools/list") {
        return {
          response: new Response(null, { status: 200 }),
          text: "",
          json: { result: { tools: [{ name: "bash", description: "Run bash", inputSchema: {} }] } },
        };
      }
      if (method === "tools/call") {
        return {
          response: new Response(null, { status: 200 }),
          text: "",
          json: { result: { content: [{ type: "text", text: "hello\n" }] } },
        };
      }
      return { response: new Response(null, { status: 200 }), text: "", json: {} };
    });

    const res = await callChat({ message: "Run echo hello" });
    expect(res.status).toBe(200);

    const data = await res.json();
    expect(data.ok).toBe(true);
    expect(data.response).toBe("The command output was: hello");
    expect(data.toolCalls.length).toBe(1);
    expect(data.toolCalls[0].tool).toBe("bash");
    expect(data.toolCalls[0].stdout).toBe("hello\n");
  });

  it("returns error when no API key is configured", async () => {
    delete process.env.OPENAI_API_KEY;
    delete process.env.AZURE_OPENAI_ENDPOINT;
    delete process.env.AZURE_OPENAI_API_KEY;

    const res = await callChat({ message: "Hello" });
    expect(res.status).toBe(200);

    const data = await res.json();
    expect(data.ok).toBe(true);
    expect(data.response).toContain("No API key configured");
  });

  it("returns 502 when LLM endpoint is unreachable", async () => {
    process.env.OPENAI_BASE_URL = "http://127.0.0.1:1/v1"; // unreachable port

    const res = await callChat({ message: "Hello" });
    expect(res.status).toBe(502);

    const data = await res.json();
    expect(data.ok).toBe(false);
    expect(data.error).toBeTruthy();
  });

  it("sends tools to the model in OpenAI function-calling format", async () => {
    setCompletion("I see the tools.");

    await callChat({ message: "List tools" });

    const log = getRequestLog();
    expect(log.length).toBe(1);
    expect(log[0].tools).toBeDefined();
    expect(Array.isArray(log[0].tools)).toBe(true);
    const tools = log[0].tools as Array<{ type: string; function: { name: string } }>;
    expect(tools.length).toBeGreaterThan(0);
    expect(tools[0].type).toBe("function");
    expect(tools[0].function.name).toBeTruthy();
  });

  it("detects plot paths in tool output", async () => {
    setCompletionSequence([
      makeToolCallResponse([{ name: "bash", arguments: { command: "python plot.py" } }]),
      makeTextResponse("Here is your phase diagram."),
    ]);

    mockCallMcpRpc.mockImplementation(async (method: string) => {
      if (method === "tools/list") {
        return {
          response: new Response(null, { status: 200 }),
          text: "",
          json: { result: { tools: [{ name: "bash", description: "Run bash", inputSchema: {} }] } },
        };
      }
      if (method === "tools/call") {
        // Check which tool is being called based on context
        return {
          response: new Response(null, { status: 200 }),
          text: "",
          json: {
            result: {
              content: [{ type: "text", text: "Plot saved to /mnt/data/output/salt-plots/NaCl-KCl_phase.png" }],
            },
          },
        };
      }
      return { response: new Response(null, { status: 200 }), text: "", json: {} };
    });

    const res = await callChat({ message: "Show phase diagram for NaCl-KCl" });
    const data = await res.json();
    expect(data.ok).toBe(true);
    expect(data.toolCalls.length).toBeGreaterThanOrEqual(1);
    expect(data.toolCalls[0].plotPath).toBe("/mnt/data/output/salt-plots/NaCl-KCl_phase.png");
  });
});
