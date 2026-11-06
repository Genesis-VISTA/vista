import { NextResponse } from "next/server";
import { jsonBody, relay } from "../../_relay";

export const runtime = "nodejs";

/**
 * Proxy for the backend's `/updates/download`. POST, body `{ version }`, starts
 * downloading and verifying the offered release in the background (409 when
 * that version is not on offer); GET reports its progress:
 * `{ state, version, bytes, total, error }`.
 */
export async function POST(request: Request) {
  const body = await jsonBody(request);
  if (body instanceof NextResponse) return body;
  return relay("/updates/download", { method: "POST", body });
}

export async function GET() {
  return relay("/updates/download");
}
