import { NextResponse } from "next/server";
import { SLUG } from "@/lib/kb-sharing";
import { postBackend } from "../../../skills/import/_imported";

export const runtime = "nodejs";

/**
 * Proxy for the backend's `POST /knowledge-bases/{slug}/publish`: publish the
 * knowledge base's PDFs and index. Body `{ on_top_of?, as_new? }`. A success is
 * `{ status, knowledge_base }`; errors pass through with their status and
 * body, an error's `code` (`version_conflict` with `head_version`,
 * `remote_missing`, `collaboration_unavailable`, …) telling the page what to
 * offer. A 409 without a code is a knowledge base still indexing.
 */
export async function POST(
  request: Request,
  { params }: { params: Promise<{ slug: string }> }
) {
  const { slug } = await params;
  if (!SLUG.test(slug)) {
    return NextResponse.json({ detail: "Invalid slug." }, { status: 400 });
  }
  const sent = await postBackend(request, `/knowledge-bases/${slug}/publish`);
  if (sent instanceof NextResponse) return sent;
  return NextResponse.json(sent.payload ?? { detail: "Failed" }, { status: sent.status });
}
