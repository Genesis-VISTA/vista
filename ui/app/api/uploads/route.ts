import { NextResponse } from "next/server";
import { backendUrl } from "../_backend";

export const runtime = "nodejs";

type BackendUpload = {
  name: string;
  size: number;
  created: string;
  modified: string;
};

/**
 * GET /api/uploads — list uploaded files.
 *
 * Backend returns `{name, size, created, modified}`. The frontend reads
 * `modifiedAt`, so we rename `modified` on the way out.
 */
export async function GET() {
  try {
    const upstream = await fetch(backendUrl("/uploads"), {
      headers: { accept: "application/json" },
    });
    if (!upstream.ok) {
      return NextResponse.json([], { status: 200 });
    }
    const files = (await upstream.json()) as BackendUpload[];
    const summaries = files.map((file) => ({
      name: file.name,
      size: file.size,
      modifiedAt: file.modified,
    }));
    return NextResponse.json(summaries);
  } catch {
    return NextResponse.json([], { status: 200 });
  }
}

/**
 * POST /api/uploads — forward the multipart body to the backend.
 *
 * The backend's response is `list[str]` (saved filenames). The frontend
 * expects `{ok, saved}` with a 4xx body of `{ok: false, error}` on failure.
 */
export async function POST(request: Request) {
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

  let upstream: Response;
  try {
    upstream = await fetch(backendUrl("/uploads"), {
      method: "POST",
      body: outgoing,
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
      // not JSON; keep the raw text
    }
    return NextResponse.json(
      { ok: false, error: detail || `Upload failed (${upstream.status}).` },
      { status: upstream.status }
    );
  }

  let saved: string[] = [];
  try {
    const parsed = JSON.parse(text);
    if (Array.isArray(parsed)) {
      saved = parsed.filter((entry): entry is string => typeof entry === "string");
    }
  } catch {
    // fall through with empty list
  }
  return NextResponse.json({ ok: true, saved });
}
