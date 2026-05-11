import { NextResponse } from "next/server";
import { backendUrl } from "../../_backend";

export const runtime = "nodejs";

function sanitizeName(rawName: string): string | null {
  if (!rawName) return null;
  let decoded: string;
  try {
    decoded = decodeURIComponent(rawName);
  } catch {
    return null;
  }
  const base = decoded.split("/").pop() ?? "";
  if (!base || base !== decoded) return null;
  return base;
}

export async function GET(_request: Request, { params }: { params: Promise<{ name: string }> }) {
  const safeName = sanitizeName((await params).name);
  if (!safeName) {
    return NextResponse.json({ ok: false, error: "Invalid file name." }, { status: 400 });
  }

  let upstream: Response;
  try {
    upstream = await fetch(backendUrl(`/uploads/${encodeURIComponent(safeName)}`));
  } catch {
    return NextResponse.json({ ok: false, error: "Not found." }, { status: 404 });
  }

  if (!upstream.ok || !upstream.body) {
    return NextResponse.json({ ok: false, error: "Not found." }, { status: upstream.status || 404 });
  }

  return new Response(upstream.body, {
    status: 200,
    headers: {
      "content-type": upstream.headers.get("content-type") ?? "application/octet-stream",
      "content-disposition":
        upstream.headers.get("content-disposition") ?? `attachment; filename="${safeName}"`,
    },
  });
}

export async function DELETE(_request: Request, { params }: { params: Promise<{ name: string }> }) {
  const safeName = sanitizeName((await params).name);
  if (!safeName) {
    return NextResponse.json({ ok: false, error: "Invalid file name." }, { status: 400 });
  }

  let upstream: Response;
  try {
    upstream = await fetch(backendUrl(`/uploads/${encodeURIComponent(safeName)}`), {
      method: "DELETE",
    });
  } catch {
    return NextResponse.json({ ok: false, error: "Failed to delete file." }, { status: 502 });
  }

  if (!upstream.ok) {
    return NextResponse.json({ ok: false, error: "Failed to delete file." }, { status: upstream.status });
  }
  return NextResponse.json({ ok: true });
}
