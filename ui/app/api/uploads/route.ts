import { NextRequest, NextResponse } from "next/server";
import { backendUrl } from "@/lib/backend";

// File upload proxy — kept as a route handler because multipart form data
// cannot be forwarded from a server action.

export async function GET() {
  const res = await fetch(`${backendUrl}/uploads`, { cache: "no-store" });
  if (!res.ok) {
    return NextResponse.json({ error: `HTTP ${res.status}` }, { status: res.status });
  }
  return NextResponse.json(await res.json());
}

export async function POST(req: NextRequest) {
  const formData = await req.formData();
  const upstream = await fetch(`${backendUrl}/uploads`, {
    method: "POST",
    body: formData,
  });
  const data = await upstream.text();
  return new Response(data, {
    status: upstream.status,
    headers: { "content-type": upstream.headers.get("content-type") ?? "application/json" },
  });
}
