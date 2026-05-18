import { NextResponse } from "next/server";
import { backendUrl } from "../_backend";

export const runtime = "nodejs";

/**
 * Proxy for `GET /knowledge-bases` and `POST /knowledge-bases`.
 *
 * The backend is the source of truth — Knowledge Bases live in
 * vista.db and are managed by the FastAPI router in
 * `backend/.../api/knowledge_bases.py`. This route is a thin shim
 * for the unmodified frontend.
 */

export async function GET() {
  try {
    const upstream = await fetch(backendUrl("/knowledge-bases"), {
      headers: { accept: "application/json" },
      // KBs include progress snapshots that change without the underlying
      // row being touched — skip Next.js's per-request cache.
      cache: "no-store",
    });
    if (!upstream.ok) {
      return NextResponse.json([], { status: 200 });
    }
    return NextResponse.json(await upstream.json());
  } catch {
    return NextResponse.json([], { status: 200 });
  }
}

export async function POST(request: Request) {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ ok: false, error: "Invalid JSON." }, { status: 400 });
  }

  let upstream: Response;
  try {
    upstream = await fetch(backendUrl("/knowledge-bases"), {
      method: "POST",
      headers: { "content-type": "application/json", accept: "application/json" },
      body: JSON.stringify(body),
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json(
      { ok: false, error: `Backend unreachable: ${message}` },
      { status: 502 }
    );
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
      // raw text
    }
    return NextResponse.json(
      { ok: false, error: detail || `Create failed (${upstream.status}).` },
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
