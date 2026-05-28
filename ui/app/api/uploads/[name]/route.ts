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

export async function GET(request: Request, { params }: { params: Promise<{ name: string }> }) {
  const resolved = requireProjectName(request);
  if (resolved instanceof NextResponse) return resolved;
  const { projectName } = resolved;

  const safeName = sanitizeName((await params).name);
  if (!safeName) {
    return NextResponse.json({ ok: false, error: "Invalid file name." }, { status: 400 });
  }

  let upstream: Response;
  try {
    upstream = await fetch(
      backendUrl(
        `/projects/${encodeURIComponent(projectName)}/uploads/${encodeURIComponent(safeName)}`
      )
    );
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

export async function DELETE(request: Request, { params }: { params: Promise<{ name: string }> }) {
  const resolved = requireProjectName(request);
  if (resolved instanceof NextResponse) return resolved;
  const { projectName } = resolved;

  const safeName = sanitizeName((await params).name);
  if (!safeName) {
    return NextResponse.json({ ok: false, error: "Invalid file name." }, { status: 400 });
  }

  let upstream: Response;
  try {
    upstream = await fetch(
      backendUrl(
        `/projects/${encodeURIComponent(projectName)}/uploads/${encodeURIComponent(safeName)}`
      ),
      { method: "DELETE" }
    );
  } catch {
    return NextResponse.json({ ok: false, error: "Failed to delete file." }, { status: 502 });
  }

  if (!upstream.ok) {
    return NextResponse.json({ ok: false, error: "Failed to delete file." }, { status: upstream.status });
  }
  return NextResponse.json({ ok: true });
}
