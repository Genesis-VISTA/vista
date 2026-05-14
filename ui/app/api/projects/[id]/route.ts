import { NextResponse } from "next/server";
import { backendUrl } from "../../_backend";

export const runtime = "nodejs";

const UUID_RE = /^[0-9a-fA-F-]{36}$/;

/**
 * Proxy for the backend's `GET/PUT/DELETE /projects/{id}`.
 *
 * `PUT` is a full overwrite on the backend, so callers must send a complete
 * `ProjectCreate` body; this route just forwards it.
 */
async function resolveId(
  params: Promise<{ id: string }>
): Promise<string | null> {
  const { id } = await params;
  return UUID_RE.test(id) ? id : null;
}

export async function GET(
  _request: Request,
  { params }: { params: Promise<{ id: string }> }
) {
  const id = await resolveId(params);
  if (!id) return NextResponse.json({ error: "Project not found" }, { status: 404 });

  let upstream: Response;
  try {
    upstream = await fetch(backendUrl(`/projects/${id}`), {
      headers: { accept: "application/json" },
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json(
      { error: `Backend unreachable: ${message}` },
      { status: 502 }
    );
  }
  return new NextResponse(await upstream.text(), {
    status: upstream.status,
    headers: { "content-type": "application/json" },
  });
}

export async function PUT(
  request: Request,
  { params }: { params: Promise<{ id: string }> }
) {
  const id = await resolveId(params);
  if (!id) return NextResponse.json({ error: "Project not found" }, { status: 404 });

  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body." }, { status: 400 });
  }

  let upstream: Response;
  try {
    upstream = await fetch(backendUrl(`/projects/${id}`), {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json(
      { error: `Backend unreachable: ${message}` },
      { status: 502 }
    );
  }
  return new NextResponse(await upstream.text(), {
    status: upstream.status,
    headers: { "content-type": "application/json" },
  });
}

export async function DELETE(
  _request: Request,
  { params }: { params: Promise<{ id: string }> }
) {
  const id = await resolveId(params);
  if (!id) return NextResponse.json({ error: "Project not found" }, { status: 404 });

  let upstream: Response;
  try {
    upstream = await fetch(backendUrl(`/projects/${id}`), { method: "DELETE" });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json(
      { error: `Backend unreachable: ${message}` },
      { status: 502 }
    );
  }
  if (upstream.status === 204) {
    return new NextResponse(null, { status: 204 });
  }
  return new NextResponse(await upstream.text(), {
    status: upstream.status,
    headers: { "content-type": "application/json" },
  });
}
