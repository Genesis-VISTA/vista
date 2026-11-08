import { NextResponse } from "next/server";
import { SLUG } from "@/lib/kb-sharing";
import { postBackend } from "../../../skills/import/_imported";

export const runtime = "nodejs";

/**
 * Proxy for the backend's `POST /knowledge-bases/{slug}/copy`, body
 * `{ team_id }`: copy the knowledge base, PDFs and index, to a team on the
 * collaboration server, which can take as long as a publish. A success is the
 * backend's `TeamCopyResult`; errors pass through with their status and `detail`.
 */
export async function POST(
  request: Request,
  { params }: { params: Promise<{ slug: string }> }
) {
  const { slug } = await params;
  if (!SLUG.test(slug)) {
    return NextResponse.json({ detail: "Invalid slug." }, { status: 400 });
  }
  const sent = await postBackend(request, `/knowledge-bases/${slug}/copy`);
  if (sent instanceof NextResponse) return sent;
  return NextResponse.json(sent.payload ?? { detail: "Failed" }, { status: sent.status });
}
