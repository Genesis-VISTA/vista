import { NextResponse } from "next/server";
import { backendUrl } from "../../_backend";

export const runtime = "nodejs";
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

/**
 * Proxy for `GET /mcp/tools`. Backend returns a bare `list[Tool]`;
 * the frontend expects an `{ok, tools, error?}` envelope.
 */
export async function GET() {
  try {
    const upstream = await fetch(backendUrl("/mcp/tools"), {
      headers: { accept: "application/json" },
      signal: AbortSignal.timeout(5000),
    });
    if (!upstream.ok) {
      const detail = await upstream.text().catch(() => "");
      return NextResponse.json(
        {
          ok: false,
          tools: [],
          error: detail || `Backend returned ${upstream.status}`,
        } satisfies McpToolsDiscoveryResult,
        { status: 200, headers: { "cache-control": "no-store, no-cache, must-revalidate, proxy-revalidate" } }
      );
    }

    const tools = (await upstream.json()) as Array<{ name?: string; description?: string; inputSchema?: unknown }>;
    const summaries: McpToolSummary[] = Array.isArray(tools)
      ? tools
          .filter((t) => t && typeof t.name === "string")
          .map((t) => ({
            name: t.name as string,
            description: t.description,
            inputSchema: t.inputSchema,
          }))
      : [];

    return NextResponse.json(
      { ok: true, tools: summaries } satisfies McpToolsDiscoveryResult,
      { status: 200, headers: { "cache-control": "no-store, no-cache, must-revalidate, proxy-revalidate" } }
    );
  } catch (error) {
    const isAbort = error instanceof Error && error.name === "AbortError";
    const message = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json(
      {
        ok: false,
        tools: [],
        error: isAbort ? "Tool discovery timed out." : message,
      } satisfies McpToolsDiscoveryResult,
      { status: 200, headers: { "cache-control": "no-store, no-cache, must-revalidate, proxy-revalidate" } }
    );
  }
}
