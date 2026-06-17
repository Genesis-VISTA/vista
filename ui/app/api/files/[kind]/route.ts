import { NextResponse } from "next/server";
import { backendUrl, backendHeaders } from "../../_backend";

export const runtime = "nodejs";

/** File kinds the backend exposes under /projects/{name}/{kind}. */
const FILE_KINDS = ["uploads", "outputs"] as const;
type FileKind = (typeof FILE_KINDS)[number];

function parseKind(value: string): FileKind | null {
  return (FILE_KINDS as readonly string[]).includes(value) ? (value as FileKind) : null;
}

type BackendFile = {
  name: string;
  size: number;
  created: string;
  modified: string;
};

function requireProjectName(request: Request): { projectName: string } | NextResponse {
  const projectName = new URL(request.url).searchParams.get("project_name");
  if (!projectName) {
    return NextResponse.json(
      { ok: false, error: "Missing 'project_name' query parameter." },
      { status: 400 }
    );
  }
  return { projectName };
}

/**
 * GET /api/files/{kind}?project_name=... — list a project agent's files.
 *
 * Backend returns `{name, size, created, modified}`. The frontend reads
 * `modifiedAt` and a `source` discriminator, so we reshape on the way out:
 * uploaded files are `"upload"`, generated outputs are `"generated"`.
 */
export async function GET(
  request: Request,
  { params }: { params: Promise<{ kind: string }> }
) {
  const kind = parseKind((await params).kind);
  if (!kind) {
    return NextResponse.json({ ok: false, error: "Unknown file kind." }, { status: 404 });
  }

  const resolved = requireProjectName(request);
  if (resolved instanceof NextResponse) return resolved;
  const { projectName } = resolved;

  try {
    const upstream = await fetch(
      backendUrl(`/projects/${encodeURIComponent(projectName)}/${kind}`),
      { headers: await backendHeaders({ accept: "application/json" }) }
    );
    if (!upstream.ok) {
      return NextResponse.json([], { status: 200 });
    }
    const files = (await upstream.json()) as BackendFile[];
    const summaries = files.map((file) => ({
      name: file.name,
      size: file.size,
      modifiedAt: file.modified,
      source: kind === "outputs" ? "generated" : "upload",
    }));
    return NextResponse.json(summaries);
  } catch {
    return NextResponse.json([], { status: 200 });
  }
}

/**
 * POST /api/files/uploads?project_name=... — forward the multipart body to the
 * backend. Only the `uploads` kind is writable; generated `outputs` are not.
 *
 * The backend's response is `list[str]` (saved filenames). The frontend
 * expects `{ok, saved}` with a 4xx body of `{ok: false, error}` on failure.
 */
export async function POST(
  request: Request,
  { params }: { params: Promise<{ kind: string }> }
) {
  const kind = parseKind((await params).kind);
  if (kind !== "uploads") {
    return NextResponse.json(
      { ok: false, error: "Only uploads can be created." },
      { status: 405 }
    );
  }

  const resolved = requireProjectName(request);
  if (resolved instanceof NextResponse) return resolved;
  const { projectName } = resolved;

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
    upstream = await fetch(
      backendUrl(`/projects/${encodeURIComponent(projectName)}/uploads`),
      { method: "POST", headers: await backendHeaders(), body: outgoing }
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
