import { NextResponse } from "next/server";
import {
  MAX_PDF_SIZE_BYTES,
  addPublications,
  isValidSlug,
} from "@/lib/knowledge-bases-server";

export const runtime = "nodejs";

/**
 * Add one or more PDFs to an existing KB.
 * Multipart form: `files` (1+ File entries).
 */
export async function POST(
  request: Request,
  { params }: { params: Promise<{ slug: string }> }
) {
  const { slug } = await params;
  if (!isValidSlug(slug)) {
    return NextResponse.json({ ok: false, error: "Invalid slug." }, { status: 400 });
  }
  try {
    const form = await request.formData();
    const files = form.getAll("files").filter((entry): entry is File => entry instanceof File);
    if (files.length === 0) {
      return NextResponse.json(
        { ok: false, error: "No files were provided." },
        { status: 400 }
      );
    }
    for (const f of files) {
      if (f.size > MAX_PDF_SIZE_BYTES) {
        return NextResponse.json(
          {
            ok: false,
            error: `File '${f.name}' exceeds the ${MAX_PDF_SIZE_BYTES / (1024 * 1024)}MB PDF size limit.`,
          },
          { status: 400 }
        );
      }
    }
    const buffers = await Promise.all(
      files.map(async (f) => ({ name: f.name, bytes: Buffer.from(await f.arrayBuffer()) }))
    );
    const kb = await addPublications(slug, buffers);
    return NextResponse.json({ ok: true, kb });
  } catch (err) {
    const msg = err instanceof Error ? err.message : "Failed to add publications.";
    return NextResponse.json({ ok: false, error: msg }, { status: 400 });
  }
}
