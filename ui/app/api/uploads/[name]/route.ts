import { NextResponse } from "next/server";
import { readFile, stat, unlink, writeFile } from "node:fs/promises";
import path from "node:path";
import { config } from "@/app/config";

export const runtime = "nodejs";
const UPLOAD_MANIFEST_NAME = ".upload-manifest.json";

type UploadManifest = {
  uploaded: string[];
};

function getSafeName(rawName: string): string | null {
  if (!rawName) return null;
  const decoded = decodeURIComponent(rawName);
  const base = path.basename(decoded);
  if (!base || base !== decoded) return null;
  return base;
}

function contentTypeForFile(filePath: string): string {
  const ext = path.extname(filePath).toLowerCase();
  if (ext === ".png") return "image/png";
  if (ext === ".jpg" || ext === ".jpeg") return "image/jpeg";
  if (ext === ".gif") return "image/gif";
  if (ext === ".webp") return "image/webp";
  if (ext === ".svg") return "image/svg+xml";
  if (ext === ".csv") return "text/csv; charset=utf-8";
  if (ext === ".json") return "application/json; charset=utf-8";
  if (ext === ".txt") return "text/plain; charset=utf-8";
  return "application/octet-stream";
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

async function writeUploadManifest(uploaded: Set<string>) {
  const manifestPath = path.join(config.uploadsDir, UPLOAD_MANIFEST_NAME);
  const payload: UploadManifest = {
    uploaded: Array.from(uploaded).sort()
  };
  await writeFile(manifestPath, JSON.stringify(payload, null, 2), "utf-8");
}

export async function GET(_request: Request, { params }: { params: Promise<{ name: string }> }) {
  const safeName = getSafeName((await params).name);
  if (!safeName) {
    return NextResponse.json({ ok: false, error: "Invalid file name." }, { status: 400 });
  }

  const filePath = path.join(config.uploadsDir, safeName);
  try {
    const stats = await stat(filePath);
    if (!stats.isFile()) {
      return NextResponse.json({ ok: false, error: "Not found." }, { status: 404 });
    }
    const bytes = await readFile(filePath);
    return new NextResponse(bytes, {
      status: 200,
      headers: {
        "content-type": contentTypeForFile(filePath),
        "content-disposition": `attachment; filename="${safeName}"`
      }
    });
  } catch {
    return NextResponse.json({ ok: false, error: "Not found." }, { status: 404 });
  }
}

export async function DELETE(_request: Request, { params }: { params: Promise<{ name: string }> }) {
  const safeName = getSafeName((await params).name);
  if (!safeName) {
    return NextResponse.json({ ok: false, error: "Invalid file name." }, { status: 400 });
  }

  const filePath = path.join(config.uploadsDir, safeName);
  try {
    await unlink(filePath);
    const manifest = await readUploadManifest();
    if (manifest.delete(safeName)) {
      await writeUploadManifest(manifest);
    }
    return NextResponse.json({ ok: true });
  } catch {
    return NextResponse.json({ ok: false, error: "Failed to delete file." }, { status: 404 });
  }
}
