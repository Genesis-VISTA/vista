import { NextResponse } from "next/server";
import { backendHeaders, backendUrl } from "../../_backend";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/**
 * Server-sent events for a live debate, passed straight through.
 *
 * The upstream body is relayed unbuffered — reading it to completion first would
 * defeat the point, since the stream stays open for the length of the argument.
 * `x-accel-buffering: no` stops an intermediate proxy from holding events back.
 */
export async function GET(request: Request) {
  const params = new URL(request.url).searchParams;
  const projectName = params.get("project_name");
  const runId = params.get("run_id");
  if (!projectName || !runId) {
    return NextResponse.json(
      { error: "Missing project_name or run_id." },
      { status: 400 }
    );
  }

  const path = `/projects/${encodeURIComponent(projectName)}/debates/${encodeURIComponent(
    runId
  )}/events`;

  let upstream: Response;
  try {
    upstream = await fetch(backendUrl(path), {
      headers: await backendHeaders({ accept: "text/event-stream" }),
      signal: request.signal,
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json({ error: `Backend unreachable: ${message}` }, { status: 502 });
  }

  if (!upstream.ok || !upstream.body) {
    return new NextResponse(await upstream.text(), {
      status: upstream.status,
      headers: { "content-type": "application/json" },
    });
  }

  return new NextResponse(upstream.body, {
    status: 200,
    headers: {
      "content-type": "text/event-stream",
      "cache-control": "no-cache, no-transform",
      connection: "keep-alive",
      "x-accel-buffering": "no",
    },
  });
}
