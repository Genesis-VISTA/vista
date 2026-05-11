import { NextResponse } from "next/server";
import { readFile } from "node:fs/promises";
import {
  getPublicationFile,
  isValidSlug,
  removePublication,
} from "@/lib/knowledge-bases-server";

export const runtime = "nodejs";

export async function GET(
  _request: Request,
  { params }: { params: Promise<{ slug: string; filename: string }> }
) {
  const { slug, filename } = await params;
  if (!isValidSlug(slug)) {
    return NextResponse.json({ ok: false, error: "Invalid slug." }, { status: 400 });
  }
  const decoded = decodeURIComponent(filename);
  const found = await getPublicationFile(slug, decoded);
  if (!found) {
    return NextResponse.json({ ok: false, error: "Not found." }, { status: 404 });
  }
  const bytes = await readFile(found.path);
  return new NextResponse(bytes, {
    status: 200,
    headers: {
      "content-type": "application/pdf",
      "content-disposition": `inline; filename="${decoded}"`,
    },
  });
}

export async function DELETE(
  _request: Request,
  { params }: { params: Promise<{ slug: string; filename: string }> }
) {
  const { slug, filename } = await params;
  if (!isValidSlug(slug)) {
    return NextResponse.json({ ok: false, error: "Invalid slug." }, { status: 400 });
  }
  const decoded = decodeURIComponent(filename);
  try {
    const kb = await removePublication(slug, decoded);
    return NextResponse.json({ ok: true, kb });
  } catch (err) {
    const msg = err instanceof Error ? err.message : "Failed to delete publication.";
    return NextResponse.json({ ok: false, error: msg }, { status: 400 });
  }
}
