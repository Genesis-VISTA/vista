import { callMcpRpc } from "@/lib/mcp";

export type McpToolSummary = {
  name: string;
  description?: string;
  inputSchema?: any;
};

export type McpToolsDiscoveryResult = {
  ok: boolean;
  tools: McpToolSummary[];
  error?: string;
};

function normalizeTools(raw: unknown): McpToolSummary[] {
  if (!raw || typeof raw !== "object") return [];

  const envelope = raw as Record<string, unknown>;
  const result = (envelope.result ?? envelope) as Record<string, unknown>;
  const toolsRaw = result?.tools;

  if (!Array.isArray(toolsRaw)) return [];

  return toolsRaw
    .filter((tool) => tool && typeof tool === "object")
    .map((tool) => {
      const obj = tool as Record<string, unknown>;
      return {
        name: typeof obj.name === "string" ? obj.name : "unknown-tool",
        description: typeof obj.description === "string" ? obj.description : undefined,
        inputSchema: obj.inputSchema
      };
    });
}

async function tryListTools(method: string, timeoutMs: number): Promise<{ tools: McpToolSummary[]; rpcError?: string }> {
  const { response, text, json } = await callMcpRpc(method, {}, timeoutMs);
  if (!response.ok) {
    return { tools: [], rpcError: `HTTP ${response.status}: ${text}` };
  }

  const parsedObj = json as Record<string, unknown> | null;
  if (parsedObj?.error) {
    return {
      tools: [],
      rpcError: typeof parsedObj.error === "string" ? parsedObj.error : JSON.stringify(parsedObj.error)
    };
  }

  const tools = normalizeTools(json);
  return { tools };
}

export async function discoverMcpTools(timeoutMs = 2000): Promise<McpToolsDiscoveryResult> {
  const methods = ["tools/list", "list_tools"];

  for (const method of methods) {
    const { tools, rpcError } = await tryListTools(method, timeoutMs);
    if (tools.length > 0) {
      return { ok: true, tools };
    }
    if (!rpcError) {
      return { ok: true, tools: [] };
    }
  }

  return {
    ok: false,
    tools: [],
    error:
      "Tool discovery is unavailable. Expected MCP JSON-RPC support for method 'tools/list' returning { result: { tools: [...] } }. Enable MCP tool listing on the server transport or add a custom discovery endpoint."
  };
}
