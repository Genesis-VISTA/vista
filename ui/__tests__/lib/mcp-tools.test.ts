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

describe("discoverMcpTools", () => {
  it("discovers tools via tools/list", async () => {
    setMcpHandler((method) => {
      if (method === "tools/list") {
        return {
          result: {
            tools: [
              { name: "bash", description: "Run a bash command", inputSchema: { type: "object" } },
              { name: "rag_search", description: "Search literature" },
            ],
          },
        };
      }
      return { result: {} };
    });

    const { discoverMcpTools } = await import("@/lib/mcp-tools");
    const result = await discoverMcpTools(5000);
    expect(result.ok).toBe(true);
    expect(result.tools).toHaveLength(2);
    expect(result.tools[0].name).toBe("bash");
    expect(result.tools[1].name).toBe("rag_search");
  });

  it("returns ok:true with empty tools when server returns none", async () => {
    setMcpHandler((method) => {
      if (method === "tools/list") {
        return { result: { tools: [] } };
      }
      return { result: {} };
    });

    const { discoverMcpTools } = await import("@/lib/mcp-tools");
    const result = await discoverMcpTools(5000);
    expect(result.ok).toBe(true);
    expect(result.tools).toHaveLength(0);
  });

  it("falls back to list_tools if tools/list returns RPC error", async () => {
    setMcpHandler((method) => {
      if (method === "tools/list") {
        return { error: { code: -32601, message: "Method not found" } };
      }
      if (method === "list_tools") {
        return {
          result: {
            tools: [{ name: "fallback_tool", description: "Found via list_tools" }],
          },
        };
      }
      return { result: {} };
    });

    const { discoverMcpTools } = await import("@/lib/mcp-tools");
    const result = await discoverMcpTools(5000);
    expect(result.ok).toBe(true);
    expect(result.tools).toHaveLength(1);
    expect(result.tools[0].name).toBe("fallback_tool");
  });

  it("returns error when all methods fail", async () => {
    setMcpHandler((method) => {
      if (method === "tools/list" || method === "list_tools") {
        return { error: { code: -32601, message: "Method not found" } };
      }
      return { result: {} };
    });

    const { discoverMcpTools } = await import("@/lib/mcp-tools");
    const result = await discoverMcpTools(5000);
    expect(result.ok).toBe(false);
    expect(result.tools).toHaveLength(0);
    expect(result.error).toContain("unavailable");
  });

  it("normalizes tool objects with missing fields", async () => {
    setMcpHandler((method) => {
      if (method === "tools/list") {
        return {
          result: {
            tools: [
              { name: "minimal" },
              { name: "full", description: "Full description", inputSchema: { type: "object", properties: {} } },
              null, // should be filtered
              { noname: true }, // should get "unknown-tool"
            ],
          },
        };
      }
      return { result: {} };
    });

    const { discoverMcpTools } = await import("@/lib/mcp-tools");
    const result = await discoverMcpTools(5000);
    expect(result.ok).toBe(true);
    // null is filtered, noname gets "unknown-tool"
    expect(result.tools.length).toBeGreaterThanOrEqual(2);
    const names = result.tools.map((t) => t.name);
    expect(names).toContain("minimal");
    expect(names).toContain("full");
  });
});
