import { NextResponse } from "next/server";
import { backendUrl } from "../_backend";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/**
 * Thin proxy to the Python backend's `POST /projects/{project_name}/agent/run`
 * SSE endpoint.
 *
 * The client sends `{ project_name, user_prompt, message_history }`; we add
 * `stream: true` and forward to the project-scoped agent route. The backend
 * emits PydanticAI `AgentStreamEvent`s plus app events (`log`,
 * `agent_run_result`, `mcp_form_elicitation`, `mcp_url_elicitation`) as
 * Server-Sent Events with real `event:` lines — this route streams them
 * straight through; the parser lives in `app/page.tsx`.
 */
export async function POST(request: Request) {
  let body: { project_name?: unknown; user_prompt?: unknown; message_history?: unknown };
  try {
    body = await request.json();
  } catch {
    return NextResponse.json(
      { ok: false, error: "Invalid JSON body." },
      { status: 400 }
    );
  }

  const projectName = body.project_name;
  if (typeof projectName !== "string" || projectName.length === 0) {
    return NextResponse.json(
      { ok: false, error: "Missing project_name." },
      { status: 400 }
    );
  }

  let upstream: Response;
  try {
    upstream = await fetch(
      backendUrl(`/projects/${encodeURIComponent(projectName)}/agent/run`),
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          stream: true,
          user_prompt: body.user_prompt,
          message_history: body.message_history ?? [],
        }),
        signal: request.signal,
      }
    );
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json(
      { ok: false, error: `Backend unreachable: ${message}` },
      { status: 502 }
    );
  }

  if (!upstream.ok || !upstream.body) {
    const detail = await upstream.text().catch(() => "");
    return NextResponse.json(
      { ok: false, error: detail || `Backend returned ${upstream.status}` },
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
