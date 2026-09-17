import { NextResponse } from "next/server";
import { backendUrl, backendHeaders } from "../../../../../_backend";

export const runtime = "nodejs";

/** The clusters the backend will authorize. Checked here so an unknown value
 *  is refused with a readable message rather than interpolated into the
 *  upstream path and answered with a validation dump. */
const CLUSTERS = new Set(["odo", "frontier"]);

/**
 * Proxy for the backend's `POST /users/me/globus/{cluster}/login`.
 *
 * Starts an authorization and returns the address to visit. The reply carries
 * no secret: the PKCE verifier that makes the later exchange work never leaves
 * the backend, which is the whole reason this is two calls instead of one.
 */
export async function POST(
  _request: Request,
  { params }: { params: Promise<{ cluster: string }> }
) {
  const { cluster } = await params;
  if (!CLUSTERS.has(cluster)) {
    return NextResponse.json({ error: `Unknown cluster "${cluster}".` }, { status: 400 });
  }

  let upstream: Response;
  try {
    upstream = await fetch(backendUrl(`/users/me/globus/${cluster}/login`), {
      method: "POST",
      headers: await backendHeaders({ accept: "application/json" }),
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
