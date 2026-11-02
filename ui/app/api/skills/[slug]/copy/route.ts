import { NextResponse } from "next/server";
import { postBackend } from "../../import/_imported";

export const runtime = "nodejs";

/**
 * Proxy for the backend's `POST /skills/{name}/copy`, body `{ team_id }`: copy
 * the skill to a team on the collaboration server. A success is the backend's
 * `TeamCopyResult`; errors pass through with their status and `detail` (404
 * for a team the researcher is not in, 400 for the team it is in already).
 */
export async function POST(
  request: Request,
  { params }: { params: Promise<{ slug: string }> }
) {
  const { slug } = await params;
  if (!/^[a-zA-Z0-9._-]+$/.test(slug)) {
    return NextResponse.json({ detail: "Skill not found" }, { status: 404 });
  }
  const sent = await postBackend(request, `/skills/${encodeURIComponent(slug)}/copy`);
  if (sent instanceof NextResponse) return sent;
  return NextResponse.json(sent.payload ?? { detail: "Failed" }, { status: sent.status });
}
