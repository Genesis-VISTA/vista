import { NextResponse } from "next/server";
import { backendUrl, backendHeaders } from "../../../_backend";

export const runtime = "nodejs";

/**
 * Proxy for the backend's `GET /users/me/hpc-status`: whether each HPC
 * cluster the rail shows would work for this researcher right now.
 *
 * Forwards only `fresh` and `cluster`, the two parameters the backend reads,
 * so nothing else a caller appends reaches it. The backend caches results for
 * a minute; `fresh=true` (optionally with `cluster=<name>`) reruns the checks.
 */
export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const upstreamParams = new URLSearchParams();
  if (searchParams.get("fresh") === "true") upstreamParams.set("fresh", "true");
  const cluster = searchParams.get("cluster");
  if (cluster) upstreamParams.set("cluster", cluster);
  const query = upstreamParams.toString();

  let upstream: Response;
  try {
    upstream = await fetch(
      backendUrl(`/users/me/hpc-status${query ? `?${query}` : ""}`),
      {
        headers: await backendHeaders({ accept: "application/json" }),
        cache: "no-store",
      },
    );
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
