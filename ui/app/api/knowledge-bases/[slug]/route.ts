import { NextResponse } from "next/server";
import { backendUrl } from "../../_backend";

export const runtime = "nodejs";

/**
 * Proxy for `GET/PUT/DELETE /knowledge-bases/{slug}`.
 */

function sanitizeSlug(rawSlug: string): string | null {
  if (!rawSlug) return null;
  let decoded: string;
  try {
    decoded = decodeURIComponent(rawSlug);
  } catch {
    return null;
  }
  if (decoded !== decoded.toLowerCase()) return null;
  if (!/^[a-z0-9](?:[a-z0-9-]{0,78}[a-z0-9])?$/.test(decoded)) return null;
  return decoded;
}

export async function GET(
  _request: Request,
  { params }: { params: Promise<{ slug: string }> }
) {
  const slug = sanitizeSlug((await params).slug);
  if (!slug) {
    return NextResponse.json({ ok: false, error: "Invalid slug." }, { status: 400 });
  }
  let upstream: Response;
  try {
    upstream = await fetch(backendUrl(`/knowledge-bases/${encodeURIComponent(slug)}`), {
      headers: { accept: "application/json" },
      cache: "no-store",
    });
  } catch {
    return NextResponse.json({ ok: false, error: "Backend unreachable." }, { status: 502 });
  }
  if (upstream.status === 404) {
    return NextResponse.json({ ok: false, error: "Not found." }, { status: 404 });
  }
  if (!upstream.ok) {
    return NextResponse.json(
      { ok: false, error: `Backend error (${upstream.status}).` },
      { status: upstream.status }
    );
  }
  try {
    return NextResponse.json(await upstream.json());
  } catch {
    return NextResponse.json(
      { ok: false, error: "Backend returned malformed JSON." },
      { status: 502 }
    );
  }
}

export async function PUT(
  request: Request,
  { params }: { params: Promise<{ slug: string }> }
) {
  const slug = sanitizeSlug((await params).slug);
  if (!slug) {
    return NextResponse.json({ ok: false, error: "Invalid slug." }, { status: 400 });
  }
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ ok: false, error: "Invalid JSON." }, { status: 400 });
  }
  let upstream: Response;
  try {
    upstream = await fetch(backendUrl(`/knowledge-bases/${encodeURIComponent(slug)}`), {
      method: "PUT",
      headers: { "content-type": "application/json", accept: "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    return NextResponse.json({ ok: false, error: "Backend unreachable." }, { status: 502 });
  }
  const text = await upstream.text();
  if (!upstream.ok) {
    let detail = text;
    try {
      const parsed = JSON.parse(text);
      if (parsed && typeof parsed === "object" && "detail" in parsed) {
        detail = String((parsed as { detail: unknown }).detail);
      }
    } catch {
      // raw
    }
    return NextResponse.json(
      { ok: false, error: detail || `Update failed (${upstream.status}).` },
      { status: upstream.status }
    );
  }
  try {
    return NextResponse.json({ ok: true, kb: JSON.parse(text) });
  } catch {
    return NextResponse.json(
      { ok: false, error: "Backend returned malformed JSON." },
      { status: 502 }
    );
  }
}

export async function DELETE(
  _request: Request,
  { params }: { params: Promise<{ slug: string }> }
) {
  const slug = sanitizeSlug((await params).slug);
  if (!slug) {
    return NextResponse.json({ ok: false, error: "Invalid slug." }, { status: 400 });
  }
  let upstream: Response;
  try {
    upstream = await fetch(backendUrl(`/knowledge-bases/${encodeURIComponent(slug)}`), {
      method: "DELETE",
    });
  } catch {
    return NextResponse.json({ ok: false, error: "Backend unreachable." }, { status: 502 });
  }
  if (!upstream.ok && upstream.status !== 204) {
    const text = await upstream.text();
    let detail = text;
    try {
      const parsed = JSON.parse(text);
      if (parsed && typeof parsed === "object" && "detail" in parsed) {
        detail = String((parsed as { detail: unknown }).detail);
      }
    } catch {
      // raw
    }
    return NextResponse.json(
      { ok: false, error: detail || `Delete failed (${upstream.status}).` },
      { status: upstream.status }
    );
  }
  return NextResponse.json({ ok: true });
}
