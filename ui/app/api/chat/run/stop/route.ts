import { NextResponse } from "next/server";
import { backendHeaders, backendUrl } from "../../../_backend";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/**
 * Proxy to the backend's `POST /projects/{p}/chat-sessions/{id}/run/stop`.
 *
 * Takes `{ project_name, chat_session_id }`. The backend answers 202 when it
 * has cancelled the run, or 404 when the conversation has no active run (the
 * run may have finished a moment earlier), and both are passed through.
 */
export async function POST(request: Request) {
  let body: { project_name?: unknown; chat_session_id?: unknown };
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body." }, { status: 400 });
  }

  const projectName = body.project_name;
  const chatSessionId = body.chat_session_id;
  if (typeof projectName !== "string" || projectName.length === 0) {
    return NextResponse.json({ error: "Missing project_name." }, { status: 400 });
  }
  if (typeof chatSessionId !== "string" || chatSessionId.length === 0) {
    return NextResponse.json({ error: "Missing chat_session_id." }, { status: 400 });
  }

  let upstream: Response;
  try {
    upstream = await fetch(
      backendUrl(
        `/projects/${encodeURIComponent(projectName)}/chat-sessions/${encodeURIComponent(chatSessionId)}/run/stop`
      ),
      { method: "POST", headers: await backendHeaders() }
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
