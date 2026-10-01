import { NextResponse } from "next/server";
import { backendHeaders, backendUrl } from "../../../_backend";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/**
 * Proxy to the backend's `GET /projects/{p}/chat-sessions/{id}/run/events`.
 *
 * Re-attaches to a conversation's active run: every event after `after`
 * (0 replays from the start), then live events until the run ends. The SSE
 * body is streamed straight through, with the backend's `id:` lines (the
 * event's seq) intact. 204 means the conversation has no active run.
 *
 * Closing this request only stops watching; the run continues. So the
 * browser's abort is forwarded upstream (`signal`) to release the stream, and
 * never to cancel anything.
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
    const upstreamUrl = new URL(
      backendUrl(
        `/projects/${encodeURIComponent(projectName)}/chat-sessions/${encodeURIComponent(chatSessionId)}/run/events`
      )
    );
    upstreamUrl.searchParams.set("after", url.searchParams.get("after") ?? "0");
    upstream = await fetch(upstreamUrl, {
      headers: await backendHeaders({ accept: "text/event-stream" }),
      signal: request.signal,
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json(
      { error: `Backend unreachable: ${message}` },
      { status: 502 }
    );
  }

  if (upstream.status === 204) return new Response(null, { status: 204 });

  if (!upstream.ok || !upstream.body) {
    const detail = await upstream.text().catch(() => "");
    return NextResponse.json(
      { error: detail || `Backend returned ${upstream.status}` },
      { status: upstream.status || 502 }
    );
  }

  return new Response(upstream.body, {
    status: upstream.status,
    headers: {
      "content-type": upstream.headers.get("content-type") ?? "text/event-stream",
      "cache-control": "no-cache",
      connection: "keep-alive",
      "x-accel-buffering": "no",
    },
  });
}
