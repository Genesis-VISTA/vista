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
  /**
   * The tool result part. PydanticAI 1.105 renamed this field from `result`
   * to `part` (`result` survives only as a deprecated, non-serialized Python
   * property, so it no longer appears on the wire). `result` is kept here as
   * an optional fallback for older backends.
   */
  part: ToolReturnPart | RetryPromptPart;
  result?: ToolReturnPart | RetryPromptPart;
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
export type McpToolApprovalEvent = {
  event_kind: "mcp_tool_approval";
  mode: "tool_approval";
  elicitation_id: string;
  tool_name: string;
  message: string;
  args?: Record<string, unknown> | null;
  /**
   * PALISADE gate decision metadata (e.g. G5's fast-tier summary:
   * resolved SLURM script, account verified, resource ceiling passed,
   * no denylist match). Loose by design — the modal renders what's set.
   */
  decision_metadata?: Record<string, unknown> | null;
};
export type McpElicitationEvent =
  | McpFormElicitationEvent
  | McpUrlElicitationEvent
  | McpToolApprovalEvent;

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
  /** Git remote the project's Hypothesis Lab publishes to; null means no lab. */
  forum_repo_url: string | null;
  tools: string[];
  usage_limits: Record<string, unknown>;
};

/** Backend `UserPublic` — returned by `GET /projects/{name}/members`. */
export type UserPublic = {
  id: string;
  email: string;
  is_admin: boolean;
};

/** Backend `ProjectCreate` — body for `POST /projects` and `PUT /projects/{name}`. */
export type ProjectCreate = {
  name: string;
  description: string | null;
  system_prompt: string | null;
  skills: string[];
  knowledge_bases: string[];
  forum_repo_url: string | null;
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

export type ToolReturnFile = { url: string; mimeType?: string; name?: string };

/**
 * Walk an arbitrary tool-return `content` value looking for a `display_file`-style
 * payload: a record carrying a string `uri` (the backend download URL, plus optional
 * `mime_type`/`filename`).
 *
 * `display_file` returns `{uri, mime_type, filename}`; PydanticAI surfaces the
 * structured object as `ToolReturnPart.content` directly, but we walk defensively
 * in case it's wrapped/nested.
 */
export function fileFromToolReturnContent(
  content: unknown,
  depth = 0
): ToolReturnFile | null {
  if (depth > 6 || content == null) return null;

  if (Array.isArray(content)) {
    for (const item of content) {
      const hit = fileFromToolReturnContent(item, depth + 1);
      if (hit) return hit;
    }
    return null;
  }

  if (!isRecord(content)) return null;

  if (typeof content.uri === "string" && content.uri.length > 0) {
    return {
      url: content.uri,
      mimeType: typeof content.mime_type === "string" ? content.mime_type : undefined,
      name: typeof content.filename === "string" ? content.filename : undefined,
    };
  }

  for (const value of Object.values(content)) {
    const hit = fileFromToolReturnContent(value, depth + 1);
    if (hit) return hit;
  }
  return null;
}

/**
 * Walk an arbitrary tool-return `content` value looking for renderable HTML.
 *
 * Used for tools that emit raw HTML (svg/table/div). We handle PydanticAI
 * `MultiModalContent` objects and raw MCP content blocks defensively.
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

/**
 * Plain text out of a tool return, for the panels that read stdout.
 *
 * The salt quick-actions used to be the only path that filled the prediction
 * summary and references boxes, because they called the MCP route directly and
 * got an `ExecutionResult` with real stdout back. An agent run reaches the UI
 * as tool-return content instead, and that content was being dropped. This
 * pulls the text out of the shapes PydanticAI and MCP actually send, so those
 * panels work from an agent turn.
 *
 * Text only. HTML and files already have their own extractors above.
 */
export function textFromToolReturnContent(content: unknown, depth = 0): string | null {
  if (depth > 6 || content == null) return null;

  if (typeof content === "string") {
    return content.trim() ? content : null;
  }

  if (Array.isArray(content)) {
    const parts = content
      .map((item) => textFromToolReturnContent(item, depth + 1))
      .filter((part): part is string => Boolean(part));
    return parts.length > 0 ? parts.join("\n") : null;
  }

  if (!isRecord(content)) return null;

  // PydanticAI MultiModalContent, then raw MCP content blocks.
  if (content.kind === "text-content" && typeof content.text === "string") {
    return content.text.trim() ? content.text : null;
  }
  if (content.type === "text" && typeof content.text === "string") {
    return content.text.trim() ? content.text : null;
  }
  if (content.type === "resource" && isRecord(content.resource)) {
    const resource = content.resource;
    const mime = typeof resource.mimeType === "string" ? resource.mimeType : "";
    if (typeof resource.text === "string" && !mime.includes("html")) {
      return resource.text.trim() ? resource.text : null;
    }
  }

  // The shape an MCP tool returns when it wraps a process run.
  for (const key of ["stdout", "text", "output"]) {
    const value = content[key];
    if (typeof value === "string" && value.trim()) return value;
  }

  for (const value of Object.values(content)) {
    const hit = textFromToolReturnContent(value, depth + 1);
    if (hit) return hit;
  }

  return null;
}
