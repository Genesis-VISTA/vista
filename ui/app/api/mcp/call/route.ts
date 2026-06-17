import { NextResponse } from "next/server";
import type { ExecutionResult, UiPayload } from "@/lib/types";
import { backendUrl, backendHeaders } from "../../_backend";

export const runtime = "nodejs";

/**
 * Proxy for `POST /projects/{project_name}/mcp/call`.
 *
 * The Python backend returns the raw MCP `CallToolResult` (a `{content[],
 * isError}` envelope). The frontend expects our richer `ExecutionResult`
 * (stdout/stderr/ui/...), so we normalize the response on the way back —
 * the same transformation the old Next.js route did against the MCP SDK.
 *
 * The project name is taken from the request body (`project_name`) and
 * threaded into the upstream URL. The backend filters the tool against the
 * project's allow/deny set, so calls are gated to whatever that project can
 * actually invoke.
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

/**
 * Detect a `display_file`-style structured payload: a record with a string `uri`
 * (the backend download URL, plus optional `mime_type`/`filename`). `display_file`
 * returns its result as `structuredContent` on the `CallToolResult`; the SDK may
 * wrap a single value in `{ result: ... }`, so we check that too.
 */
function fileUiFromResult(raw: Record<string, unknown>): UiPayload | null {
  const candidates: unknown[] = [raw.structuredContent];
  const structured = raw.structuredContent;
  if (structured && typeof structured === "object" && "result" in structured) {
    candidates.push((structured as Record<string, unknown>).result);
  }
  for (const candidate of candidates) {
    if (!candidate || typeof candidate !== "object") continue;
    const obj = candidate as Record<string, unknown>;
    if (typeof obj.uri === "string" && obj.uri.length > 0) {
      return {
        kind: "file",
        url: obj.uri,
        mimeType: typeof obj.mime_type === "string" ? obj.mime_type : undefined,
        name: typeof obj.filename === "string" ? obj.filename : undefined,
      };
    }
  }
  return null;
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

  // A `display_file` URL payload wins over the text/html heuristics above. Scoped to
  // `display_file` so other tools that happen to return a `uri` field aren't rendered
  // as a downloadable file.
  if (tool === "display_file") {
    const fileUi = fileUiFromResult(raw);
    if (fileUi) {
      envelope.ui = fileUi;
    }
  }

  return envelope;
}

export async function POST(request: Request) {
  let tool = "";
  let args: Record<string, unknown> = {};
  let projectName: string | null = null;
  let chatSessionId: string | null = null;

  try {
    const body = await request.json();
    tool = typeof body.tool === "string" ? body.tool : "";
    args = body.args && typeof body.args === "object" && !Array.isArray(body.args) ? body.args : {};
    if (typeof body.project_name === "string" && body.project_name.length > 0) {
      projectName = body.project_name;
    }
    if (typeof body.chat_session_id === "string" && body.chat_session_id.length > 0) {
      chatSessionId = body.chat_session_id;
    }
  } catch {
    return NextResponse.json(createEnvelope({ ok: false, stderr: "Invalid JSON body." }), { status: 400 });
  }

  if (!tool) {
    return NextResponse.json(createEnvelope({ ok: false, stderr: "Missing tool name." }), { status: 400 });
  }
  if (!projectName) {
    return NextResponse.json(createEnvelope({ ok: false, stderr: "Missing project_name." }), { status: 400 });
  }

  const upstreamBody: Record<string, unknown> = {
    name: tool,
    arguments: args,
    chat_session_id: chatSessionId,
  };

  let upstream: Response;
  try {
    upstream = await fetch(backendUrl(`/projects/${encodeURIComponent(projectName)}/mcp/call`), {
      method: "POST",
      headers: await backendHeaders({ "content-type": "application/json" }),
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
  return NextResponse.json(envelope);
}
