import { NextResponse } from "next/server";
import { backendHeaders, backendUrl } from "../../../_backend";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/**
 * Proxy to the backend's `GET /projects/{p}/chat-runs/status`.
 *
 * Returns `[{ chat_session_id, status, unseen }]` for every conversation in the
 * project that is working, needs the researcher, or has an unseen outcome.
 * Conversations that are idle are left out.
 */
export async function GET(request: Request) {
  const projectName = new URL(request.url).searchParams.get("project_name");
  if (!projectName) {
    return NextResponse.json({ error: "Missing project_name." }, { status: 400 });
  }

  let upstream: Response;
  try {
    upstream = await fetch(
      backendUrl(`/projects/${encodeURIComponent(projectName)}/chat-runs/status`),
      { headers: await backendHeaders({ accept: "application/json" }) }
    );
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
