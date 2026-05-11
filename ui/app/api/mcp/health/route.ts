import { NextResponse } from "next/server";
import { backendUrl, getMcpBaseUrl } from "../../_backend";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

type HealthResponse = {
  ok: boolean;
  mcpBaseUrl: string;
  detail?: string;
};

/**
 * Health check for the MCP server, exercised via the Python backend's
 * `GET /mcp/tools` (a successful tools/list call implies the backend can
 * talk to MCP).
 */
export async function GET() {
  const mcpBaseUrl = getMcpBaseUrl();
  try {
    const upstream = await fetch(backendUrl("/mcp/tools"), {
      headers: { accept: "application/json" },
      signal: AbortSignal.timeout(5000),
    });
    if (!upstream.ok) {
      const detail = await upstream.text().catch(() => "");
      return NextResponse.json(
        { ok: false, mcpBaseUrl, detail: detail || `Backend returned ${upstream.status}` } satisfies HealthResponse,
        { status: 200 }
      );
    }
    return NextResponse.json({ ok: true, mcpBaseUrl } satisfies HealthResponse, { status: 200 });
  } catch (error) {
    const isAbort = error instanceof Error && error.name === "AbortError";
    const message = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json(
      {
        ok: false,
        mcpBaseUrl,
        detail: isAbort ? "MCP health check timed out." : message,
      } satisfies HealthResponse,
      { status: 200 }
    );
  }
}
