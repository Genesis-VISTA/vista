import { NextResponse } from "next/server";
import { backendUrl, backendHeaders } from "../../../_backend";

export const runtime = "nodejs";

/**
 * Proxy for the backend's `GET /projects/{name}/models`.
 *
 * A thin pass-through of status and body: a missing or rejected inference
 * credential surfaces as the backend's own 409, which `ui/lib/models.ts`
 * already knows how to read (same shape `ui/lib/user.ts` reads elsewhere).
 */
export async function GET(
  _request: Request,
  { params }: { params: Promise<{ name: string }> }
) {
  const { name } = await params;

  let upstream: Response;
  try {
    upstream = await fetch(
      backendUrl(`/projects/${encodeURIComponent(name)}/models`),
      { headers: await backendHeaders({ accept: "application/json" }) }
    );
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json(
      { error: `Backend unreachable: ${message}` },
      { status: 502 }
    );
  }
  return new NextResponse(await upstream.text(), {
    status: upstream.status,
    headers: { "content-type": "application/json" },
  });
}
