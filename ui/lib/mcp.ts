const DEFAULT_MCP_BASE_URL = "http://127.0.0.1:8000/mcp";
export const MCP_JSON_HEADERS: HeadersInit = {
  "content-type": "application/json",
  accept: "application/json, text/event-stream"
};
const MCP_PROTOCOL_VERSION = "2024-11-05";

let mcpSessionId: string | null = null;

export function getMcpBaseUrl(): string {
  return process.env.MCP_BASE_URL || DEFAULT_MCP_BASE_URL;
}

export async function fetchWithTimeout(
  input: RequestInfo | URL,
  init: RequestInit = {},
  timeoutMs = 2000
): Promise<Response> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);

  try {
    return await fetch(input, {
      ...init,
      signal: controller.signal
    });
  } finally {
    clearTimeout(timeout);
  }
}

type RpcResult = {
  response: Response;
  text: string;
  json: unknown;
};

function parseJsonSafe(text: string): unknown {
  try {
    return text ? JSON.parse(text) : null;
  } catch {
    return text;
  }
}

function parseEventStreamJson(text: string): unknown {
  const events: unknown[] = [];
  const lines = text.split(/\r?\n/);

  for (const line of lines) {
    if (!line.startsWith("data:")) continue;
    const data = line.slice(5).trim();
    if (!data || data === "[DONE]") continue;
    try {
      events.push(JSON.parse(data));
    } catch {
      continue;
    }
  }

  if (events.length === 0) return text;

  // Prefer a JSON-RPC payload with result/error when present.
  const rpcEnvelope = events.find((event) => {
    if (!event || typeof event !== "object") return false;
    const obj = event as Record<string, unknown>;
    return obj.jsonrpc === "2.0" && ("result" in obj || "error" in obj);
  });

  return rpcEnvelope ?? events[events.length - 1];
}

function parseMcpPayload(text: string, contentType: string | null): unknown {
  const isEventStream = (contentType ?? "").toLowerCase().includes("text/event-stream");
  if (isEventStream) {
    return parseEventStreamJson(text);
  }
  return parseJsonSafe(text);
}

async function postRpc(payload: unknown, timeoutMs: number, useSession: boolean): Promise<RpcResult> {
  const headers: Record<string, string> = {
    "content-type": "application/json",
    accept: "application/json, text/event-stream"
  };
  if (useSession && mcpSessionId) {
    headers["mcp-session-id"] = mcpSessionId;
  }

  const response = await fetchWithTimeout(
    getMcpBaseUrl(),
    {
      method: "POST",
      headers,
      body: JSON.stringify(payload)
    },
    timeoutMs
  );

  const maybeSessionId = response.headers.get("mcp-session-id") ?? response.headers.get("Mcp-Session-Id");
  if (maybeSessionId) {
    mcpSessionId = maybeSessionId;
  }

  const text = await response.text();
  const contentType = response.headers.get("content-type");
  return { response, text, json: parseMcpPayload(text, contentType) };
}

function isMissingSession(result: RpcResult): boolean {
  if (result.response.status !== 400) return false;
  if (typeof result.text === "string" && result.text.includes("Missing session ID")) return true;
  if (!result.json || typeof result.json !== "object") return false;
  const err = (result.json as Record<string, unknown>).error;
  if (!err || typeof err !== "object") return false;
  const msg = (err as Record<string, unknown>).message;
  return typeof msg === "string" && msg.includes("Missing session ID");
}

async function ensureSession(timeoutMs: number): Promise<void> {
  if (mcpSessionId) return;

  const initPayload = {
    jsonrpc: "2.0",
    id: `init-${Date.now()}`,
    method: "initialize",
    params: {
      protocolVersion: MCP_PROTOCOL_VERSION,
      capabilities: {},
      clientInfo: {
        name: "vercel-vista-ui",
        version: "0.1.0"
      }
    }
  };

  const initResult = await postRpc(initPayload, timeoutMs, false);
  if (!initResult.response.ok || !mcpSessionId) return;

  // Best-effort initialized notification.
  await postRpc(
    {
      jsonrpc: "2.0",
      method: "notifications/initialized",
      params: {}
    },
    timeoutMs,
    true
  ).catch(() => undefined);
}

export async function callMcpRpc(method: string, params: Record<string, unknown> = {}, timeoutMs = 2000): Promise<RpcResult> {
  await ensureSession(timeoutMs);

  const payload = {
    jsonrpc: "2.0",
    id: `${method}-${Date.now()}`,
    method,
    params
  };

  let result = await postRpc(payload, timeoutMs, true);
  if (isMissingSession(result)) {
    mcpSessionId = null;
    await ensureSession(timeoutMs);
    result = await postRpc(payload, timeoutMs, true);
  }
  return result;
}
