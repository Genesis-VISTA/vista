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
};
