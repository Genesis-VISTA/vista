import { NextResponse } from "next/server";
import { backendUrl, backendHeaders } from "../../_backend";

export const runtime = "nodejs";

export async function POST(request: Request) {
  let body: { project_name?: unknown; id?: unknown; action?: unknown; content?: unknown };
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ ok: false, error: "Invalid JSON" }, { status: 400 });
  }

  const projectName = body.project_name;
  if (typeof projectName !== "string" || projectName.length === 0) {
    return NextResponse.json({ ok: false, error: "Missing project_name." }, { status: 400 });
  }

  const upstreamBody = {
    id: body.id,
    action: body.action,
    content: body.content,
  };

  let upstream: Response;
  try {
    upstream = await fetch(
      backendUrl(`/projects/${encodeURIComponent(projectName)}/elicitation`),
      {
        method: "POST",
        headers: await backendHeaders({ "content-type": "application/json" }),
        body: JSON.stringify(upstreamBody),
      }
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
    return NextResponse.json(
      { ok: false, error: text || `Backend returned ${upstream.status}` },
      { status: upstream.status }
    );
  }

  // Backend already returns `{ok: true}` on success; pass it through.
  try {
    return NextResponse.json(JSON.parse(text));
  } catch {
    return NextResponse.json({ ok: true });
  }
}
