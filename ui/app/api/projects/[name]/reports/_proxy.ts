import { NextResponse } from "next/server";
import { backendUrl, backendHeaders } from "../../../_backend";

/**
 * POST a JSON body to `/projects/{name}/reports{suffix}` on the backend and
 * pass its status and body straight through, so a 403, a missing inference
 * credential's 409, or a too-large report's 400 reach the modal as-is.
 */
export async function proxyReportPost(
  request: Request,
  name: string,
  suffix: "" | "/generate"
): Promise<NextResponse> {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON" }, { status: 400 });
  }

  let upstream: Response;
  try {
    upstream = await fetch(
      backendUrl(`/projects/${encodeURIComponent(name)}/reports${suffix}`),
      {
        method: "POST",
        headers: await backendHeaders({
          "content-type": "application/json",
          accept: "application/json",
        }),
        body: JSON.stringify(body ?? {}),
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
