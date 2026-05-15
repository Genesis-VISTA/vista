"use client";

// Top-right panel: most-recent tool HTML output rendered in a sandboxed iframe.
//
// The backend (and tools it calls) returns MCP CallToolResult content blocks.
// PydanticAI's VercelAIAdapter exposes those as `output-available` parts on
// the assistant message — we walk the message stream for the latest one whose
// payload looks like HTML and render it.

import { useMemo } from "react";
import { isToolUIPart, getToolOrDynamicToolName } from "ai";
import type { UIMessagePart } from "ai";
import SandboxedHtmlCard from "@/components/SandboxedHtmlCard";
import type { VistaUIMessage } from "@/lib/types";

interface LatestOutputProps {
  messages: VistaUIMessage[];
}

// Recursively scan a tool's output payload for the first HTML string. MCP
// returns `{ content: [{ type: "text" | "resource", ... }] }`, but tools can
// also produce structured JSON; this matches both shapes loosely.
function findHtml(value: unknown): string | null {
  if (value == null) return null;
  if (typeof value === "string") {
    return /<\s*(html|body|div|svg|table|figure|img)\b/i.test(value) ? value : null;
  }
  if (Array.isArray(value)) {
    for (const item of value) {
      const html = findHtml(item);
      if (html) return html;
    }
    return null;
  }
  if (typeof value === "object") {
    const obj = value as Record<string, unknown>;
    // MCP content block: { type: "resource"|"text", text?, resource?: { mimeType, text } }
    if (typeof obj.mimeType === "string" && /html/i.test(obj.mimeType)) {
      const text =
        typeof obj.text === "string"
          ? obj.text
          : typeof obj.blob === "string"
            ? obj.blob
            : null;
      if (text) return text;
    }
    if (typeof obj.html === "string") return obj.html;
    if (typeof obj.text === "string") {
      const maybe = findHtml(obj.text);
      if (maybe) return maybe;
    }
    for (const key of Object.keys(obj)) {
      if (key === "mimeType" || key === "html" || key === "text") continue;
      const html = findHtml(obj[key]);
      if (html) return html;
    }
  }
  return null;
}

function latestHtml(messages: VistaUIMessage[]): string | null {
  // Walk newest → oldest, newest part within each message first.
  for (let m = messages.length - 1; m >= 0; m--) {
    const parts = messages[m].parts as UIMessagePart<Record<string, never>, Record<string, never>>[];
    for (let p = parts.length - 1; p >= 0; p--) {
      const part = parts[p];
      if (part.type === "dynamic-tool" || isToolUIPart(part)) {
        if (part.state === "output-available") {
          const html = findHtml(part.output);
          if (html) return html;
        }
      } else if (part.type.startsWith("data-html-output")) {
        const data = (part as { data?: { html?: string } }).data;
        if (data?.html) return data.html;
      }
    }
  }
  return null;
}

export default function LatestOutput({ messages }: LatestOutputProps) {
  const html = useMemo(() => latestHtml(messages), [messages]);
  const latestTool = useMemo(() => {
    for (let m = messages.length - 1; m >= 0; m--) {
      const parts = messages[m].parts;
      for (let p = parts.length - 1; p >= 0; p--) {
        const part = parts[p];
        if (part.type === "dynamic-tool") return part.toolName;
        if (isToolUIPart(part)) return getToolOrDynamicToolName(part) as string;
      }
    }
    return null;
  }, [messages]);

  return (
    <div className="flex-1 min-h-0 flex flex-col border-b border-gray-200">
      <div className="px-3 py-2 border-b border-gray-200">
        <div className="text-sm font-semibold uppercase tracking-widest text-gray-700">
          Latest Output
        </div>
        {latestTool && (
          <div className="text-[11px] text-gray-500 mt-0.5">from {latestTool}</div>
        )}
      </div>
      <div className="flex-1 overflow-auto p-3">
        {html ? (
          <SandboxedHtmlCard html={html} />
        ) : (
          <div className="rounded-md border border-gray-200 bg-gray-50 px-3 py-2 text-sm text-gray-600">
            No figure yet.
          </div>
        )}
      </div>
    </div>
  );
}
