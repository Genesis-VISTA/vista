import { NextRequest } from "next/server";
import { backendUrl } from "@/lib/backend";

// Download proxy for uploaded files. Deletion is handled by the
// `deleteUpload` server action in app/actions/uploads.ts — no DELETE here.

export async function GET(_req: NextRequest, { params }: { params: Promise<{ name: string }> }) {
  const { name } = await params;
  const upstream = await fetch(
    `${backendUrl}/uploads/${encodeURIComponent(name)}`,
    { cache: "no-store" },
  );
  const headers = new Headers();
  for (const h of ["content-type", "content-length", "content-disposition"]) {
    const v = upstream.headers.get(h);
    if (v) headers.set(h, v);
  }
  return new Response(upstream.body, { status: upstream.status, headers });
}
