import { NextResponse } from "next/server";
import fs from "node:fs/promises";
import path from "node:path";
import matter from "gray-matter";
import { callMcpRpc, fetchWithTimeout } from "@/lib/mcp";
import { discoverMcpTools } from "@/lib/mcp-tools";

type ChatRequest = {
  message: string;
};

type ToolExecution = {
  tool: string;
  args: Record<string, unknown>;
  ok: boolean;
  output: string;
  error?: string;
};

type ChatResponse = {
  ok: boolean;
  response: string;
  tools?: Array<{ name: string; description?: string; inputSchema?: any }>;
  executions?: ToolExecution[];
  error?: string;
};

type PlannerResult = {
  assistant_plan?: string;
  tool_calls?: Array<{ tool?: string; args?: Record<string, unknown>; reason?: string }>;
};

type OpenAiConfig = {
  apiKey: string;
  model: string;
  baseUrl: string;
  apiStyle: string;
  chatUrlOverride: string;
  authMode: string;
  timeoutMs: number;
};

const DEFAULT_OPENAI_MODEL = "gpt-4o-mini";
const DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1";
const DEFAULT_OPENAI_TIMEOUT_MS = 30000;
const MAX_TOOL_CALLS = 3;

async function listSkillFiles(rootDir: string): Promise<string[]> {
  const results: string[] = [];

  async function walk(dir: string) {
    const entries = await fs.readdir(dir, { withFileTypes: true });
    for (const entry of entries) {
      const fullPath = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        await walk(fullPath);
      } else if (entry.isFile() && entry.name === "SKILL.md") {
        results.push(fullPath);
      }
    }
  }

  await walk(rootDir);
  return results;
}

async function loadSkillsContext(): Promise<string> {
  const repoRoot = path.resolve(process.cwd(), "..");
  const skillsRoot = path.join(repoRoot, "skills");

  try {
    await fs.access(skillsRoot);
  } catch {
    return "No local skills directory found.";
  }

  const files = await listSkillFiles(skillsRoot);
  if (files.length === 0) return "No skills found.";

  const summaries = await Promise.all(
    files.map(async (filePath) => {
      const raw = await fs.readFile(filePath, "utf8");
      const parsed = matter(raw);
      const slug = path.basename(path.dirname(filePath));
      const name = typeof parsed.data.name === "string" ? parsed.data.name : slug;
      const description = typeof parsed.data.description === "string" ? parsed.data.description : "";
      const body = parsed.content.replace(/\s+/g, " ").trim().slice(0, 700);
      return `- ${name} (${slug})\n  description: ${description || "n/a"}\n  spec: ${body || "n/a"}`;
    })
  );

  return summaries.join("\n");
}

function parseJsonObject(text: string): Record<string, unknown> | null {
  try {
    return JSON.parse(text) as Record<string, unknown>;
  } catch {
    const match = text.match(/\{[\s\S]*\}/);
    if (!match) return null;
    try {
      return JSON.parse(match[0]) as Record<string, unknown>;
    } catch {
      return null;
    }
  }
}

function summarizeTools(tools: Array<{ name: string; description?: string }>): string {
  if (tools.length === 0) return "No MCP tools were discovered.";
  return tools.map((tool) => `- ${tool.name}${tool.description ? `: ${tool.description}` : ""}`).join("\n");
}

async function callOpenAi(config: OpenAiConfig, systemPrompt: string, userPrompt: string): Promise<string> {
  const url = config.chatUrlOverride
    ? config.chatUrlOverride
    : config.apiStyle === "chat_completions"
      ? `${config.baseUrl}/chat/completions`
      : `${config.baseUrl}/responses`;

  const payload =
    config.apiStyle === "chat_completions"
      ? {
          model: config.model,
          messages: [
            { role: "system", content: systemPrompt },
            { role: "user", content: userPrompt }
          ]
        }
      : {
          model: config.model,
          input: [
            {
              role: "system",
              content: [{ type: "input_text", text: systemPrompt }]
            },
            {
              role: "user",
              content: [{ type: "input_text", text: userPrompt }]
            }
          ]
        };

  const headers: Record<string, string> = {
    "content-type": "application/json"
  };
  if (config.authMode === "api_key") {
    headers["api-key"] = config.apiKey;
  } else {
    headers.authorization = `Bearer ${config.apiKey}`;
  }

  const response = await fetchWithTimeout(
    url,
    {
      method: "POST",
      headers,
      body: JSON.stringify(payload)
    },
    config.timeoutMs
  );

  const text = await response.text();
  if (!response.ok) {
    throw new Error(`OpenAI error (${response.status}): ${text}`);
  }

  const parsed = text ? (JSON.parse(text) as Record<string, unknown>) : null;
  if (!parsed) return "";

  if (config.apiStyle === "chat_completions") {
    const choices = Array.isArray(parsed.choices) ? parsed.choices : [];
    const first = choices[0] as Record<string, unknown> | undefined;
    const messageObj = (first?.message ?? null) as Record<string, unknown> | null;
    return typeof messageObj?.content === "string" ? messageObj.content : "";
  }

  return typeof parsed.output_text === "string" ? parsed.output_text : "";
}

function extractTextFromMcpJson(raw: unknown): string {
  if (!raw || typeof raw !== "object") return typeof raw === "string" ? raw : JSON.stringify(raw);
  const obj = raw as Record<string, unknown>;
  const result = (obj.result ?? obj) as Record<string, unknown>;

  if (typeof result.stdout === "string") return result.stdout;
  if (typeof result.stderr === "string" && result.stderr) return result.stderr;

  const content = Array.isArray(result.content) ? result.content : [];
  const textParts: string[] = [];
  for (const item of content) {
    if (!item || typeof item !== "object") continue;
    const part = item as Record<string, unknown>;
    if (typeof part.text === "string") textParts.push(part.text);
  }

  if (textParts.length > 0) return textParts.join("\n");
  return JSON.stringify(result);
}

function cleanArgs(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  return value as Record<string, unknown>;
}

export async function POST(request: Request) {
  let body: ChatRequest;
  try {
    body = (await request.json()) as ChatRequest;
  } catch {
    return NextResponse.json({ ok: false, response: "", error: "Invalid JSON body." } satisfies ChatResponse, { status: 400 });
  }

  const message = typeof body.message === "string" ? body.message.trim() : "";
  if (!message) {
    return NextResponse.json({ ok: false, response: "", error: "Message is required." } satisfies ChatResponse, { status: 400 });
  }

  const apiKey = process.env.OPENAI_API_KEY;
  if (!apiKey) {
    return NextResponse.json({ ok: false, response: "", error: "OPENAI_API_KEY not set" } satisfies ChatResponse, { status: 200 });
  }

  const config: OpenAiConfig = {
    apiKey,
    model: process.env.OPENAI_MODEL || DEFAULT_OPENAI_MODEL,
    baseUrl: (process.env.OPENAI_BASE_URL || DEFAULT_OPENAI_BASE_URL).replace(/\/+$/, ""),
    apiStyle: (process.env.OPENAI_API_STYLE || "responses").toLowerCase(),
    chatUrlOverride: process.env.OPENAI_CHAT_URL?.trim() || "",
    authMode: (process.env.OPENAI_AUTH_MODE || "bearer").toLowerCase(),
    timeoutMs: (() => {
      const timeoutRaw = Number(process.env.OPENAI_TIMEOUT_MS);
      return Number.isFinite(timeoutRaw) && timeoutRaw > 0 ? timeoutRaw : DEFAULT_OPENAI_TIMEOUT_MS;
    })()
  };

  let toolsResult;
  try {
    toolsResult = await discoverMcpTools(4000);
  } catch (error) {
    const messageText = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json({ ok: false, response: "", error: `Failed to discover MCP tools: ${messageText}` } satisfies ChatResponse, { status: 502 });
  }

  const tools = toolsResult.tools;
  const skillsContext = await loadSkillsContext();
  const toolsSummary = summarizeTools(tools);

  const plannerSystemPrompt = [
    "You are an execution planner for an MCP tool console.",
    "Decide whether tools should be called to satisfy the user request.",
    "Use local skills specs and discovered MCP tools as constraints.",
    "Return STRICT JSON only with shape:",
    '{"assistant_plan":"string","tool_calls":[{"tool":"string","args":{},"reason":"string"}]}',
    `Limit tool_calls to at most ${MAX_TOOL_CALLS}.`,
    "Only use tool names from discovered MCP tools.",
    "If no tool is needed, return an empty tool_calls array."
  ].join(" ");

  const plannerUserPrompt = [
    `User request:\n${message}`,
    `\nDiscovered MCP tools:\n${toolsSummary}`,
    `\nLocal skills:\n${skillsContext}`
  ].join("\n");

  let plannerText = "";
  try {
    plannerText = await callOpenAi(config, plannerSystemPrompt, plannerUserPrompt);
  } catch (error) {
    const messageText = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json(
      {
        ok: false,
        response: "",
        tools,
        error: messageText
      } satisfies ChatResponse,
      { status: 502 }
    );
  }

  const parsedPlan = parseJsonObject(plannerText) as PlannerResult | null;
  const rawToolCalls = Array.isArray(parsedPlan?.tool_calls) ? parsedPlan.tool_calls : [];
  const allowedToolNames = new Set(tools.map((tool) => tool.name));

  const plannedCalls = rawToolCalls
    .map((call) => ({
      tool: typeof call.tool === "string" ? call.tool : "",
      args: cleanArgs(call.args),
      reason: typeof call.reason === "string" ? call.reason : ""
    }))
    .filter((call) => call.tool && allowedToolNames.has(call.tool))
    .slice(0, MAX_TOOL_CALLS);

  const executions: ToolExecution[] = [];
  for (const call of plannedCalls) {
    try {
      const { response, text, json } = await callMcpRpc(
        "tools/call",
        {
          name: call.tool,
          arguments: call.args
        },
        30000
      );

      if (!response.ok) {
        executions.push({
          tool: call.tool,
          args: call.args,
          ok: false,
          output: "",
          error: `MCP error (${response.status}): ${text}`
        });
        continue;
      }

      executions.push({
        tool: call.tool,
        args: call.args,
        ok: true,
        output: extractTextFromMcpJson(json)
      });
    } catch (error) {
      executions.push({
        tool: call.tool,
        args: call.args,
        ok: false,
        output: "",
        error: error instanceof Error ? error.message : "Unknown tool execution error"
      });
    }
  }

  const executionSummary =
    executions.length === 0
      ? "No tools executed."
      : executions
          .map((run, index) => {
            const head = `#${index + 1} tool=${run.tool} ok=${run.ok}`;
            const argsText = `args=${JSON.stringify(run.args)}`;
            const bodyText = run.ok ? run.output : `error=${run.error || "unknown"}`;
            return `${head}\n${argsText}\n${bodyText}`;
          })
          .join("\n\n");

  const responderSystemPrompt = [
    "You are an execution assistant.",
    "Answer using tool results when available.",
    "If a tool failed, explain clearly and provide a concrete next step.",
    "Do not invent tool outputs."
  ].join(" ");

  const responderUserPrompt = [
    `User request:\n${message}`,
    `\nPlanner notes:\n${parsedPlan?.assistant_plan || ""}`,
    `\nTool execution results:\n${executionSummary}`
  ].join("\n");

  try {
    const finalText = await callOpenAi(config, responderSystemPrompt, responderUserPrompt);
    return NextResponse.json(
      {
        ok: true,
        response: finalText || "Completed. No additional response text generated.",
        tools,
        executions
      } satisfies ChatResponse,
      { status: 200 }
    );
  } catch (error) {
    const messageText = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json(
      {
        ok: false,
        response: executionSummary,
        tools,
        executions,
        error: messageText
      } satisfies ChatResponse,
      { status: 502 }
    );
  }
}
