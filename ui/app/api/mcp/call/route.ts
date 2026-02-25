import { NextResponse } from "next/server";
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

function extractPlotPath(stdout: string): string | null {
  const match = stdout.match(/Plot saved to\s+(.+)/);
  if (!match) return null;
  return match[1].trim();
}

function extractMetric(stdout: string, name: string): number | null {
  const regex = new RegExp(`${name}:\\s*(\\d+)`, "i");
  const match = stdout.match(regex);
  if (!match) return null;
  const value = Number(match[1]);
  return Number.isFinite(value) ? value : null;
}

function extractReferencesCount(stdout: string): number | null {
  const referencesSection = stdout.split("References");
  if (referencesSection.length < 2) return null;
  const tail = referencesSection[referencesSection.length - 1];
  const matches = tail.match(/\[\d+\]/g);
  return matches ? matches.length : 0;
}

function toImageUiHtml(mimeType: string, base64Data: string): string {
  const src = `data:${mimeType};base64,${base64Data}`;
  return `<img src="${src}" alt="MCP tool image output" style="max-width:100%;height:auto;display:block;margin:0 auto;" />`;
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

    if (tool === "execute_skill_script" && envelope.stdout) {
      const plotPath = extractPlotPath(envelope.stdout);
      if (plotPath) {
        envelope.artifacts.push({
          type: "plot",
          name: "analysis_plot",
          url: plotPath
        });
      }

      envelope.meta = {
        ...envelope.meta,
        analysisSummary: {
          measurements: extractMetric(envelope.stdout, "Total measurements"),
          compositions: extractMetric(envelope.stdout, "Number of compositions"),
          references: extractReferencesCount(envelope.stdout),
          plotPath
        }
      };
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

    const normalized = normalizeMcpResult(json, tool);
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
