import { NextResponse } from "next/server";
import { backendUrl, backendHeaders } from "../../_backend";

export const runtime = "nodejs";

/**
 * Proxy for the backend's `GET /collaboration/knowledge-bases`: the
 * collaboration server's knowledge bases, each with its newest version's
 * manifest and this installation's copy of it (`?manifests=false`: head
 * versions only). Errors (503 without a server that answers) pass through with
 * their `detail`.
 */
export async function GET(request: Request) {
  // Only the one flag the backend takes is passed on.
  const manifests = new URL(request.url).searchParams.get("manifests") === "false" ? "?manifests=false" : "";
  let upstream: Response;
  try {
    upstream = await fetch(backendUrl(`/collaboration/knowledge-bases${manifests}`), {
      headers: await backendHeaders({ accept: "application/json" }),
      cache: "no-store",
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json({ detail: `Backend unreachable: ${message}` }, { status: 502 });
  }
  return new NextResponse(await upstream.text(), {
    status: upstream.status,
    headers: {
      "content-type": upstream.headers.get("content-type") ?? "application/json",
    },
  });
}
