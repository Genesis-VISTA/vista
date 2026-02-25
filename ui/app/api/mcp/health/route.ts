import { NextResponse } from "next/server";
import { callMcpRpc, getMcpBaseUrl } from "@/lib/mcp";

type HealthResponse = {
  ok: boolean;
  mcpBaseUrl: string;
  detail?: string;
};

export async function GET() {
  const mcpBaseUrl = getMcpBaseUrl();
  try {
    const { response, text } = await callMcpRpc("tools/list", {}, 2000);
    const result: HealthResponse = {
      ok: response.ok,
      mcpBaseUrl
    };

    if (!response.ok) {
      result.detail = `MCP responded with HTTP ${response.status}: ${text}`;
      return NextResponse.json(result, { status: 200 });
    }

    return NextResponse.json(result, { status: 200 });
  } catch (error) {
    const isAbort = error instanceof Error && error.name === "AbortError";
    const message = error instanceof Error ? error.message : "Unknown error";

    return NextResponse.json(
      {
        ok: false,
        mcpBaseUrl,
        detail: isAbort ? "MCP health check timed out after 2000ms." : message
      } satisfies HealthResponse,
      { status: 200 }
    );
  }
}
