import { NextResponse } from "next/server";
import {
  MAX_PDF_SIZE_BYTES,
  addPublications,
  createKnowledgeBase,
  listKnowledgeBases,
} from "@/lib/knowledge-bases-server";

export const runtime = "nodejs";

export async function GET() {
  try {
    const kbs = await listKnowledgeBases();
    return NextResponse.json(kbs);
  } catch (err) {
    console.error("KB list failed:", err);
    return NextResponse.json([], { status: 200 });
  }
}

/**
 * Create a new KB. Accepts multipart/form-data so the user can upload
 * the initial set of PDFs in the same request:
 *   - name        (string, required)
 *   - description (string, optional)
 *   - files       (one or more File entries, optional)
 */
export async function POST(request: Request) {
  try {
    const form = await request.formData();
    const name = String(form.get("name") ?? "").trim();
    const description = String(form.get("description") ?? "").trim();
    const slugInput = form.get("slug");
    const slug = typeof slugInput === "string" && slugInput.trim() ? slugInput.trim() : undefined;

    if (!name) {
      return NextResponse.json(
        { ok: false, error: "Name is required." },
        { status: 400 }
      );
    }

    let kb;
    try {
      kb = await createKnowledgeBase({ name, description, slug });
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Failed to create knowledge base.";
      return NextResponse.json({ ok: false, error: msg }, { status: 400 });
    }

    const files = form.getAll("files").filter((entry): entry is File => entry instanceof File);
    if (files.length > 0) {
      // Pre-validate sizes to give a clean error before doing any disk work
      // beyond creating the KB skeleton.
      for (const f of files) {
        if (f.size > MAX_PDF_SIZE_BYTES) {
          return NextResponse.json(
            {
              ok: false,
              error: `File '${f.name}' exceeds the ${MAX_PDF_SIZE_BYTES / (1024 * 1024)}MB PDF size limit.`,
              kb,
            },
            { status: 400 }
          );
        }
      }
      const buffers = await Promise.all(
        files.map(async (f) => ({ name: f.name, bytes: Buffer.from(await f.arrayBuffer()) }))
      );
      try {
        kb = await addPublications(kb.slug, buffers);
      } catch (err) {
        const msg = err instanceof Error ? err.message : "Failed to attach PDFs.";
        return NextResponse.json({ ok: false, error: msg, kb }, { status: 400 });
      }
    }

    return NextResponse.json({ ok: true, kb });
  } catch (err) {
    console.error("KB create failed:", err);
    return NextResponse.json(
      { ok: false, error: "Failed to create knowledge base." },
      { status: 500 }
    );
  }
}
