import { NextResponse } from "next/server";
import { mkdir, readdir, stat, writeFile } from "node:fs/promises";
import path from "node:path";
import { config } from "@/app/config";

export const runtime = "nodejs";

const MAX_UPLOAD_SIZE_BYTES = 20 * 1024 * 1024;

function sanitizeFilename(name: string): string {
  const base = path.basename(name).replace(/[^a-zA-Z0-9._-]/g, "_");
  return base || "upload.bin";
}

function ensureUniqueName(existing: Set<string>, name: string): string {
  if (!existing.has(name)) return name;

  const ext = path.extname(name);
  const stem = path.basename(name, ext);
  let i = 1;
  while (existing.has(`${stem}-${i}${ext}`)) i += 1;
  return `${stem}-${i}${ext}`;
}

export async function GET() {
  try {
    await mkdir(config.uploadsDir, { recursive: true });
    const names = await readdir(config.uploadsDir);

    const files = await Promise.all(
      names.map(async (name) => {
        const filePath = path.join(config.uploadsDir, name);
        const stats = await stat(filePath);
        if (!stats.isFile()) return null;
        return {
          name,
          size: stats.size,
          modifiedAt: stats.mtime.toISOString()
        };
      })
    );

    return NextResponse.json(
      files
        .filter((f): f is { name: string; size: number; modifiedAt: string } => Boolean(f))
        .sort((a, b) => b.modifiedAt.localeCompare(a.modifiedAt))
    );
  } catch {
    return NextResponse.json([], { status: 200 });
  }
}

export async function POST(request: Request) {
  try {
    const formData = await request.formData();
    const incoming = formData.getAll("files").filter((entry): entry is File => entry instanceof File);

    if (incoming.length === 0) {
      return NextResponse.json({ ok: false, error: "No files were provided." }, { status: 400 });
    }

    await mkdir(config.uploadsDir, { recursive: true });
    const existingNames = new Set(await readdir(config.uploadsDir));
    const saved: string[] = [];

    for (const file of incoming) {
      if (file.size > MAX_UPLOAD_SIZE_BYTES) {
        return NextResponse.json(
          {
            ok: false,
            error: `File '${file.name}' exceeds the 20MB upload limit.`
          },
          { status: 400 }
        );
      }

      const safeName = ensureUniqueName(existingNames, sanitizeFilename(file.name));
      const targetPath = path.join(config.uploadsDir, safeName);
      const buffer = Buffer.from(await file.arrayBuffer());
      await writeFile(targetPath, buffer);
      existingNames.add(safeName);
      saved.push(safeName);
    }

    return NextResponse.json({ ok: true, saved });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unknown upload error.";
    return NextResponse.json({ ok: false, error: message }, { status: 500 });
  }
}
