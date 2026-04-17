import { NextResponse } from "next/server";
import { mkdir, readdir, readFile, stat, writeFile } from "node:fs/promises";
import path from "node:path";
import { config } from "@/app/config";

export const runtime = "nodejs";

const MAX_UPLOAD_SIZE_BYTES = 20 * 1024 * 1024;
const UPLOAD_MANIFEST_NAME = ".upload-manifest.json";

type UploadManifest = {
  uploaded: string[];
};

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

async function readUploadManifest(): Promise<Set<string>> {
  const manifestPath = path.join(config.uploadsDir, UPLOAD_MANIFEST_NAME);
  try {
    const raw = await readFile(manifestPath, "utf-8");
    const parsed = JSON.parse(raw) as UploadManifest;
    if (!Array.isArray(parsed.uploaded)) return new Set();
    return new Set(parsed.uploaded.filter((name): name is string => typeof name === "string" && name.length > 0));
  } catch {
    return new Set();
  }
}

async function uploadManifestExists(): Promise<boolean> {
  const manifestPath = path.join(config.uploadsDir, UPLOAD_MANIFEST_NAME);
  try {
    await stat(manifestPath);
    return true;
  } catch {
    return false;
  }
}

async function writeUploadManifest(uploaded: Set<string>) {
  const manifestPath = path.join(config.uploadsDir, UPLOAD_MANIFEST_NAME);
  const payload: UploadManifest = {
    uploaded: Array.from(uploaded).sort()
  };
  await writeFile(manifestPath, JSON.stringify(payload, null, 2), "utf-8");
}

export async function GET() {
  try {
    await mkdir(config.uploadsDir, { recursive: true });
    const names = await readdir(config.uploadsDir);
    const manifestExists = await uploadManifestExists();
    const uploadedNames = await readUploadManifest();
    const nonManifestNames = names.filter((name) => name !== UPLOAD_MANIFEST_NAME);

    // Backfill legacy installs: before manifest tracking existed, all files in this
    // directory came from uploads. Preserve that behavior once, then persist it.
    if (!manifestExists && nonManifestNames.length > 0) {
      for (const name of nonManifestNames) uploadedNames.add(name);
      await writeUploadManifest(uploadedNames);
    }

    const files = await Promise.all(
      names.map(async (name) => {
        if (name === UPLOAD_MANIFEST_NAME) return null;
        const filePath = path.join(config.uploadsDir, name);
        const stats = await stat(filePath);
        if (!stats.isFile()) return null;
        return {
          name,
          size: stats.size,
          modifiedAt: stats.mtime.toISOString(),
          source: uploadedNames.has(name) ? "upload" : "generated"
        };
      })
    );

    return NextResponse.json(
      files.filter((f): f is { name: string; size: number; modifiedAt: string; source: "upload" | "generated" } => Boolean(f))
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
    const existingNames = new Set(
      (await readdir(config.uploadsDir)).filter((name) => name !== UPLOAD_MANIFEST_NAME)
    );
    const uploadManifest = await readUploadManifest();
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
      uploadManifest.add(safeName);
    }

    await writeUploadManifest(uploadManifest);

    return NextResponse.json({ ok: true, saved });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unknown upload error.";
    return NextResponse.json({ ok: false, error: message }, { status: 500 });
  }
}
