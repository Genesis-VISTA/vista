import { NextResponse } from "next/server";
import { discoverMcpTools, type McpToolsDiscoveryResult } from "@/lib/mcp-tools";

export async function GET() {
  try {
    const result = await discoverMcpTools(2000);
    return NextResponse.json(result satisfies McpToolsDiscoveryResult, { status: 200 });
  } catch (error) {
    const isAbort = error instanceof Error && error.name === "AbortError";
    const message = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json(
      {
        ok: false,
        tools: [],
        error: isAbort ? "Tool discovery timed out after 2000ms." : message
      } satisfies McpToolsDiscoveryResult,
      { status: 200 }
    );
  }
}
