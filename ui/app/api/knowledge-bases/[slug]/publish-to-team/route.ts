import { NextResponse } from "next/server";
import { SLUG } from "@/lib/kb-sharing";
import { postBackend } from "../../../skills/import/_imported";

export const runtime = "nodejs";

/**
 * Proxy for the backend's `POST /knowledge-bases/{slug}/publish-to-team`, body
 * `{ team_id }`: publish a private knowledge base, PDFs and index, for the
 * first time, into a team on the collaboration server, which can take as long
 * as a publish. A success is the backend's `TeamPublishResult`; errors pass
 * through with their status and `detail` (400 with code `not_in_team` for a
 * team the researcher is not in, 400 for a knowledge base in a team already).
 */
export async function POST(
  request: Request,
  { params }: { params: Promise<{ slug: string }> }
) {
  const { slug } = await params;
  if (!SLUG.test(slug)) {
    return NextResponse.json({ detail: "Invalid slug." }, { status: 400 });
  }
  const sent = await postBackend(request, `/knowledge-bases/${slug}/publish-to-team`);
  if (sent instanceof NextResponse) return sent;
  return NextResponse.json(sent.payload ?? { detail: "Failed" }, { status: sent.status });
}
