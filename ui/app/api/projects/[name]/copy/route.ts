import { NextResponse } from "next/server";
import { postBackend } from "../../../skills/import/_imported";

export const runtime = "nodejs";

/**
 * Proxy for the backend's `POST /projects/{name}/copy`, body `{ team_id }`:
 * copy the project to a team on the collaboration server, its skills and
 * knowledge bases first. A success is the backend's `TeamCopyResult`, the
 * project last; errors pass through with their status and `detail` (400 when a
 * skill or knowledge base it uses is missing or of another team).
 */
export async function POST(
  request: Request,
  { params }: { params: Promise<{ name: string }> }
) {
  const { name } = await params;
  const sent = await postBackend(request, `/projects/${encodeURIComponent(name)}/copy`);
  if (sent instanceof NextResponse) return sent;
  return NextResponse.json(sent.payload ?? { detail: "Failed" }, { status: sent.status });
}
