import { NextResponse } from "next/server";
import { getMcpClient } from "@/lib/mcp-client";

export const dynamic = "force-dynamic";
export const revalidate = 0;

type McpToolSummary = {
  name: string;
  description?: string;
  inputSchema?: unknown;
};

type McpToolsDiscoveryResult = {
  ok: boolean;
  tools: McpToolSummary[];
  error?: string;
};

export async function GET() {
  try {
    const client = await getMcpClient();
    const { tools } = await client.listTools(undefined, { signal: AbortSignal.timeout(5000) });

    const summaries: McpToolSummary[] = tools.map((t) => ({
      name: t.name,
      description: t.description,
      inputSchema: t.inputSchema,
    }));

    return NextResponse.json({ ok: true, tools: summaries } satisfies McpToolsDiscoveryResult, {
      status: 200,
      headers: {
        "cache-control": "no-store, no-cache, must-revalidate, proxy-revalidate"
      }
    });
  } catch (error) {
    const isAbort = error instanceof Error && error.name === "AbortError";
    const message = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json(
      {
        ok: false,
        tools: [],
        error: isAbort ? "Tool discovery timed out." : message
      } satisfies McpToolsDiscoveryResult,
      {
        status: 200,
        headers: {
          "cache-control": "no-store, no-cache, must-revalidate, proxy-revalidate"
        }
      }
    );
  }
}
