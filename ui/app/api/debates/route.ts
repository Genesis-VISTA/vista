import { NextResponse } from "next/server";
import { backendHeaders, backendUrl } from "../_backend";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/**
 * Debate proxy. Query params:
 *   project_name (required)
 *   run_id  -> GET  /projects/{name}/debates/{run_id}  (run + participants + posts)
 *   (none)  -> GET  /projects/{name}/debates           (every debate in the project)
 *
 * POST opens a debate. It returns as soon as the thread and roster exist; the
 * argument then runs in the background and the client follows `/api/debates/events`.
 */
function debatePath(projectName: string, runId?: string | null): string {
  const base = `/projects/${encodeURIComponent(projectName)}/debates`;
  return runId ? `${base}/${encodeURIComponent(runId)}` : base;
}

async function relay(path: string, init?: RequestInit): Promise<NextResponse> {
  let upstream: Response;
  try {
    upstream = await fetch(backendUrl(path), init);
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json({ error: `Backend unreachable: ${message}` }, { status: 502 });
  }
  return new NextResponse(await upstream.text(), {
    status: upstream.status,
    headers: { "content-type": "application/json" },
  });
}

export async function GET(request: Request) {
  const params = new URL(request.url).searchParams;
  const projectName = params.get("project_name");
  if (!projectName) {
    return NextResponse.json({ error: "Missing project_name." }, { status: 400 });
  }
  return relay(debatePath(projectName, params.get("run_id")), {
    headers: await backendHeaders({ accept: "application/json" }),
  });
}

export async function POST(request: Request) {
  const params = new URL(request.url).searchParams;
  const projectName = params.get("project_name");
  if (!projectName) {
    return NextResponse.json({ error: "Missing project_name." }, { status: 400 });
  }
  return relay(debatePath(projectName), {
    method: "POST",
    headers: await backendHeaders({
      accept: "application/json",
      "content-type": "application/json",
    }),
    body: await request.text(),
  });
}
