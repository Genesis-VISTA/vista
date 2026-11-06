import { NextResponse } from "next/server";
import { postBackend } from "../../../skills/import/_imported";

export const runtime = "nodejs";

/**
 * Proxy for the backend's `POST /projects/{name}/publish-to-team`, body
 * `{ team_id }`: publish a private project for the first time, into a team on
 * the collaboration server, its skills and knowledge bases first. A success is
 * the backend's `TeamPublishResult`, the project last; errors pass through with
 * their status and `detail` (400 for a project in a team already, or when a
 * skill or knowledge base it uses is missing or in a team).
 */
export async function POST(
  request: Request,
  { params }: { params: Promise<{ name: string }> }
) {
  const { name } = await params;
  const sent = await postBackend(request, `/projects/${encodeURIComponent(name)}/publish-to-team`);
  if (sent instanceof NextResponse) return sent;
  return NextResponse.json(sent.payload ?? { detail: "Failed" }, { status: sent.status });
}
