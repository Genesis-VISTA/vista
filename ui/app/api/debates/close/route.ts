import { NextResponse } from "next/server";
import { backendHeaders, backendUrl } from "../../_backend";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/**
 * End a debate early.
 *
 * Closing is enforced by the forum rather than by the orchestrator: it writes a
 * CLOSED post, the next agent post is refused, and the loop stops by being told
 * no.
 */
export async function POST(request: Request) {
  const params = new URL(request.url).searchParams;
  const projectName = params.get("project_name");
  const threadId = params.get("thread_id");
  if (!projectName || !threadId) {
    return NextResponse.json(
      { error: "Missing project_name or thread_id." },
      { status: 400 }
    );
  }

  const path = `/projects/${encodeURIComponent(projectName)}/threads/${encodeURIComponent(
    threadId
  )}/close`;

  let upstream: Response;
  try {
    upstream = await fetch(backendUrl(path), {
      method: "POST",
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
