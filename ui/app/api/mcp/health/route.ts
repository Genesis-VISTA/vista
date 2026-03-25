import { NextResponse } from "next/server";
import { getMcpClient, getMcpBaseUrl } from "@/lib/mcp-client";

type HealthResponse = {
  ok: boolean;
  mcpBaseUrl: string;
  detail?: string;
};

export async function GET() {
  const mcpBaseUrl = getMcpBaseUrl();
  try {
    const client = await getMcpClient();
    await client.listTools(undefined, { signal: AbortSignal.timeout(5000) });
    return NextResponse.json({ ok: true, mcpBaseUrl } satisfies HealthResponse, { status: 200 });
  } catch (error) {
    const isAbort = error instanceof Error && error.name === "AbortError";
    const message = error instanceof Error ? error.message : "Unknown error";

    return NextResponse.json(
      {
        ok: false,
        mcpBaseUrl,
        detail: isAbort ? "MCP health check timed out." : message
      } satisfies HealthResponse,
      { status: 200 }
    );
  }
}
