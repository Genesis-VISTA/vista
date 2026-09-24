import { NextResponse } from "next/server";
import { backendHeaders, backendUrl } from "../../_backend";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/**
 * The human speaking into a live debate.
 *
 * Posted as `human`, which is the point: agent posts carry their own identity,
 * and this one must not be able to borrow one.
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

  const path = `/projects/${encodeURIComponent(projectName)}/debates/${encodeURIComponent(
    runId
  )}/posts`;

  let upstream: Response;
  try {
    upstream = await fetch(backendUrl(path), {
      method: "POST",
      headers: await backendHeaders({
        accept: "application/json",
        "content-type": "application/json",
      }),
      body: await request.text(),
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
