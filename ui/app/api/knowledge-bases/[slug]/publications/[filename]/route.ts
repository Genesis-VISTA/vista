import { NextResponse } from "next/server";
import { backendUrl, backendHeaders } from "../../../../_backend";

export const runtime = "nodejs";

/**
 * Proxy for `GET/DELETE /knowledge-bases/{slug}/publications/{filename}`.
 * GET streams the PDF body straight through so the browser can render it.
 */

function sanitizeSlug(rawSlug: string): string | null {
  if (!rawSlug) return null;
  let decoded: string;
  try {
    decoded = decodeURIComponent(rawSlug);
  } catch {
    return null;
  }
  if (!/^[a-z0-9](?:[a-z0-9-]{0,78}[a-z0-9])?$/.test(decoded)) return null;
  return decoded;
}

function sanitizeFilename(rawName: string): string | null {
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

export async function GET(
  request: Request,
  { params }: { params: Promise<{ slug: string; filename: string }> }
) {
  const { slug: rawSlug, filename: rawFile } = await params;
  const slug = sanitizeSlug(rawSlug);
  const filename = sanitizeFilename(rawFile);
  if (!slug || !filename) {
    return NextResponse.json({ ok: false, error: "Invalid path." }, { status: 400 });
  }

  const url = new URL(request.url);
  const projectName = url.searchParams.get("project_name");
  const base = `/knowledge-bases/${encodeURIComponent(slug)}/publications/${encodeURIComponent(filename)}`;
  const upstreamPath = projectName
    ? `${base}?project_name=${encodeURIComponent(projectName)}`
    : base;

  let upstream: Response;
  try {
    upstream = await fetch(backendUrl(upstreamPath), { headers: await backendHeaders() });
  } catch {
    return NextResponse.json({ ok: false, error: "Not found." }, { status: 404 });
  }
  if (upstream.status === 403) {
    return NextResponse.json({ ok: false, error: "Knowledge base not in project scope." }, { status: 403 });
  }
  if (!upstream.ok || !upstream.body) {
    return NextResponse.json(
      { ok: false, error: "Not found." },
      { status: upstream.status || 404 }
    );
  }
  return new Response(upstream.body, {
    status: 200,
    headers: {
      "content-type": upstream.headers.get("content-type") ?? "application/pdf",
      "content-disposition":
        upstream.headers.get("content-disposition") ?? `inline; filename="${filename}"`,
    },
  });
}

export async function DELETE(
  request: Request,
  { params }: { params: Promise<{ slug: string; filename: string }> }
) {
  const { slug: rawSlug, filename: rawFile } = await params;
  const slug = sanitizeSlug(rawSlug);
  const filename = sanitizeFilename(rawFile);
  if (!slug || !filename) {
    return NextResponse.json({ ok: false, error: "Invalid path." }, { status: 400 });
  }

  const url = new URL(request.url);
  const projectName = url.searchParams.get("project_name");
  const base = `/knowledge-bases/${encodeURIComponent(slug)}/publications/${encodeURIComponent(filename)}`;
  const upstreamPath = projectName
    ? `${base}?project_name=${encodeURIComponent(projectName)}`
    : base;

  let upstream: Response;
  try {
    upstream = await fetch(
      backendUrl(upstreamPath),
      { method: "DELETE", headers: await backendHeaders() }
    );
  } catch {
    return NextResponse.json({ ok: false, error: "Backend unreachable." }, { status: 502 });
  }
  if (upstream.status === 403) {
    return NextResponse.json({ ok: false, error: "Knowledge base not in project scope." }, { status: 403 });
  }
  if (!upstream.ok && upstream.status !== 204) {
    return NextResponse.json(
      { ok: false, error: `Delete failed (${upstream.status}).` },
      { status: upstream.status }
    );
  }
  return NextResponse.json({ ok: true });
}
