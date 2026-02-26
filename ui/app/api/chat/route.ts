import { NextResponse } from "next/server";
import { fetchWithTimeout } from "@/lib/mcp";
import { discoverMcpTools } from "@/lib/mcp-tools";
import { toPrompt, findSkills } from "@/lib/skills";
import { config } from "@/app/config";

type ChatRequest = {
  message: string;
};

type ChatResponse = {
  ok: boolean;
  response: string;
  tools?: Array<{ name: string; description?: string; inputSchema?: any }>;
  error?: string;
};

const DEFAULT_OPENAI_MODEL = "gpt-4o-mini";
const DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1";
const DEFAULT_OPENAI_TIMEOUT_MS = 30000;

function buildSystemPrompt(): string {
  return [
    "You are an orchestrator assistant for an MCP-enabled tool console.",
    "",
    toPrompt(findSkills([config.skillsDir]))
  ].join("\n");
}

function summarizeTools(tools: Array<{ name: string; description?: string }>): string {
  if (tools.length === 0) {
    return "No MCP tools were discovered.";
  }
  return tools
    .map((tool) => `- ${tool.name}${tool.description ? `: ${tool.description}` : ""}`)
    .join("\n");
}

export async function POST(request: Request) {
  let body: ChatRequest;
  try {
    body = (await request.json()) as ChatRequest;
  } catch {
    return NextResponse.json(
      {
        ok: false,
        response: "",
        error: "Invalid JSON body."
      } satisfies ChatResponse,
      { status: 400 }
    );
  }

  const message = typeof body.message === "string" ? body.message.trim() : "";
  if (!message) {
    return NextResponse.json(
      {
        ok: false,
        response: "",
        error: "Message is required."
      } satisfies ChatResponse,
      { status: 400 }
    );
  }

  const apiKey = process.env.OPENAI_API_KEY;
  if (!apiKey) {
    return NextResponse.json(
      {
        ok: false,
        response: "",
        error: "OPENAI_API_KEY not set"
      } satisfies ChatResponse,
      { status: 200 }
    );
  }

  const model = process.env.OPENAI_MODEL || DEFAULT_OPENAI_MODEL;
  const baseUrl = (process.env.OPENAI_BASE_URL || DEFAULT_OPENAI_BASE_URL).replace(/\/+$/, "");
  const apiStyle = (process.env.OPENAI_API_STYLE || "responses").toLowerCase();
  const chatUrlOverride = process.env.OPENAI_CHAT_URL?.trim();
  const authMode = (process.env.OPENAI_AUTH_MODE || "bearer").toLowerCase();
  const timeoutRaw = Number(process.env.OPENAI_TIMEOUT_MS);
  const openAiTimeoutMs = Number.isFinite(timeoutRaw) && timeoutRaw > 0 ? timeoutRaw : DEFAULT_OPENAI_TIMEOUT_MS;

  let toolsResult;
  try {
    toolsResult = await discoverMcpTools(2000);
  } catch (error) {
    const messageText = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json(
      {
        ok: false,
        response: "",
        error: `Failed to discover MCP tools: ${messageText}`
      } satisfies ChatResponse,
      { status: 502 }
    );
  }

  const tools = toolsResult.tools;
  const toolsSummary = summarizeTools(tools);

  const systemPrompt = buildSystemPrompt();
  const toolContext = `Discovered MCP tools:\n${toolsSummary}\nIf no tools exist, explain that clearly and still provide /api/mcp/call usage format.`;

  try {
    const url = chatUrlOverride
      ? chatUrlOverride
      : apiStyle === "chat_completions"
        ? `${baseUrl}/chat/completions`
        : `${baseUrl}/responses`;
    const payload =
      apiStyle === "chat_completions"
        ? {
            model,
            messages: [
              { role: "system", content: systemPrompt },
              { role: "system", content: toolContext },
              { role: "user", content: message }
            ]
          }
        : {
            model,
            input: [
              {
                role: "system",
                content: [{ type: "input_text", text: systemPrompt }]
              },
              {
                role: "system",
                content: [{ type: "input_text", text: toolContext }]
              },
              {
                role: "user",
                content: [{ type: "input_text", text: message }]
              }
            ]
          };

    const headers: Record<string, string> = {
      "content-type": "application/json"
    };
    if (authMode === "api_key") {
      headers["api-key"] = apiKey;
    } else {
      headers.authorization = `Bearer ${apiKey}`;
    }

    const response = await fetchWithTimeout(
      url,
      {
        method: "POST",
        headers,
        body: JSON.stringify(payload)
      },
      openAiTimeoutMs
    );

    const text = await response.text();
    if (!response.ok) {
      return NextResponse.json(
        {
          ok: false,
          response: "",
          tools,
          error: `OpenAI error (${response.status}): ${text}`
        } satisfies ChatResponse,
        { status: 502 }
      );
    }

    let parsed: unknown = null;
    try {
      parsed = text ? JSON.parse(text) : null;
    } catch {
      parsed = null;
    }

    const parsedObj = parsed as Record<string, unknown> | null;
    let outputText = "";
    if (apiStyle === "chat_completions") {
      const choices = Array.isArray(parsedObj?.choices) ? parsedObj?.choices : [];
      const first = choices[0] as Record<string, unknown> | undefined;
      const messageObj = (first?.message ?? null) as Record<string, unknown> | null;
      outputText = typeof messageObj?.content === "string" ? messageObj.content : "";
    } else {
      outputText = typeof parsedObj?.output_text === "string" ? parsedObj.output_text : "";
    }
    const fallback = "I can list MCP tools and explain how to call /api/mcp/call manually.";

    return NextResponse.json(
      {
        ok: true,
        response: outputText || fallback,
        tools
      } satisfies ChatResponse,
      { status: 200 }
    );
  } catch (error) {
    const isAbort = error instanceof Error && error.name === "AbortError";
    const messageText = error instanceof Error ? error.message : "Unknown error";

    return NextResponse.json(
      {
        ok: false,
        response: "",
        tools,
        error: isAbort ? `OpenAI request timed out after ${openAiTimeoutMs}ms.` : messageText
      } satisfies ChatResponse,
      { status: 502 }
    );
  }
}
