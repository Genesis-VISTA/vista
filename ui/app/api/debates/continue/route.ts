import { NextResponse } from "next/server";
import { backendHeaders, backendUrl } from "../../_backend";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/**
 * Argue a finished debate for a few more rounds.
 *
 * The backend refuses this on a debate that is still arguing and on a closed
 * thread, so those statuses come back as 409 and are surfaced as-is rather than
 * translated here.
 */
export async function POST(request: Request) {
  const params = new URL(request.url).searchParams;
  const projectName = params.get("project_name");
  const runId = params.get("run_id");
  if (!projectName || !runId) {
    return NextResponse.json(
      { error: "Missing project_name or run_id." },
      { status: 400 }
    );
  }

  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "Expected a JSON body." }, { status: 400 });
  }

  const path = `/projects/${encodeURIComponent(projectName)}/debates/${encodeURIComponent(
    runId
  )}/continue`;

  let upstream: Response;
  try {
    upstream = await fetch(backendUrl(path), {
      method: "POST",
      headers: await backendHeaders({
        accept: "application/json",
        "content-type": "application/json",
      }),
      body: JSON.stringify(body),
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
