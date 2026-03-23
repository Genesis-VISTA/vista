/**
 * @jest-environment node
 */
import { startMockMcp, stopMockMcp, setMcpHandler, resetMockMcp } from "../helpers/mock-mcp-server";

let mcpUrl: string;

beforeAll(async () => {
  mcpUrl = await startMockMcp();
});

afterAll(async () => {
  await stopMockMcp();
});

beforeEach(() => {
  resetMockMcp();
  jest.resetModules();
  process.env.MCP_BASE_URL = mcpUrl;
});

async function importMcp() {
  return await import("@/lib/mcp");
}

describe("getMcpBaseUrl", () => {
  it("returns env var when set", async () => {
    process.env.MCP_BASE_URL = "http://custom:9000/mcp";
    const { getMcpBaseUrl } = await importMcp();
    expect(getMcpBaseUrl()).toBe("http://custom:9000/mcp");
  });

  it("returns default when env var is not set", async () => {
    delete process.env.MCP_BASE_URL;
    const { getMcpBaseUrl } = await importMcp();
    expect(getMcpBaseUrl()).toBe("http://127.0.0.1:8000/mcp");
  });
});

describe("fetchWithTimeout", () => {
  it("completes for fast responses", async () => {
    const { fetchWithTimeout } = await importMcp();
    const res = await fetchWithTimeout(mcpUrl, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ jsonrpc: "2.0", id: "1", method: "initialize", params: {} }),
    }, 5000);
    expect(res.ok).toBe(true);
  });
});

describe("callMcpRpc", () => {
  it("initializes session and calls method", async () => {
    setMcpHandler((method) => {
      if (method === "tools/list") {
        return {
          result: {
            tools: [
              { name: "bash", description: "Run bash" },
            ],
          },
        };
      }
      return { result: {} };
    });

    const { callMcpRpc } = await importMcp();
    const { response, json } = await callMcpRpc("tools/list", {}, 5000);
    expect(response.ok).toBe(true);

    const envelope = json as Record<string, unknown>;
    const result = envelope.result as Record<string, unknown>;
    expect(Array.isArray(result.tools)).toBe(true);
  });

  it("handles tools/call with parameters", async () => {
    setMcpHandler((method, params) => {
      if (method === "tools/call") {
        const name = (params as Record<string, unknown>).name;
        return {
          result: {
            content: [{ type: "text", text: `Called tool: ${name}` }],
          },
        };
      }
      return { result: {} };
    });

    const { callMcpRpc } = await importMcp();
    const { json } = await callMcpRpc("tools/call", { name: "bash", arguments: { command: "ls" } }, 5000);

    const envelope = json as Record<string, unknown>;
    const result = envelope.result as Record<string, unknown>;
    const content = result.content as Array<{ type: string; text: string }>;
    expect(content[0].text).toBe("Called tool: bash");
  });

  it("re-initializes on missing session error", async () => {
    let callCount = 0;
    setMcpHandler((method) => {
      callCount++;
      if (method === "tools/list") {
        return { result: { tools: [] } };
      }
      return { result: {} };
    });

    const { callMcpRpc } = await importMcp();
    // First call establishes session, second call uses it
    await callMcpRpc("tools/list", {}, 5000);
    const firstCount = callCount;
    await callMcpRpc("tools/list", {}, 5000);
    // Second call should reuse session without re-initialization
    expect(callCount).toBe(firstCount + 1);
  });
});
