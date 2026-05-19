/**
 * Wire types for the backend's `POST /projects/{project_name}/agent/run` SSE
 * stream and the `/projects` CRUD API.
 *
 * These mirror PydanticAI's `AgentStreamEvent` / message model plus the
 * app-specific events the backend layers on top (`log`, `agent_run_result`,
 * `mcp_form_elicitation`, `mcp_url_elicitation`). They are intentionally loose —
 * most fields are optional — because the frontend only reads a subset; the
 * backend remains the source of truth for the full schema.
 */

/* ------------------------------------------------------------------ */
/*  Message history (opaque)                                           */
/* ------------------------------------------------------------------ */

/**
 * A PydanticAI `ModelMessage`. The frontend never constructs or mutates these —
 * it stores the `new_messages` arrays returned by each turn and replays them as
 * `message_history` on the next call.
 */
export type ModelMessage = {
  kind: "request" | "response";
  parts: unknown[];
  [k: string]: unknown;
};

/* ------------------------------------------------------------------ */
/*  Response parts (discriminator: part_kind)                          */
/* ------------------------------------------------------------------ */

export type TextPart = { part_kind: "text"; content: string };
export type ThinkingPart = { part_kind: "thinking"; content: string };
export type ToolCallPart = {
  part_kind: "tool-call" | "builtin-tool-call";
  tool_name: string;
  args: string | Record<string, unknown> | null;
  tool_call_id: string;
};
export type ToolReturnPart = {
  part_kind: "tool-return" | "builtin-tool-return";
  tool_name: string;
  content: unknown;
  tool_call_id: string;
  outcome?: "success" | "failed" | "denied";
};
export type RetryPromptPart = {
  part_kind: "retry-prompt";
  tool_name?: string | null;
  tool_call_id: string;
  content: unknown;
};

export type ModelResponsePart =
  | TextPart
  | ThinkingPart
  | ToolCallPart
  | ToolReturnPart
  | RetryPromptPart
  | { part_kind: string; [k: string]: unknown };

/* ------------------------------------------------------------------ */
/*  Part deltas (discriminator: part_delta_kind)                       */
/* ------------------------------------------------------------------ */

export type TextPartDelta = { part_delta_kind: "text"; content_delta: string };
export type ThinkingPartDelta = {
  part_delta_kind: "thinking";
  content_delta?: string | null;
};
export type ToolCallPartDelta = {
  part_delta_kind: "tool_call";
  tool_name_delta?: string | null;
  args_delta?: string | Record<string, unknown> | null;
  tool_call_id?: string | null;
};

export type ModelResponsePartDelta =
  | TextPartDelta
  | ThinkingPartDelta
  | ToolCallPartDelta
  | { part_delta_kind: string; [k: string]: unknown };

/* ------------------------------------------------------------------ */
/*  Stream events (discriminator: event_kind / SSE `event:` line)      */
/* ------------------------------------------------------------------ */

export type PartStartEvent = {
  event_kind: "part_start";
  index: number;
  part: ModelResponsePart;
};
export type PartDeltaEvent = {
  event_kind: "part_delta";
  index: number;
  delta: ModelResponsePartDelta;
};
export type PartEndEvent = {
  event_kind: "part_end";
  index: number;
  part: ModelResponsePart;
};
export type FinalResultEvent = {
  event_kind: "final_result";
  tool_name: string | null;
  tool_call_id?: string | null;
};
export type FunctionToolCallEvent = {
  event_kind: "function_tool_call";
  part: ToolCallPart;
};
export type FunctionToolResultEvent = {
  event_kind: "function_tool_result";
  result: ToolReturnPart | RetryPromptPart;
  content?: unknown;
};
export type LogEvent = {
  event_kind: "log";
  level: string;
  area: string;
  message: string;
};
export type AgentRunResultEvent = {
  event_kind: "agent_run_result";
  result: ProjectAgentResult;
};

export type McpFormElicitationEvent = {
  event_kind: "mcp_form_elicitation";
  mode: "form";
  elicitation_id: string;
  message: string;
  requested_schema: Record<string, unknown>;
};
export type McpUrlElicitationEvent = {
  event_kind: "mcp_url_elicitation";
  mode: "url";
  elicitation_id: string;
  message: string;
  url: string;
};
export type McpElicitationEvent =
  | McpFormElicitationEvent
  | McpUrlElicitationEvent;

export type ProjectAgentResult = {
  new_messages: ModelMessage[];
  usage: Record<string, unknown>;
  logs: { level: string; area: string; message: string }[];
};

/* ------------------------------------------------------------------ */
/*  Project wire shapes (/projects API)                                */
/* ------------------------------------------------------------------ */

/** Backend `ProjectPublic` — returned by `GET /projects`. */
export type ProjectPublic = {
  id: string;
  name: string;
  description: string | null;
  system_prompt: string | null;
  skills: string[];
  knowledge_bases: string[];
  tools: string[];
  usage_limits: Record<string, unknown>;
};

/** Backend `ProjectCreate` — body for `POST /projects` and `PUT /projects/{name}`. */
export type ProjectCreate = {
  name: string;
  description: string | null;
  system_prompt: string | null;
  skills: string[];
  knowledge_bases: string[];
  tools: string[];
  usage_limits: Record<string, unknown>;
};

/* ------------------------------------------------------------------ */
/*  Tool-result HTML extraction                                        */
/* ------------------------------------------------------------------ */

const HTML_RE = /<img|<svg|<table|<div|<!doctype|<html/i;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function htmlFromString(value: string): string | null {
  return HTML_RE.test(value) ? value : null;
}

/**
 * Walk an arbitrary tool-return `content` value looking for renderable HTML.
 *
 * The common case is the MCP `display_file` tool, which is typed `-> str` and
 * returns a raw `<img ...>` HTML string; PydanticAI unwraps
 * `structuredContent={"result": ...}` so `ToolReturnPart.content` is that
 * string directly. We also handle PydanticAI `MultiModalContent` objects and
 * raw MCP content blocks defensively.
 */
export function htmlFromToolReturnContent(
  content: unknown,
  depth = 0
): string | null {
  if (depth > 6 || content == null) return null;

  if (typeof content === "string") {
    return htmlFromString(content);
  }

  if (Array.isArray(content)) {
    for (const item of content) {
      const hit = htmlFromToolReturnContent(item, depth + 1);
      if (hit) return hit;
    }
    return null;
  }

  if (!isRecord(content)) return null;

  // PydanticAI MultiModalContent (discriminator: `kind`).
  const kind = content.kind;
  if (kind === "binary") {
    const mediaType = content.media_type;
    const data = content.data;
    if (
      typeof mediaType === "string" &&
      mediaType.startsWith("image/") &&
      typeof data === "string"
    ) {
      return `<img src="data:${mediaType};base64,${data}" style="max-width:100%" />`;
    }
  }
  if (kind === "image-url" && typeof content.url === "string") {
    return `<img src="${content.url}" style="max-width:100%" />`;
  }
  if (kind === "text-content" && typeof content.text === "string") {
    return htmlFromString(content.text);
  }

  // Raw MCP content blocks (discriminator: `type`).
  const type = content.type;
  if (type === "resource" && isRecord(content.resource)) {
    const resource = content.resource;
    if (
      typeof resource.mimeType === "string" &&
      resource.mimeType.includes("text/html") &&
      typeof resource.text === "string"
    ) {
      return resource.text;
    }
  }
  if (
    type === "image" &&
    typeof content.data === "string" &&
    typeof content.mimeType === "string"
  ) {
    return `<img src="data:${content.mimeType};base64,${content.data}" style="max-width:100%" />`;
  }
  if (type === "text" && typeof content.text === "string") {
    return htmlFromString(content.text);
  }

  // Common ad-hoc keys, then recurse into remaining values.
  for (const key of ["html", "rawHtml", "htmlString"]) {
    const value = content[key];
    if (typeof value === "string") {
      const hit = htmlFromString(value);
      if (hit) return hit;
    }
  }
  for (const value of Object.values(content)) {
    const hit = htmlFromToolReturnContent(value, depth + 1);
    if (hit) return hit;
  }

  return null;
}
