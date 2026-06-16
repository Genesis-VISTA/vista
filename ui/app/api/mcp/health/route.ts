import { NextResponse } from "next/server";
import { backendUrl, getMcpBaseUrl, backendHeaders } from "../../_backend";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

type HealthResponse = {
  ok: boolean;
  mcpBaseUrl: string;
  detail?: string;
};

/**
 * Health check for the MCP server, exercised via the Python backend's
 * `GET /projects/{project_name}/mcp/tools` (a successful tools/list call
 * implies the backend can talk to MCP). The endpoint is project-scoped
 * post-refactor, so the active project name must be supplied as
 * `?project_name=<name>`.
 */
export async function GET(request: Request) {
  const mcpBaseUrl = getMcpBaseUrl();
  const url = new URL(request.url);
  const projectName = url.searchParams.get("project_name");
  const chatSessionId = url.searchParams.get("chat_session_id");
  if (!projectName) {
    return NextResponse.json(
      { ok: false, mcpBaseUrl, detail: "Missing project_name." } satisfies HealthResponse,
      { status: 400 }
    );
  }
  try {
    const upstreamUrl = new URL(
      backendUrl(`/projects/${encodeURIComponent(projectName)}/mcp/tools`)
    );
    if (chatSessionId) upstreamUrl.searchParams.set("chat_session_id", chatSessionId);
    const upstream = await fetch(upstreamUrl, {
      headers: await backendHeaders({ accept: "application/json" }),
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
