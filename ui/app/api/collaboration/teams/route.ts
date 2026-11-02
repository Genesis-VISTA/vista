import { NextResponse } from "next/server";
import { jsonBody, relay } from "../_relay";

export const runtime = "nodejs";

/**
 * Proxy for the backend's `POST /collaboration/teams`, body `{ name }`: a new
 * team on the collaboration server, with this researcher as its first member.
 * 409 with `detail` when the name is taken, in any case.
 */
export async function POST(request: Request) {
  const body = await jsonBody(request);
  if (body instanceof NextResponse) return body;
  return relay("/collaboration/teams", { method: "POST", body });
}
