import { NextResponse } from "next/server";
import { backendHeaders, backendUrl } from "../../_backend";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  const projectName = new URL(request.url).searchParams.get("project_name");
  if (!projectName) {
    return NextResponse.json({ error: "Missing project_name." }, { status: 400 });
  }

  let upstream: Response;
  try {
    upstream = await fetch(
      backendUrl(`/projects/${encodeURIComponent(projectName)}/chat-session`),
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
    message_history?: unknown;
    messages?: unknown;
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
    upstream = await fetch(
      backendUrl(`/projects/${encodeURIComponent(projectName)}/chat-session`),
      {
        method: "PUT",
        headers: await backendHeaders({ "content-type": "application/json" }),
        body: JSON.stringify({
          message_history: body.message_history ?? [],
          messages: body.messages ?? [],
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
