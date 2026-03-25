import { Client } from "@modelcontextprotocol/sdk/client";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";

const DEFAULT_MCP_BASE_URL = "http://127.0.0.1:8000/mcp";
export function getMcpBaseUrl(): string {
  return process.env.MCP_BASE_URL || DEFAULT_MCP_BASE_URL;
}

let client: Client | undefined;

/**
 * Get or create the singleton MCP SDK client.
 * Lazily connects on first call; reconnects if the previous session closed.
 *
 * Callers that need elicitation support should register their own handler
 * via `client.setRequestHandler(ElicitRequestSchema, ...)` after obtaining
 * the client.
 */
export async function getMcpClient(): Promise<Client> {
  if (client) return client;

  const newClient = new Client(
    { name: "vercel-vista-ui", version: "0.1.0" },
    { capabilities: { elicitation: { form: {} } } }
  );

  const transport = new StreamableHTTPClientTransport(
    new URL(getMcpBaseUrl())
  );
  transport.onclose = async () => {
    // this is probably redundant but just to make sure everything is cleaned up properly
    await client?.close().catch(() => {});
    client = undefined;
  };

  await newClient.connect(transport);
  client = newClient;

  return newClient;
}
