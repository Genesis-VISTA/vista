import { NextResponse } from "next/server";
import { readFile } from "node:fs/promises";
import { extname } from "node:path";
import type { ExecutionResult } from "@/lib/types";
import { backendUrl } from "../../_backend";

export const runtime = "nodejs";

/**
 * Proxy for `POST /mcp/call`.
 *
 * The Python backend returns the raw MCP `CallToolResult` (a `{content[],
 * isError}` envelope). The frontend expects our richer `ExecutionResult`
 * (stdout/stderr/ui/...), so we normalize the response on the way back —
 * the same transformation the old Next.js route did against the MCP SDK.
 */

function createEnvelope(partial: Partial<ExecutionResult>): ExecutionResult {
  return {
    ok: false,
    stdout: "",
    stderr: "",
    data: undefined,
    artifacts: [],
    meta: {},
    ui: { kind: "none" },
    ...partial,
  };
}

function toImageUiHtml(mimeType: string, base64Data: string): string {
  const src = `data:${mimeType};base64,${base64Data}`;
  return `<img src="${src}" alt="MCP tool image output" style="max-width:100%;height:auto;display:block;margin:0 auto;" />`;
}

function extractUiHtml(payload: unknown): string | null {
  if (!payload) return null;
  if (typeof payload === "string") return null;
  if (Array.isArray(payload)) {
    for (const item of payload) {
      const html = extractUiHtml(item);
      if (html) return html;
    }
    return null;
  }
  if (typeof payload !== "object") return null;

  const obj = payload as Record<string, unknown>;
  if (typeof obj.rawHtml === "string") return obj.rawHtml;
  if (typeof obj.htmlString === "string") return obj.htmlString;
  if (typeof obj.html === "string") return obj.html;
  if (obj.resource && typeof obj.resource === "object") {
    const resource = obj.resource as Record<string, unknown>;
    const mimeType = typeof resource.mimeType === "string" ? resource.mimeType : "";
    if (mimeType.includes("text/html")) {
      if (typeof resource.text === "string") return resource.text;
    }
  }
  if (Array.isArray(obj.contents)) return extractUiHtml(obj.contents);
  if (Array.isArray(obj.content)) return extractUiHtml(obj.content);
  return null;
}

function getMimeTypeFromPath(filePath: string): string {
  const ext = extname(filePath).toLowerCase();
  if (ext === ".png") return "image/png";
  if (ext === ".jpg" || ext === ".jpeg") return "image/jpeg";
  if (ext === ".gif") return "image/gif";
  if (ext === ".webp") return "image/webp";
  if (ext === ".svg") return "image/svg+xml";
  return "application/octet-stream";
}

function findUriDeep(payload: unknown, depth = 0): string | null {
  if (depth > 6 || payload == null) return null;

  if (typeof payload === "string") {
    const trimmed = payload.trim();
    if (trimmed.startsWith("file://") || trimmed.startsWith("/")) return trimmed;
    if (trimmed.startsWith("{") || trimmed.startsWith("[")) {
      try {
        return findUriDeep(JSON.parse(trimmed), depth + 1);
      } catch {
        return null;
      }
    }
    const fileUriMatch = trimmed.match(/file:\/\/[^\s"']+/);
    if (fileUriMatch) return fileUriMatch[0];
    const absPathMatch = trimmed.match(/\/[^\s"']+\.(png|jpe?g|gif|webp|svg)/i);
    if (absPathMatch) return absPathMatch[0];
    return null;
  }

  if (Array.isArray(payload)) {
    for (const item of payload) {
      const found = findUriDeep(item, depth + 1);
      if (found) return found;
    }
    return null;
  }

  if (typeof payload !== "object") return null;
  const obj = payload as Record<string, unknown>;

  if (typeof obj.uri === "string") return obj.uri;
  if (typeof obj.path === "string" && obj.path.startsWith("/")) return obj.path;

  for (const value of Object.values(obj)) {
    const found = findUriDeep(value, depth + 1);
    if (found) return found;
  }
  return null;
}

/**
 * Fallback for `display_file` results that come back as a URI-only payload
 * with no rendered HTML / inline image. Reads the file off disk and inlines
 * it as a base64 <img>, mirroring the legacy Next.js direct-call route.
 */
async function addDisplayFileFallbackUi(
  envelope: ExecutionResult,
  tool: string
): Promise<ExecutionResult> {
  if (tool !== "display_file") return envelope;
  if (envelope.ui?.kind === "html") return envelope;

  const uri = findUriDeep(envelope.data) ?? findUriDeep(envelope.stdout);
  if (!uri) return envelope;

  let filePath = uri;
  if (uri.startsWith("file://")) {
    try {
      filePath = decodeURIComponent(uri.slice("file://".length));
    } catch {
      filePath = uri.slice("file://".length);
    }
  }

  if (!filePath.startsWith("/")) return envelope;

  try {
    const bytes = await readFile(filePath);
    const mimeType = getMimeTypeFromPath(filePath);
    if (!mimeType.startsWith("image/")) return envelope;
    const base64Data = bytes.toString("base64");
    return {
      ...envelope,
      ui: { kind: "html", html: toImageUiHtml(mimeType, base64Data) },
    };
  } catch {
    return envelope;
  }
}

function normalizeCallToolResult(raw: Record<string, unknown>, tool: string): ExecutionResult {
  const envelope = createEnvelope({ ok: !raw.isError, meta: { tool } });
  const content = Array.isArray(raw.content) ? raw.content : [];

  for (const item of content) {
    if (!item || typeof item !== "object") continue;
    const ci = item as Record<string, unknown>;

    if (ci.type === "text" && typeof ci.text === "string") {
      envelope.stdout += envelope.stdout ? `\n${ci.text}` : ci.text;
    }

    if (ci.type === "resource") {
      const resource = ci.resource as Record<string, unknown> | undefined;
      const mimeType = typeof resource?.mimeType === "string" ? resource.mimeType : "";
      const htmlText = typeof resource?.text === "string" ? resource.text : null;
      if (envelope.ui?.kind !== "html" && mimeType.includes("text/html") && htmlText) {
        envelope.ui = { kind: "html", html: htmlText };
      }
    }

    if (
      envelope.ui?.kind !== "html" &&
      ci.type === "image" &&
      typeof ci.data === "string" &&
      typeof ci.mimeType === "string"
    ) {
      envelope.ui = {
        kind: "html",
        html: toImageUiHtml(ci.mimeType as string, ci.data as string),
      };
    }
  }

  const uiFromResult = extractUiHtml(raw);
  if (uiFromResult && envelope.ui?.kind !== "html") {
    envelope.ui = { kind: "html", html: uiFromResult };
  }

  if (Object.keys(raw).length > 0) {
    envelope.data = raw;
  }

  if (envelope.ui?.kind !== "html" && envelope.stdout.includes("<img")) {
    envelope.ui = { kind: "html", html: envelope.stdout };
  }

  return envelope;
}

export async function POST(request: Request) {
  let tool = "";
  let args: Record<string, unknown> = {};
  let projectName: string | null = null;

  try {
    const body = await request.json();
    tool = typeof body.tool === "string" ? body.tool : "";
    args = body.args && typeof body.args === "object" && !Array.isArray(body.args) ? body.args : {};
    if (typeof body.project_name === "string" && body.project_name.length > 0) {
      projectName = body.project_name;
    }
  } catch {
    return NextResponse.json(createEnvelope({ ok: false, stderr: "Invalid JSON body." }), { status: 400 });
  }

  if (!tool) {
    return NextResponse.json(createEnvelope({ ok: false, stderr: "Missing tool name." }), { status: 400 });
  }

  // The backend enforces project scope on KB-aware tools. Forward the
  // active project name when the caller supplied one so e.g. rag_search
  // is gated to the project's `knowledge_bases` set.
  const upstreamBody: Record<string, unknown> = { name: tool, arguments: args };
  if (projectName) upstreamBody.project_name = projectName;

  let upstream: Response;
  try {
    upstream = await fetch(backendUrl("/mcp/call"), {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(upstreamBody),
      signal: AbortSignal.timeout(60000),
    });
  } catch (error) {
    const isAbort = error instanceof Error && error.name === "AbortError";
    const message = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json(
      createEnvelope({
        ok: false,
        stderr: isAbort
          ? "MCP unreachable: request timed out after 60000ms."
          : `MCP unreachable: ${message}`,
        meta: { tool, timeoutMs: 60000 },
      }),
      { status: 502 }
    );
  }

  if (!upstream.ok) {
    const detail = await upstream.text().catch(() => "");
    return NextResponse.json(
      createEnvelope({
        ok: false,
        stderr: detail || `Backend returned ${upstream.status}`,
        meta: { tool },
      }),
      { status: upstream.status }
    );
  }

  const raw = (await upstream.json()) as Record<string, unknown>;
  const envelope = normalizeCallToolResult(raw, tool);
  const withFallback = await addDisplayFileFallbackUi(envelope, tool);
  return NextResponse.json(withFallback);
}
