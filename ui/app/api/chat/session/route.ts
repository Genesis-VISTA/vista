import { NextResponse } from "next/server";
import { backendHeaders, backendUrl } from "../../_backend";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  const url = new URL(request.url);
  const projectName = url.searchParams.get("project_name");
  const chatSessionId = url.searchParams.get("chat_session_id");
  if (!projectName) {
    return NextResponse.json({ error: "Missing project_name." }, { status: 400 });
  }

  let upstream: Response;
  try {
    const upstreamUrl = new URL(
      backendUrl(`/projects/${encodeURIComponent(projectName)}/chat-session`)
    );
    if (chatSessionId) upstreamUrl.searchParams.set("chat_session_id", chatSessionId);
    upstream = await fetch(
      upstreamUrl,
      {
        headers: await backendHeaders({ accept: "application/json" }),
      }
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

export async function PUT(request: Request) {
  let body: {
    project_name?: unknown;
    chat_session_id?: unknown;
    title?: unknown;
    messages?: unknown;
    latest_result?: unknown;
    ack_run?: unknown;
  };
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body." }, { status: 400 });
  }

  const projectName = body.project_name;
  if (typeof projectName !== "string" || projectName.length === 0) {
    return NextResponse.json({ error: "Missing project_name." }, { status: 400 });
  }

  let upstream: Response;
  try {
    const upstreamUrl = new URL(
      backendUrl(`/projects/${encodeURIComponent(projectName)}/chat-session`)
    );
    if (typeof body.chat_session_id === "string" && body.chat_session_id.length > 0) {
      upstreamUrl.searchParams.set("chat_session_id", body.chat_session_id);
    }
    upstream = await fetch(
      upstreamUrl,
      {
        method: "PUT",
        headers: await backendHeaders({ "content-type": "application/json" }),
        body: JSON.stringify({
          title: typeof body.title === "string" ? body.title : null,
          // Model history is written only by the backend, so it is never forwarded.
          messages: body.messages ?? null,
          latest_result: body.latest_result ?? null,
          // The researcher has seen the last run: clears its unseen flag and stored events.
          ack_run: body.ack_run === true,
        }),
      }
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

export async function DELETE(request: Request) {
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
      backendUrl(`/projects/${encodeURIComponent(projectName)}/chat-session`)
    );
    upstreamUrl.searchParams.set("chat_session_id", chatSessionId);
    upstream = await fetch(
      upstreamUrl,
      {
        method: "DELETE",
        headers: await backendHeaders(),
      }
    );
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json(
      { error: `Backend unreachable: ${message}` },
      { status: 502 }
    );
  }

  return new NextResponse(null, { status: upstream.status });
}
