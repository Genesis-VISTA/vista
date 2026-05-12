import { NextResponse } from "next/server";
import { backendUrl } from "../_backend";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/**
 * Thin proxy to the Python backend's `/chat` SSE endpoint.
 *
 * The backend already emits the SSE event shapes the frontend expects
 * (`log`, `agent_turn`, `agent_response`, `elicitation`, `error`, `done`),
 * so this route just forwards the request body and streams the response
 * straight through.
 */
export async function POST(request: Request) {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json(
      { ok: false, response: "", error: "Invalid JSON body." },
      { status: 400 }
    );
  }

  let upstream: Response;
  try {
    upstream = await fetch(backendUrl("/ui/chat"), {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
      signal: request.signal,
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json(
      { ok: false, response: "", error: `Backend unreachable: ${message}` },
      { status: 502 }
    );
  }

  if (!upstream.ok || !upstream.body) {
    const detail = await upstream.text().catch(() => "");
    return NextResponse.json(
      { ok: false, response: "", error: detail || `Backend returned ${upstream.status}` },
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
