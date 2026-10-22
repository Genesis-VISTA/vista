import { NextResponse } from "next/server";
import { backendUrl, backendHeaders } from "../../_backend";

export const runtime = "nodejs";

/**
 * Proxy for the backend's `GET /collaboration/status`: whether publishing and
 * importing can work, and why not when they cannot. The backend asks the
 * collaboration server each time, within a few seconds.
 */
export async function GET() {
  let upstream: Response;
  try {
    upstream = await fetch(backendUrl("/collaboration/status"), {
      headers: await backendHeaders({ accept: "application/json" }),
      cache: "no-store",
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json(
      { error: `Backend unreachable: ${message}` },
      { status: 502 },
    );
  }
  return new NextResponse(await upstream.text(), {
    status: upstream.status,
    headers: {
      "content-type": upstream.headers.get("content-type") ?? "application/json",
    },
  });
}
