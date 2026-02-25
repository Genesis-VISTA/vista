import { NextResponse } from "next/server";
import { readFile } from "node:fs/promises";
import { extname } from "node:path";
import type { ExecutionResult } from "@/lib/types";
import { callMcpRpc } from "@/lib/mcp";

function createEnvelope(partial: Partial<ExecutionResult>): ExecutionResult {
  return {
    ok: false,
    stdout: "",
    stderr: "",
    data: undefined,
    artifacts: [],
    meta: {},
    ui: { kind: "none" },
    ...partial
  };
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
      if (typeof resource.rawHtml === "string") return resource.rawHtml;
      if (typeof resource.htmlString === "string") return resource.htmlString;
      if (typeof resource.html === "string") return resource.html;
    }
  }
  if (Array.isArray(obj.contents)) return extractUiHtml(obj.contents);
  if (Array.isArray(obj.content)) return extractUiHtml(obj.content);
  return null;
}

function toImageUiHtml(mimeType: string, base64Data: string): string {
  const src = `data:${mimeType};base64,${base64Data}`;
  return `<img src="${src}" alt="MCP tool image output" style="max-width:100%;height:auto;display:block;margin:0 auto;" />`;
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

async function addDisplayFileFallbackUi(envelope: ExecutionResult, tool: string): Promise<ExecutionResult> {
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
      ui: { kind: "html", html: toImageUiHtml(mimeType, base64Data) }
    };
  } catch {
    return envelope;
  }
}

function normalizeMcpResult(raw: unknown, tool: string): ExecutionResult {
  const envelope = createEnvelope({ ok: true, meta: { tool } });

  if (!raw || typeof raw !== "object") {
    envelope.data = raw;
    return envelope;
  }

  const response = raw as Record<string, unknown>;
  if (response.error) {
    const rpcError = response.error as Record<string, unknown>;
    const code = typeof rpcError?.code === "number" ? rpcError.code : undefined;
    if (code !== undefined) {
      envelope.meta = { ...envelope.meta, rpcErrorCode: code };
    }
    envelope.ok = false;
    envelope.stderr = typeof response.error === "string" ? response.error : JSON.stringify(response.error, null, 2);
    return envelope;
  }

  const result = response.result ?? response;

  if (result && typeof result === "object") {
    const resultObj = result as Record<string, unknown>;
    if (typeof resultObj.stdout === "string") envelope.stdout = resultObj.stdout;
    if (typeof resultObj.stderr === "string") envelope.stderr = resultObj.stderr;
    if (typeof resultObj.ok === "boolean") envelope.ok = resultObj.ok;
    if (resultObj.data !== undefined) envelope.data = resultObj.data;
    if (Array.isArray(resultObj.artifacts)) {
      envelope.artifacts = resultObj.artifacts
        .filter((item) => item && typeof item === "object")
        .map((item) => {
          const obj = item as Record<string, unknown>;
          return {
            type: typeof obj.type === "string" ? obj.type : "unknown",
            name: typeof obj.name === "string" ? obj.name : "artifact",
            url: typeof obj.url === "string" ? obj.url : undefined
          };
        });
    }

    const uiFromResult = extractUiHtml(resultObj);
    if (uiFromResult) {
      envelope.ui = { kind: "html", html: uiFromResult };
    }

    const content = Array.isArray(resultObj.content) ? resultObj.content : [];
    for (const item of content) {
      if (!item || typeof item !== "object") continue;
      const contentItem = item as Record<string, unknown>;
      if (contentItem.type === "text" && typeof contentItem.text === "string") {
        envelope.stdout += envelope.stdout ? `\n${contentItem.text}` : contentItem.text;
      }

      if (contentItem.type === "resource") {
        const resource = contentItem.resource as Record<string, unknown> | undefined;
        const mimeType = typeof resource?.mimeType === "string" ? resource.mimeType : "";
        const htmlText = typeof resource?.text === "string" ? resource.text : null;
        if (envelope.ui?.kind !== "html" && mimeType.includes("text/html") && htmlText) {
          envelope.ui = { kind: "html", html: htmlText };
        }
      }

      if (
        envelope.ui?.kind !== "html" &&
        contentItem.type === "image" &&
        typeof contentItem.data === "string" &&
        typeof contentItem.mimeType === "string"
      ) {
        envelope.ui = {
          kind: "html",
          html: toImageUiHtml(contentItem.mimeType, contentItem.data)
        };
      }
    }

    if (envelope.data === undefined && Object.keys(resultObj).length > 0) {
      envelope.data = resultObj;
    }

    return envelope;
  }

  envelope.data = result;
  return envelope;
}

export async function POST(request: Request) {
  let tool = "";
  let args: Record<string, unknown> = {};

  try {
    const body = await request.json();
    tool = typeof body.tool === "string" ? body.tool : "";
    args = body.args && typeof body.args === "object" && !Array.isArray(body.args) ? body.args : {};
  } catch (error) {
    return NextResponse.json(createEnvelope({ ok: false, stderr: "Invalid JSON body." }), { status: 400 });
  }

  if (!tool) {
    return NextResponse.json(createEnvelope({ ok: false, stderr: "Missing tool name." }), { status: 400 });
  }

  // TODO: Add shell runner with allowlists + sandboxing for future bash execution support.
  try {
    const { response, text: responseText, json } = await callMcpRpc(
      "tools/call",
      {
        name: tool,
        arguments: args
      },
      20000
    );

    if (!response.ok) {
      return NextResponse.json(
        createEnvelope({
          ok: false,
          stderr: `MCP error (${response.status}): ${responseText}`,
          meta: { tool, status: response.status }
        }),
        { status: 502 }
      );
    }

    const normalized = await addDisplayFileFallbackUi(normalizeMcpResult(json, tool), tool);
    return NextResponse.json(normalized);
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unknown error";
    const isAbort = error instanceof Error && error.name === "AbortError";
    return NextResponse.json(
      createEnvelope({
        ok: false,
        stderr: isAbort ? "MCP unreachable: request timed out after 20000ms." : `MCP unreachable: ${message}`,
        meta: { tool, timeoutMs: 20000 }
      }),
      { status: 502 }
    );
  }
}
