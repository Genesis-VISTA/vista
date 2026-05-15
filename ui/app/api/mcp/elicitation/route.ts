import { NextRequest, NextResponse } from "next/server";
import { backendUrl } from "@/lib/backend";

// Forwards the user's elicitation answer to the backend, which routes it to
// the pending `asyncio.Future` that the active /agent/run stream is awaiting.
//
// Body shape (matches backend's ElicitationSubmit):
//   { id: string; action: "accept" | "decline" | "cancel"; content?: object }

export async function POST(req: NextRequest) {
  const body = await req.json();
  const upstream = await fetch(`${backendUrl}/mcp/elicitation`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  const text = await upstream.text();
  return new NextResponse(text, {
    status: upstream.status,
    headers: { "content-type": upstream.headers.get("content-type") ?? "application/json" },
  });
}
