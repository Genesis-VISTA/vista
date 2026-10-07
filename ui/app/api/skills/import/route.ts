import { NextResponse } from "next/server";
import { backendUrl, backendHeaders } from "../../_backend";
import { relayImported } from "./_imported";

export const runtime = "nodejs";

/**
 * Proxy for `POST /skills/import`.
 *
 * Body: `{ url: string }` pointing at either
 *   https://github.com/<owner>/<repo>                          (root SKILL.md)
 *   https://github.com/<owner>/<repo>/tree/<ref>/<subpath>     (monorepo entry)
 *
 * A local folder goes through `/api/skills/import/upload` instead.
 */
export async function POST(request: Request) {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON" }, { status: 400 });
  }

  let upstream: Response;
  try {
    upstream = await fetch(backendUrl("/skills/import"), {
      method: "POST",
      headers: await backendHeaders({
        "content-type": "application/json",
        accept: "application/json",
      }),
      body: JSON.stringify(body ?? {}),
    });
  } catch {
    return NextResponse.json({ error: "Upstream unavailable" }, { status: 502 });
  }
  return relayImported(upstream);
}
