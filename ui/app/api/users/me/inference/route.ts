import { NextResponse } from "next/server";
import { backendUrl, backendHeaders } from "../../../_backend";

export const runtime = "nodejs";

/**
 * Proxy for the backend's `GET /users/me/inference`: the provider options, the
 * provider and model in effect for this researcher, and which providers have a
 * key. Carries no secrets, so the model picker reads this rather than the full
 * user.
 */
export async function GET() {
  let upstream: Response;
  try {
    upstream = await fetch(backendUrl("/users/me/inference"), {
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
