export type UiPayload = { kind: "html"; html: string } | { kind: "none" };

export type Artifact = {
  type: string;
  name: string;
  url?: string;
};

export type ExecutionResult = {
  ok: boolean;
  stdout: string;
  stderr: string;
  data?: any;
  artifacts: Artifact[];
  meta: Record<string, any>;
  ui?: UiPayload;
};

export type SkillSummary = {
  slug: string;
  name: string;
  description: string;
  path: string;
  metadata?: Record<string, string | string[]>;
  tags?: string[];
};

export type SkillDetail = {
  slug: string;
  frontmatter: Record<string, unknown>;
  markdown: string;
};

export type ChatMessage = {
  id: string;
  role: "user" | "assistant" | "system" | "tool";
  content: string;
  result?: ExecutionResult;
  /**
   * Streamed mid-campaign agent update (e.g. a per-trial Trial Report
   * during an alloy-design optimization). Rendered collapsed by default;
   * only the latest one in the conversation expands automatically.
   */
  intermediate?: boolean;
};
