import { NextResponse } from "next/server";
import { backendUrl } from "@/lib/backend";

// Proxy to backend MCP tool discovery endpoint.

export async function GET() {
  try {
    const res = await fetch(`${backendUrl}/mcp/tools`, { cache: "no-store" });
    if (!res.ok) {
      return NextResponse.json(
        { ok: false, tools: [], error: `HTTP ${res.status}` },
        { status: 200 },
      );
    }
    const tools = await res.json();
    return NextResponse.json({ ok: true, tools });
  } catch (err) {
    return NextResponse.json({
      ok: false,
      tools: [],
      error: err instanceof Error ? err.message : "fetch failed",
    });
  }
}
