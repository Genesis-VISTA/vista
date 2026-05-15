// Core shared TypeScript types for the VISTA UI.
//
// Project / Skill / Upload shapes use snake_case to mirror the backend wire
// shape directly (no DTO mapping layer). Optional fields are nullable to match
// the backend's `str | None` columns.
//
// Vercel AI SDK v5 provides UIMessage / UIMessagePart types — these only cover
// domain objects that the SDK doesn't know about and the custom `data-*` parts
// streamed from PydanticAI's VercelAIAdapter.

import type { UIMessage, UIDataTypes } from "ai";

// ── Projects ────────────────────────────────────────────────────────────────

export interface Project {
  id: string;
  name: string;
  description: string | null;
  system_prompt: string | null;
  skills: string[];
  tools: string[];          // fnmatch patterns; "!pattern" to deny
  usage_limits: Record<string, unknown>;
}

export interface ProjectCreate {
  name: string;
  description: string | null;
  system_prompt: string | null;
  skills: string[];
  tools: string[];
  usage_limits: Record<string, unknown>;
}

// ── Skills ───────────────────────────────────────────────────────────────────

export interface SkillSummary {
  name: string;
  description: string;
  license?: string | null;
  compatibility?: string | null;
  allowed_tools?: string | null;
  tags?: string[];
  metadata?: Record<string, unknown>;
}

export interface SkillDetail extends SkillSummary {
  body: string;
}

// ── Uploads ──────────────────────────────────────────────────────────────────

export interface UploadedFile {
  name: string;
  size: number;
  created: string;
  modified: string;
}

// ── Vercel AI SDK v5 custom data parts ───────────────────────────────────────
//
// The backend emits these via PydanticAI's `DataChunk(type="<name>", data=...)`.
// In the frontend they appear as parts on a UIMessage with `type === "data-<name>"`.
// See https://ai-sdk.dev/docs/ai-sdk-ui/streaming-data#data-parts

export interface LogData {
  level: string;
  area: string;
  message: string;
}

export interface HtmlOutputData {
  html: string;
}

// MCP elicitation request (form-based or URL-based).
// Field names mirror the backend's McpFormElicitationEvent / McpUrlElicitationEvent models.
export type ElicitationData =
  | {
      event_kind: "mcp_form_elicitation";
      mode: "form";
      elicitation_id: string;
      message: string;
      requested_schema: Record<string, unknown>;
    }
  | {
      event_kind: "mcp_url_elicitation";
      mode: "url";
      elicitation_id: string;
      message: string;
      url: string;
    };

// Union of all the custom data-part payloads our chat stream produces.
// Used to parameterise UIMessage so part.type is narrowed correctly.
export interface VistaDataParts extends UIDataTypes {
  log: LogData;
  "html-output": HtmlOutputData;
  "mcp-form-elicitation": Extract<ElicitationData, { mode: "form" }>;
  "mcp-url-elicitation": Extract<ElicitationData, { mode: "url" }>;
}

export type VistaUIMessage = UIMessage<unknown, VistaDataParts>;
