import { NextResponse } from "next/server";
import { postBackend } from "../../../skills/import/_imported";

export const runtime = "nodejs";

/**
 * Proxy for the backend's `POST /projects/{name}/publish`: publish the project,
 * after the skills it loads that are not on the collaboration server or have
 * changed. Body `{ on_top_of?, as_new? }`. Success and errors pass through with
 * their status and body; an error's `code` (`version_conflict` with
 * `head_version`, `remote_missing`, `skill_conflict` with `skill`,
 * `collaboration_unavailable`, …) tells the page what to offer.
 */
export async function POST(
  request: Request,
  { params }: { params: Promise<{ name: string }> }
) {
  const { name } = await params;
  const sent = await postBackend(request, `/projects/${encodeURIComponent(name)}/publish`);
  if (sent instanceof NextResponse) return sent;
  return NextResponse.json(sent.payload ?? { detail: "Failed" }, { status: sent.status });
}
