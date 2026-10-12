import { NextResponse } from "next/server";
import { backendUrl, backendHeaders } from "../../../../_backend";
import { relayImported } from "../../../../skills/import/_imported";

export const runtime = "nodejs";

/**
 * Proxy for the backend's `GET /projects/{name}/skills/from-output`: the
 * folders in the researcher's output folder for this project that hold a
 * SKILL.md, i.e. skills the agent wrote to `/mnt/data/output/<skill>/`.
 */
export async function GET(
  _request: Request,
  { params }: { params: Promise<{ name: string }> }
) {
  const { name } = await params;

  let upstream: Response;
  try {
    upstream = await fetch(
      backendUrl(`/projects/${encodeURIComponent(name)}/skills/from-output`),
      { headers: await backendHeaders({ accept: "application/json" }) }
    );
  } catch {
    return NextResponse.json({ error: "Upstream unavailable" }, { status: 502 });
  }
  return new NextResponse(await upstream.text(), {
    status: upstream.status,
    headers: { "content-type": "application/json" },
  });
}

/**
 * Proxy for `POST /projects/{name}/skills/from-output` with `{ folder, replace }`:
 * imports that folder as a private skill and loads it into the project.
 * The reply is reshaped like the other import routes'.
 */
export async function POST(
  request: Request,
  { params }: { params: Promise<{ name: string }> }
) {
  const { name } = await params;

  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body." }, { status: 400 });
  }

  let upstream: Response;
  try {
    upstream = await fetch(
      backendUrl(`/projects/${encodeURIComponent(name)}/skills/from-output`),
      {
        method: "POST",
        headers: await backendHeaders({
          "content-type": "application/json",
          accept: "application/json",
        }),
        body: JSON.stringify(body),
      }
    );
  } catch {
    return NextResponse.json({ error: "Upstream unavailable" }, { status: 502 });
  }
  return relayImported(upstream);
}
