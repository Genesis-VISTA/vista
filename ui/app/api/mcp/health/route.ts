import { NextResponse } from "next/server";
import { backendUrl } from "@/lib/backend";

// Quick health check — returns { ok: boolean } based on whether the backend MCP is reachable.

export async function GET() {
  try {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 5000);
    const res = await fetch(`${backendUrl}/mcp/tools`, {
      cache: "no-store",
      signal: controller.signal,
    });
    clearTimeout(timeout);
    return NextResponse.json({ ok: res.ok, mcpBaseUrl: backendUrl });
  } catch (err) {
    return NextResponse.json({
      ok: false,
      mcpBaseUrl: backendUrl,
      detail: err instanceof Error ? err.message : "fetch failed",
    });
  }
}
