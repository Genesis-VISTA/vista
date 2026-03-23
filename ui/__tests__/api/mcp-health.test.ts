/**
 * @jest-environment node
 */

jest.mock("@/lib/mcp", () => ({
  callMcpRpc: jest.fn(),
  getMcpBaseUrl: jest.fn(() => "http://mock-mcp:8000/mcp"),
}));

import { callMcpRpc, getMcpBaseUrl } from "@/lib/mcp";
import { GET } from "@/app/api/mcp/health/route";

const mockCallMcpRpc = callMcpRpc as jest.MockedFunction<typeof callMcpRpc>;
const mockGetMcpBaseUrl = getMcpBaseUrl as jest.MockedFunction<typeof getMcpBaseUrl>;

beforeEach(() => {
  mockCallMcpRpc.mockReset();
  mockGetMcpBaseUrl.mockReturnValue("http://mock-mcp:8000/mcp");
});

describe("GET /api/mcp/health", () => {
  it("returns ok:true when MCP server is healthy", async () => {
    mockCallMcpRpc.mockResolvedValue({
      response: new Response(null, { status: 200 }),
      text: '{"result":{"tools":[]}}',
      json: { result: { tools: [] } },
    });

    const res = await GET();
    expect(res.status).toBe(200);

    const data = await res.json();
    expect(data.ok).toBe(true);
    expect(data.mcpBaseUrl).toBe("http://mock-mcp:8000/mcp");
  });

  it("returns ok:false when MCP returns non-200", async () => {
    mockCallMcpRpc.mockResolvedValue({
      response: new Response(null, { status: 503 }),
      text: "Service Unavailable",
      json: null,
    });

    const res = await GET();
    const data = await res.json();
    expect(data.ok).toBe(false);
    expect(data.detail).toContain("503");
  });

  it("returns ok:false when MCP is unreachable (timeout)", async () => {
    const err = new Error("Timeout");
    err.name = "AbortError";
    mockCallMcpRpc.mockRejectedValue(err);

    const res = await GET();
    const data = await res.json();
    expect(data.ok).toBe(false);
    expect(data.detail).toContain("timed out");
  });

  it("returns ok:false on generic errors", async () => {
    mockCallMcpRpc.mockRejectedValue(new Error("ECONNREFUSED"));

    const res = await GET();
    const data = await res.json();
    expect(data.ok).toBe(false);
    expect(data.detail).toContain("ECONNREFUSED");
  });

  it("always includes mcpBaseUrl", async () => {
    mockGetMcpBaseUrl.mockReturnValue("http://custom:9000/mcp");
    mockCallMcpRpc.mockRejectedValue(new Error("fail"));

    const res = await GET();
    const data = await res.json();
    expect(data.mcpBaseUrl).toBe("http://custom:9000/mcp");
  });
});
