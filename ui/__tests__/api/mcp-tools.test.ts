/**
 * @jest-environment node
 */

jest.mock("@/lib/mcp-tools", () => ({
  discoverMcpTools: jest.fn(),
}));

// Need to re-export the type so the route can use it
const actualModule = jest.requireActual("@/lib/mcp-tools");
const mock = jest.requireMock("@/lib/mcp-tools");
mock.McpToolsDiscoveryResult = actualModule.McpToolsDiscoveryResult;

import { discoverMcpTools } from "@/lib/mcp-tools";
import { GET } from "@/app/api/mcp/tools/route";

const mockDiscover = discoverMcpTools as jest.MockedFunction<typeof discoverMcpTools>;

beforeEach(() => {
  mockDiscover.mockReset();
});

describe("GET /api/mcp/tools", () => {
  it("returns discovered tools", async () => {
    mockDiscover.mockResolvedValue({
      ok: true,
      tools: [
        { name: "bash", description: "Run a bash command" },
        { name: "rag_search", description: "Search literature" },
      ],
    });

    const res = await GET();
    expect(res.status).toBe(200);

    const data = await res.json();
    expect(data.ok).toBe(true);
    expect(data.tools).toHaveLength(2);
    expect(data.tools[0].name).toBe("bash");
  });

  it("returns empty tools on discovery failure", async () => {
    mockDiscover.mockResolvedValue({
      ok: false,
      tools: [],
      error: "Tool discovery is unavailable.",
    });

    const res = await GET();
    const data = await res.json();
    expect(data.ok).toBe(false);
    expect(data.tools).toEqual([]);
    expect(data.error).toContain("unavailable");
  });

  it("returns error on timeout", async () => {
    const err = new Error("Timeout");
    err.name = "AbortError";
    mockDiscover.mockRejectedValue(err);

    const res = await GET();
    const data = await res.json();
    expect(data.ok).toBe(false);
    expect(data.tools).toEqual([]);
    expect(data.error).toContain("timed out");
  });

  it("returns error on generic exception", async () => {
    mockDiscover.mockRejectedValue(new Error("Network failure"));

    const res = await GET();
    const data = await res.json();
    expect(data.ok).toBe(false);
    expect(data.error).toContain("Network failure");
  });

  it("sets no-cache headers", async () => {
    mockDiscover.mockResolvedValue({ ok: true, tools: [] });

    const res = await GET();
    expect(res.headers.get("cache-control")).toContain("no-store");
  });
});
