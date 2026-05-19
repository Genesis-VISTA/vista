import { NextResponse } from "next/server";
import { backendUrl } from "../../../_backend";

export const runtime = "nodejs";

/**
 * Proxy for `POST /knowledge-bases/{slug}/publications`.
 *
 * Forwards the multipart body (uploaded PDFs) to the backend, which
 * writes them to the KB's pdfs/ directory and kicks off indexing as
 * a FastAPI background task. The frontend polls GET on the KB to see
 * per-publication `index_status` updates.
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

export async function POST(
  request: Request,
  { params }: { params: Promise<{ slug: string }> }
) {
  const slug = sanitizeSlug((await params).slug);
  if (!slug) {
    return NextResponse.json({ ok: false, error: "Invalid slug." }, { status: 400 });
  }

  const url = new URL(request.url);
  const projectName = url.searchParams.get("project_name");

  let formData: FormData;
  try {
    formData = await request.formData();
  } catch {
    return NextResponse.json({ ok: false, error: "Invalid form data." }, { status: 400 });
  }
  const incoming = formData.getAll("files").filter((entry): entry is File => entry instanceof File);
  if (incoming.length === 0) {
    return NextResponse.json({ ok: false, error: "No files were provided." }, { status: 400 });
  }
  const outgoing = new FormData();
  for (const file of incoming) {
    outgoing.append("files", file, file.name);
  }

  const upstreamPath = projectName
    ? `/knowledge-bases/${encodeURIComponent(slug)}/publications?project_name=${encodeURIComponent(projectName)}`
    : `/knowledge-bases/${encodeURIComponent(slug)}/publications`;

  let upstream: Response;
  try {
    upstream = await fetch(
      backendUrl(upstreamPath),
      { method: "POST", body: outgoing }
    );
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
      // raw
    }
    return NextResponse.json(
      { ok: false, error: detail || `Upload failed (${upstream.status}).` },
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
