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
      capabilities: {
        elicitation: {}
      },
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

/* ------------------------------------------------------------------ */
/*  Elicitation support                                                */
/* ------------------------------------------------------------------ */

export type ElicitationRequest = {
  id: string | number;
  message: string;
  requestedSchema: Record<string, unknown>;
};

export type ElicitationResponse = {
  action: "accept" | "decline" | "cancel";
  content?: Record<string, unknown>;
};

/**
 * Send a JSON-RPC **response** back to the MCP server (used for
 * replying to server-initiated requests like elicitation/create).
 */
async function postRpcResponse(
  id: string | number,
  result: unknown,
  timeoutMs: number
): Promise<void> {
  const headers: Record<string, string> = {
    "content-type": "application/json",
    accept: "application/json, text/event-stream"
  };
  if (mcpSessionId) {
    headers["mcp-session-id"] = mcpSessionId;
  }

  await fetchWithTimeout(
    getMcpBaseUrl(),
    {
      method: "POST",
      headers,
      body: JSON.stringify({ jsonrpc: "2.0", id, result })
    },
    timeoutMs
  );
}

/**
 * Call an MCP tool with support for mid-call elicitation.
 *
 * POSTs tools/call, then reads the response. If the MCP server sends
 * an SSE stream containing an `elicitation/create` JSON-RPC request,
 * the `onElicitation` callback is invoked so the caller can collect
 * user input. The elicitation response is POSTed back and reading
 * continues until the final tool result arrives.
 *
 * Falls back to standard buffered read when the response is plain JSON.
 */
export async function callMcpToolWithElicitation(
  toolName: string,
  toolArgs: Record<string, unknown>,
  onElicitation: (req: ElicitationRequest) => Promise<ElicitationResponse>,
  timeoutMs = 120_000
): Promise<RpcResult> {
  await ensureSession(timeoutMs);

  const rpcId = `tools/call-${Date.now()}`;
  const payload = {
    jsonrpc: "2.0",
    id: rpcId,
    method: "tools/call",
    params: { name: toolName, arguments: toolArgs }
  };

  const headers: Record<string, string> = {
    "content-type": "application/json",
    accept: "application/json, text/event-stream"
  };
  if (mcpSessionId) {
    headers["mcp-session-id"] = mcpSessionId;
  }

  const response = await fetchWithTimeout(
    getMcpBaseUrl(),
    { method: "POST", headers, body: JSON.stringify(payload) },
    timeoutMs
  );

  const maybeSessionId =
    response.headers.get("mcp-session-id") ??
    response.headers.get("Mcp-Session-Id");
  if (maybeSessionId) {
    mcpSessionId = maybeSessionId;
  }

  const contentType = response.headers.get("content-type") ?? "";

  // Non-SSE response: fall back to buffered read (no elicitation)
  if (!contentType.includes("text/event-stream")) {
    const text = await response.text();
    return { response, text, json: parseMcpPayload(text, contentType) };
  }

  // SSE response: read incrementally looking for elicitation requests
  const reader = response.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let finalJson: unknown = null;
  let fullText = "";

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split(/\r?\n/);
    // Keep the last (possibly incomplete) line in the buffer
    buffer = lines.pop() ?? "";

    for (const line of lines) {
      if (!line.startsWith("data:")) continue;
      const data = line.slice(5).trim();
      if (!data || data === "[DONE]") continue;
      fullText += line + "\n";

      let parsed: Record<string, unknown>;
      try {
        parsed = JSON.parse(data);
      } catch {
        continue;
      }

      // Server-initiated elicitation request
      if (
        parsed.jsonrpc === "2.0" &&
        parsed.method === "elicitation/create" &&
        parsed.id != null
      ) {
        const params = parsed.params as Record<string, unknown> | undefined;
        const elicitReq: ElicitationRequest = {
          id: parsed.id as string | number,
          message: typeof params?.message === "string" ? params.message : "",
          requestedSchema:
            (params?.requestedSchema as Record<string, unknown>) ?? {}
        };

        const elicitResp = await onElicitation(elicitReq);
        await postRpcResponse(elicitReq.id, elicitResp, timeoutMs);
        continue;
      }

      // JSON-RPC response (the tool result)
      if (
        parsed.jsonrpc === "2.0" &&
        ("result" in parsed || "error" in parsed)
      ) {
        finalJson = parsed;
      }
    }
  }

  // Process any remaining buffer
  if (buffer.startsWith("data:")) {
    const data = buffer.slice(5).trim();
    if (data && data !== "[DONE]") {
      fullText += buffer + "\n";
      try {
        const parsed = JSON.parse(data) as Record<string, unknown>;
        if (parsed.jsonrpc === "2.0" && ("result" in parsed || "error" in parsed)) {
          finalJson = parsed;
        }
      } catch {
        // ignore
      }
    }
  }

  const json = finalJson ?? parseMcpPayload(fullText, contentType);
  return { response, text: fullText, json };
}
