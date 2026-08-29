import { NextResponse } from "next/server";
import { backendHeaders, backendUrl } from "../../_backend";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/**
 * The forum's federation state, for the UI to warn about.
 *
 * Deployment-wide rather than per-project, which is why it sits outside the
 * project-scoped debate routes.
 */
export async function GET() {
  let upstream: Response;
  try {
    upstream = await fetch(backendUrl("/forum/status"), {
      headers: await backendHeaders({ accept: "application/json" }),
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json({ error: `Backend unreachable: ${message}` }, { status: 502 });
  }
  return new NextResponse(await upstream.text(), {
    status: upstream.status,
    headers: { "content-type": "application/json" },
  });
}
