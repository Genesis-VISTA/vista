import { NextResponse } from "next/server";
import { backendUrl, backendHeaders } from "../../../../../_backend";

export const runtime = "nodejs";

const CLUSTERS = new Set(["odo", "frontier"]);

/**
 * Proxy for the backend's `POST /users/me/globus/{cluster}/code`.
 *
 * Hands over the single-use code the researcher pasted. The backend exchanges
 * it and stores the credential; what comes back is the identity it was
 * connected as, never the credential itself.
 */
export async function POST(
  request: Request,
  { params }: { params: Promise<{ cluster: string }> }
) {
  const { cluster } = await params;
  if (!CLUSTERS.has(cluster)) {
    return NextResponse.json({ error: `Unknown cluster "${cluster}".` }, { status: 400 });
  }

  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body." }, { status: 400 });
  }

  let upstream: Response;
  try {
    upstream = await fetch(backendUrl(`/users/me/globus/${cluster}/code`), {
      method: "POST",
      headers: await backendHeaders({ "content-type": "application/json" }),
      body: JSON.stringify(body),
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json(
      { error: `Backend unreachable: ${message}` },
      { status: 502 }
    );
  }
  return new NextResponse(await upstream.text(), {
    status: upstream.status,
    headers: {
      "content-type": upstream.headers.get("content-type") ?? "application/json",
    },
  });
}
