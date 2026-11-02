import { NextResponse } from "next/server";
import { backendUrl, backendHeaders } from "../_backend";

/** A team id as the backend takes it; anything else never reaches it. */
export const TEAM_ID = /^[0-9a-fA-F-]{36}$/;

/**
 * Send one request to the backend's `/collaboration` routes and pass its
 * answer through: status, body and all, so a refusal keeps its `detail` and
 * `code`. A 204 is passed on without a body, which a 204 cannot carry.
 */
export async function relay(path: string, init: RequestInit = {}): Promise<NextResponse> {
  let upstream: Response;
  try {
    upstream = await fetch(backendUrl(path), {
      ...init,
      headers: await backendHeaders({
        accept: "application/json",
        ...(init.body ? { "content-type": "application/json" } : {}),
      }),
      cache: "no-store",
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json({ detail: `Backend unreachable: ${message}` }, { status: 502 });
  }
  if (upstream.status === 204) return new NextResponse(null, { status: 204 });
  return new NextResponse(await upstream.text(), {
    status: upstream.status,
    headers: {
      "content-type": upstream.headers.get("content-type") ?? "application/json",
    },
  });
}

/** The request's JSON body, or a 400 to answer with. */
export async function jsonBody(request: Request): Promise<string | NextResponse> {
  try {
    return JSON.stringify(await request.json());
  } catch {
    return NextResponse.json({ detail: "Invalid JSON body." }, { status: 400 });
  }
}
