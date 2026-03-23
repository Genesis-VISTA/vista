/**
 * Mock MCP JSON-RPC HTTP server.
 *
 * Used by lib tests to exercise callMcpRpc / discoverMcpTools against a
 * real HTTP endpoint without needing the Python FastMCP backend.
 */
import { createServer, type Server, type IncomingMessage, type ServerResponse } from "http";

export type RpcRequest = {
  jsonrpc: string;
  id?: string | number;
  method: string;
  params?: Record<string, unknown>;
};

export type RpcHandler = (method: string, params: Record<string, unknown>) => unknown;

let server: Server | null = null;
let port = 0;
let sessionId = "test-session-" + Date.now();
let handler: RpcHandler = defaultHandler;

function defaultHandler(method: string): unknown {
  if (method === "tools/list") {
    return {
      result: {
        tools: [
          { name: "bash", description: "Run a bash command", inputSchema: { type: "object", properties: { command: { type: "string" } } } },
          { name: "rag_search", description: "Search literature", inputSchema: { type: "object", properties: { query: { type: "string" } } } },
        ],
      },
    };
  }
  return { result: {} };
}

/** Override the handler for all non-initialize RPC calls. */
export function setMcpHandler(fn: RpcHandler): void {
  handler = fn;
}

/** Start the mock MCP server; returns the base URL (e.g. http://127.0.0.1:PORT). */
export function startMockMcp(): Promise<string> {
  return new Promise((resolve, reject) => {
    server = createServer((req: IncomingMessage, res: ServerResponse) => {
      if (req.method !== "POST") {
        res.writeHead(405);
        res.end();
        return;
      }

      let body = "";
      req.on("data", (chunk: Buffer) => {
        body += chunk.toString();
      });
      req.on("end", () => {
        try {
          const parsed: RpcRequest = JSON.parse(body);
          const headers: Record<string, string> = {
            "content-type": "application/json",
            "mcp-session-id": sessionId,
          };

          // Handle initialize
          if (parsed.method === "initialize") {
            res.writeHead(200, headers);
            res.end(
              JSON.stringify({
                jsonrpc: "2.0",
                id: parsed.id,
                result: {
                  protocolVersion: "2024-11-05",
                  capabilities: {},
                  serverInfo: { name: "mock-mcp", version: "0.0.1" },
                },
              })
            );
            return;
          }

          // Handle notifications (no id)
          if (parsed.id === undefined || parsed.id === null) {
            res.writeHead(200, headers);
            res.end("{}");
            return;
          }

          // Handle normal RPC
          const result = handler(parsed.method, parsed.params ?? {});
          res.writeHead(200, headers);

          // If result already has jsonrpc envelope, use it directly
          if (result && typeof result === "object" && "jsonrpc" in (result as Record<string, unknown>)) {
            res.end(JSON.stringify(result));
          } else if (result && typeof result === "object" && ("result" in (result as Record<string, unknown>) || "error" in (result as Record<string, unknown>))) {
            res.end(JSON.stringify({ jsonrpc: "2.0", id: parsed.id, ...(result as object) }));
          } else {
            res.end(JSON.stringify({ jsonrpc: "2.0", id: parsed.id, result }));
          }
        } catch (err) {
          res.writeHead(400, { "content-type": "application/json" });
          res.end(
            JSON.stringify({
              jsonrpc: "2.0",
              error: { code: -32700, message: "Parse error" },
            })
          );
        }
      });
    });

    server.on("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const addr = server!.address();
      if (addr && typeof addr === "object") {
        port = addr.port;
        resolve(`http://127.0.0.1:${port}`);
      } else {
        reject(new Error("Failed to get server address"));
      }
    });
  });
}

/** Stop the mock MCP server. */
export function stopMockMcp(): Promise<void> {
  return new Promise((resolve) => {
    if (!server) {
      resolve();
      return;
    }
    server.close(() => {
      server = null;
      resolve();
    });
  });
}

/** Reset handler to defaults. */
export function resetMockMcp(): void {
  handler = defaultHandler;
  sessionId = "test-session-" + Date.now();
}
