import { NextResponse } from "next/server";
import { backendHeaders, backendUrl } from "../_backend";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/**
 * Read-only campaign proxy. Query params:
 *   project_name (required)
 *   run_id            -> GET /projects/{name}/campaigns/{run_id}   (full state: run + steps + jobs)
 *   chat_session_id   -> GET /projects/{name}/campaigns?chat_session_id=...  (list for a conversation)
 *   (neither)         -> GET /projects/{name}/campaigns            (all campaigns in the project)
 */
export async function GET(request: Request) {
  const params = new URL(request.url).searchParams;
  const projectName = params.get("project_name");
  if (!projectName) {
    return NextResponse.json({ error: "Missing project_name." }, { status: 400 });
  }
  const runId = params.get("run_id");
  const chatSessionId = params.get("chat_session_id");

  const base = `/projects/${encodeURIComponent(projectName)}/campaigns`;
  let path: string;
  if (runId) {
    path = `${base}/${encodeURIComponent(runId)}`;
  } else if (chatSessionId) {
    path = `${base}?chat_session_id=${encodeURIComponent(chatSessionId)}`;
  } else {
    path = base;
  }

  let upstream: Response;
  try {
    upstream = await fetch(backendUrl(path), {
      headers: await backendHeaders({ accept: "application/json" }),
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json({ error: `Backend unreachable: ${message}` }, { status: 502 });
  }

  return new NextResponse(await upstream.text(), {
    status: upstream.status,
    headers: { "content-type": "application/json" },
  });
}
