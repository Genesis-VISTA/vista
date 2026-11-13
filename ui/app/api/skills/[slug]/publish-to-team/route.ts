import { NextResponse } from "next/server";
import { postBackend } from "../../import/_imported";

export const runtime = "nodejs";

/**
 * Proxy for the backend's `POST /skills/{name}/publish-to-team`, body
 * `{ team_id }`: publish a private skill for the first time, into a team on the
 * collaboration server. A success is the backend's `TeamPublishResult`; errors
 * pass through with their status and `detail` (400 with code `not_in_team` for
 * a team the researcher is not in, 400 for a skill in a team already).
 */
export async function POST(
  request: Request,
  { params }: { params: Promise<{ slug: string }> }
) {
  const { slug } = await params;
  if (!/^[a-zA-Z0-9._-]+$/.test(slug)) {
    return NextResponse.json({ detail: "Skill not found" }, { status: 404 });
  }
  const sent = await postBackend(request, `/skills/${encodeURIComponent(slug)}/publish-to-team`);
  if (sent instanceof NextResponse) return sent;
  return NextResponse.json(sent.payload ?? { detail: "Failed" }, { status: sent.status });
}
