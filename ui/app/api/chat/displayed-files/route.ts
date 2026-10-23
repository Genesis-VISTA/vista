import { NextResponse } from "next/server";
import { backendHeaders, backendUrl } from "../../_backend";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/**
 * GET /api/chat/displayed-files?project_name=...&chat_session_id=... — the files
 * `display_file` showed in one conversation, newest first. The backend's answer
 * and status (404 for an unknown conversation) pass through as they are.
 */
export async function GET(request: Request) {
  const url = new URL(request.url);
  const projectName = url.searchParams.get("project_name");
  const chatSessionId = url.searchParams.get("chat_session_id");
  if (!projectName) {
    return NextResponse.json({ error: "Missing project_name." }, { status: 400 });
  }
  if (!chatSessionId) {
    return NextResponse.json({ error: "Missing chat_session_id." }, { status: 400 });
  }

  let upstream: Response;
  try {
    upstream = await fetch(
      backendUrl(
        `/projects/${encodeURIComponent(projectName)}/chat-sessions/${encodeURIComponent(chatSessionId)}/displayed-files`,
      ),
      { headers: await backendHeaders({ accept: "application/json" }), cache: "no-store" },
    );
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json({ error: `Backend unreachable: ${message}` }, { status: 502 });
  }

  return new NextResponse(await upstream.text(), {
    status: upstream.status,
    headers: { "content-type": "application/json" },
  });
}
