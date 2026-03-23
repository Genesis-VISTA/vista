/**
 * @jest-environment node
 */

jest.mock("@/lib/mcp", () => ({
  callMcpRpc: jest.fn(),
  getMcpBaseUrl: jest.fn(() => "http://mock-mcp:8000/mcp"),
}));

import { callMcpRpc } from "@/lib/mcp";
import { POST } from "@/app/api/mcp/call/route";

const mockCallMcpRpc = callMcpRpc as jest.MockedFunction<typeof callMcpRpc>;

beforeEach(() => {
  mockCallMcpRpc.mockReset();
});

function makeRequest(body: unknown): Request {
  return new Request("http://localhost:3000/api/mcp/call", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

describe("POST /api/mcp/call", () => {
  it("returns 400 for invalid JSON body", async () => {
    const req = new Request("http://localhost:3000/api/mcp/call", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: "not-json",
    });
    const res = await POST(req);
    expect(res.status).toBe(400);
    const data = await res.json();
    expect(data.ok).toBe(false);
    expect(data.stderr).toContain("Invalid JSON");
  });

  it("returns 400 for missing tool name", async () => {
    const res = await POST(makeRequest({ args: {} }));
    expect(res.status).toBe(400);
    const data = await res.json();
    expect(data.ok).toBe(false);
    expect(data.stderr).toContain("Missing tool name");
  });

  it("calls MCP and returns normalized result", async () => {
    mockCallMcpRpc.mockResolvedValue({
      response: new Response(null, { status: 200 }),
      text: "",
      json: {
        result: {
          content: [{ type: "text", text: "command output here" }],
        },
      },
    });

    const res = await POST(makeRequest({ tool: "bash", args: { command: "ls" } }));
    expect(res.status).toBe(200);

    const data = await res.json();
    expect(data.ok).toBe(true);
    expect(data.stdout).toContain("command output here");
    expect(data.meta.tool).toBe("bash");
  });

  it("returns 502 when MCP returns HTTP error", async () => {
    mockCallMcpRpc.mockResolvedValue({
      response: new Response("Server Error", { status: 500 }),
      text: "Server Error",
      json: null,
    });

    const res = await POST(makeRequest({ tool: "bash", args: { command: "ls" } }));
    expect(res.status).toBe(502);

    const data = await res.json();
    expect(data.ok).toBe(false);
    expect(data.stderr).toContain("MCP error");
  });

  it("returns 502 when MCP is unreachable", async () => {
    mockCallMcpRpc.mockRejectedValue(new Error("ECONNREFUSED"));

    const res = await POST(makeRequest({ tool: "bash", args: { command: "ls" } }));
    expect(res.status).toBe(502);

    const data = await res.json();
    expect(data.ok).toBe(false);
    expect(data.stderr).toContain("MCP unreachable");
  });

  it("returns 502 on timeout with specific message", async () => {
    const err = new Error("Timeout");
    err.name = "AbortError";
    mockCallMcpRpc.mockRejectedValue(err);

    const res = await POST(makeRequest({ tool: "bash", args: {} }));
    expect(res.status).toBe(502);

    const data = await res.json();
    expect(data.stderr).toContain("timed out");
  });

  it("normalizes RPC error responses", async () => {
    mockCallMcpRpc.mockResolvedValue({
      response: new Response(null, { status: 200 }),
      text: "",
      json: {
        error: { code: -32601, message: "Method not found" },
      },
    });

    const res = await POST(makeRequest({ tool: "unknown_tool", args: {} }));
    const data = await res.json();
    expect(data.ok).toBe(false);
    expect(data.stderr).toContain("Method not found");
  });

  it("extracts HTML UI from resource content", async () => {
    mockCallMcpRpc.mockResolvedValue({
      response: new Response(null, { status: 200 }),
      text: "",
      json: {
        result: {
          content: [
            { type: "text", text: "Some text" },
            {
              type: "resource",
              resource: {
                mimeType: "text/html",
                text: '<img src="data:image/png;base64,abc" />',
              },
            },
          ],
        },
      },
    });

    const res = await POST(makeRequest({ tool: "display_file", args: { uri: "/path/to/img.png" } }));
    const data = await res.json();
    expect(data.ok).toBe(true);
    expect(data.ui?.kind).toBe("html");
    expect(data.ui?.html).toContain("<img");
  });

  it("handles empty args gracefully", async () => {
    mockCallMcpRpc.mockResolvedValue({
      response: new Response(null, { status: 200 }),
      text: "",
      json: { result: { content: [{ type: "text", text: "ok" }] } },
    });

    const res = await POST(makeRequest({ tool: "rag_search" }));
    expect(res.status).toBe(200);
    // callMcpRpc should have been called with empty args
    expect(mockCallMcpRpc).toHaveBeenCalledWith("tools/call", { name: "rag_search", arguments: {} }, 20000);
  });
});
