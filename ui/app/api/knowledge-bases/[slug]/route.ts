import { NextResponse } from "next/server";
import {
  deleteKnowledgeBase,
  getKnowledgeBase,
  isValidSlug,
  updateKnowledgeBase,
} from "@/lib/knowledge-bases-server";

export const runtime = "nodejs";

export async function GET(_request: Request, { params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  if (!isValidSlug(slug)) {
    return NextResponse.json({ ok: false, error: "Invalid slug." }, { status: 400 });
  }
  const kb = await getKnowledgeBase(slug);
  if (!kb) {
    return NextResponse.json({ ok: false, error: "Not found." }, { status: 404 });
  }
  return NextResponse.json(kb);
}

export async function PATCH(
  request: Request,
  { params }: { params: Promise<{ slug: string }> }
) {
  const { slug } = await params;
  if (!isValidSlug(slug)) {
    return NextResponse.json({ ok: false, error: "Invalid slug." }, { status: 400 });
  }
  let body: { name?: string; description?: string };
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ ok: false, error: "Invalid JSON body." }, { status: 400 });
  }
  const patch: { name?: string; description?: string } = {};
  if (typeof body.name === "string") patch.name = body.name;
  if (typeof body.description === "string") patch.description = body.description;
  try {
    const kb = await updateKnowledgeBase(slug, patch);
    return NextResponse.json({ ok: true, kb });
  } catch (err) {
    const msg = err instanceof Error ? err.message : "Failed to update.";
    return NextResponse.json({ ok: false, error: msg }, { status: 400 });
  }
}

export async function DELETE(
  _request: Request,
  { params }: { params: Promise<{ slug: string }> }
) {
  const { slug } = await params;
  if (!isValidSlug(slug)) {
    return NextResponse.json({ ok: false, error: "Invalid slug." }, { status: 400 });
  }
  try {
    await deleteKnowledgeBase(slug);
    return NextResponse.json({ ok: true });
  } catch (err) {
    const msg = err instanceof Error ? err.message : "Failed to delete.";
    return NextResponse.json({ ok: false, error: msg }, { status: 400 });
  }
}
