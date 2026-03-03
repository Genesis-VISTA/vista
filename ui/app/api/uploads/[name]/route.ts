import { NextResponse } from "next/server";
import { readFile, stat, unlink } from "node:fs/promises";
import path from "node:path";
import { config } from "@/app/config";

export const runtime = "nodejs";

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

export async function GET(_request: Request, { params }: { params: { name: string } }) {
  const safeName = getSafeName(params.name);
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

export async function DELETE(_request: Request, { params }: { params: { name: string } }) {
  const safeName = getSafeName(params.name);
  if (!safeName) {
    return NextResponse.json({ ok: false, error: "Invalid file name." }, { status: 400 });
  }

  const filePath = path.join(config.uploadsDir, safeName);
  try {
    await unlink(filePath);
    return NextResponse.json({ ok: true });
  } catch {
    return NextResponse.json({ ok: false, error: "Failed to delete file." }, { status: 404 });
  }
}
