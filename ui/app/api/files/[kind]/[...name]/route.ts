import { NextResponse } from "next/server";
import { backendUrl, backendHeaders } from "../../../_backend";

export const runtime = "nodejs";

/** File kinds the backend exposes under /projects/{name}/{kind}. */
const FILE_KINDS = ["uploads", "outputs"] as const;
type FileKind = (typeof FILE_KINDS)[number];

function parseKind(value: string): FileKind | null {
  return (FILE_KINDS as readonly string[]).includes(value) ? (value as FileKind) : null;
}

/**
 * Validate the catch-all `name` segments as a nested relative path. Rejects empty
 * paths and any `""`, `.`, or `..` component (path traversal). Returns the
 * segments unchanged so the caller can re-encode them per segment for the backend.
 */
function sanitizeSegments(segments: string[]): string[] | null {
  if (segments.length === 0) return null;
  for (const part of segments) {
    if (!part || part === "." || part === "..") return null;
  }
  return segments;
}

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

/** Build the backend file URL, re-encoding each path segment but keeping `/` literal. */
function backendFileUrl(projectName: string, kind: FileKind, segments: string[]): string {
  const path = segments.map(encodeURIComponent).join("/");
  return backendUrl(`/projects/${encodeURIComponent(projectName)}/${kind}/${path}`);
}

export async function GET(
  request: Request,
  { params }: { params: Promise<{ kind: string; name: string[] }> }
) {
  const { kind: rawKind, name: rawName } = await params;
  const kind = parseKind(rawKind);
  if (!kind) {
    return NextResponse.json({ ok: false, error: "Unknown file kind." }, { status: 404 });
  }

  const resolved = requireProjectName(request);
  if (resolved instanceof NextResponse) return resolved;
  const { projectName } = resolved;

  const segments = sanitizeSegments(rawName);
  if (!segments) {
    return NextResponse.json({ ok: false, error: "Invalid file name." }, { status: 400 });
  }

  let upstream: Response;
  try {
    upstream = await fetch(backendFileUrl(projectName, kind, segments), {
      headers: await backendHeaders(),
    });
  } catch {
    return NextResponse.json({ ok: false, error: "Not found." }, { status: 404 });
  }

  if (!upstream.ok || !upstream.body) {
    return NextResponse.json({ ok: false, error: "Not found." }, { status: upstream.status || 404 });
  }

  const fallbackName = segments[segments.length - 1];
  return new Response(upstream.body, {
    status: 200,
    headers: {
      "content-type": upstream.headers.get("content-type") ?? "application/octet-stream",
      "content-disposition":
        upstream.headers.get("content-disposition") ?? `attachment; filename="${fallbackName}"`,
    },
  });
}

export async function DELETE(
  request: Request,
  { params }: { params: Promise<{ kind: string; name: string[] }> }
) {
  const { kind: rawKind, name: rawName } = await params;
  const kind = parseKind(rawKind);
  if (!kind) {
    return NextResponse.json({ ok: false, error: "Unknown file kind." }, { status: 404 });
  }

  const resolved = requireProjectName(request);
  if (resolved instanceof NextResponse) return resolved;
  const { projectName } = resolved;

  const segments = sanitizeSegments(rawName);
  if (!segments) {
    return NextResponse.json({ ok: false, error: "Invalid file name." }, { status: 400 });
  }

  let upstream: Response;
  try {
    upstream = await fetch(backendFileUrl(projectName, kind, segments), {
      method: "DELETE",
      headers: await backendHeaders(),
    });
  } catch {
    return NextResponse.json({ ok: false, error: "Failed to delete file." }, { status: 502 });
  }

  if (!upstream.ok) {
    return NextResponse.json({ ok: false, error: "Failed to delete file." }, { status: upstream.status });
  }
  return NextResponse.json({ ok: true });
}
