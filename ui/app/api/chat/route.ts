import { NextResponse } from "next/server";
import { callMcpRpc } from "@/lib/mcp";
import { toPrompt, findSkills } from "@/lib/skills";
import { config } from "@/app/config";
import { readFileSync } from "fs";
import { join } from "path";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

type ChatRequest = {
  message: string;
  history?: Array<{ role: string; content: string }>;
};

type ToolCallResult = {
  tool: string;
  args: Record<string, unknown>;
  stdout: string;
  stderr: string;
  ok: boolean;
  plotPath?: string | null;
  displayHtml?: string | null;
};

type ChatResponse = {
  ok: boolean;
  response: string;
  toolCalls?: ToolCallResult[];
  error?: string;
};

/* ------------------------------------------------------------------ */
/*  Anthropic tool definitions                                         */
/* ------------------------------------------------------------------ */

const TOOLS = [
  {
    name: "run_bash",
    description:
      "Run a bash command inside the MCP sandbox. Use this to execute " +
      "the salt analysis script, query the JSON database with Python, " +
      "or perform any computation. The sandbox has Python 3, numpy, and " +
      "matplotlib available. Skills are mounted at /mnt/skills/ and " +
      "output should go to /mnt/data/output/. " +
      "The salt database JSON is at /mnt/skills/salt-analysis/assets/Molten_Salt_Thermophysical_Properties.json. " +
      "The analysis script is at /mnt/skills/salt-analysis/scripts/analyze_salt.py.",
    input_schema: {
      type: "object" as const,
      properties: {
        command: {
          type: "string",
          description: "The bash command to execute",
        },
      },
      required: ["command"],
    },
  },
  {
    name: "display_file",
    description:
      "Display a file (image, plot, text) to the user in the UI. " +
      "Use this after generating a plot to show it. Pass the absolute " +
      "file path as the uri parameter.",
    input_schema: {
      type: "object" as const,
      properties: {
        uri: {
          type: "string",
          description: "Absolute path or URI of the file to display",
        },
      },
      required: ["uri"],
    },
  },
  {
    name: "web_search",
    description:
      "Search the web for information about molten salts, research groups, " +
      "recent studies, or any scientific topic. Use this for questions about " +
      "research trends, groups studying specific salts, or general scientific " +
      "questions that go beyond what is in the database.",
    input_schema: {
      type: "object" as const,
      properties: {
        query: {
          type: "string",
          description: "The search query",
        },
      },
      required: ["query"],
    },
  },
];

/* ------------------------------------------------------------------ */
/*  System prompt                                                      */
/* ------------------------------------------------------------------ */

function buildSystemPrompt(): string {
  const skillsPrompt = toPrompt(findSkills([config.skillsDir]));

  // Load the JSON structure summary for the agent
  const dbPath = join(
    config.skillsDir,
    "salt-analysis",
    "assets",
    "Molten_Salt_Thermophysical_Properties.json"
  );
  let saltListSummary = "";
  try {
    const raw = readFileSync(dbPath, "utf8");
    const data = JSON.parse(raw);
    const mstdb = data?.MSTDBTP || {};
    const evaluatedSalts = Object.keys(mstdb?.evaluated || {});
    saltListSummary = `\nThe database contains ${evaluatedSalts.length} evaluated salts: ${evaluatedSalts.join(", ")}.\n`;
  } catch {
    saltListSummary = "\n(Could not load salt list summary.)\n";
  }

  return `You are VISTA, a scientific assistant specialized in molten salt thermophysical properties analysis.
You help researchers explore a database of molten salt properties, generate phase diagrams, and find relevant research.

${skillsPrompt}

## Database Information
The molten salt database is a JSON file at:
/mnt/skills/salt-analysis/assets/Molten_Salt_Thermophysical_Properties.json

Structure: { "MSTDBTP": { "estimates": { "RK": { ... } }, "evaluated": { "<salt_name>": { "<composition>": { "<property>": { "value": ..., "reference": ..., "DOI": ..., "abs_uncertainty": ... } } } } } }

Properties available may include: melt, boil, density, viscosity, heat_capacity, thermal_conductivity, molecular_weight, etc.

Compositions for multi-component salts are given as dash-separated mole fractions, e.g. "0.055-0.945" for a binary salt.
${saltListSummary}
## How to Handle Queries

### Type 1: Database Statistics & Queries
For counting, finding extremes, or listing data, write a short Python script that loads the JSON and computes the answer.
Examples:
- "How many fluoride salts?" → count salts with "F" in the name
- "Most studied salt?" → find salt with most composition entries
- "Highest melting temperature?" → scan all melt values

Use run_bash with a Python one-liner or short script. Always use: MPLBACKEND=Agg for matplotlib.

### Type 1: Phase Diagrams & Plotting
To generate a phase diagram plot:
1. Use run_bash to execute: MPLBACKEND=Agg python3 /mnt/skills/salt-analysis/scripts/analyze_salt.py --salt <SALT_NAME> --output-dir /mnt/data/output/salt-plots
2. The script will print "Plot saved to <path>" and list references.
3. Then use display_file with the plot path to show it to the user.

For custom plots (zoom, different properties, custom ranges):
- Write a Python script that loads the JSON, extracts the relevant data, and plots with matplotlib.
- Save to /mnt/data/output/salt-plots/<name>.png
- Then use display_file to show it.

When the user asks to "zoom in" on a specific mole fraction range, write a custom Python script that filters compositions and adjusts xlim.
When the user asks about a different property (e.g., "what about thermal conductivity?"), plot that property column instead of melt.

### Type 1: References
When asked "where does the data come from?" or about references, extract reference and DOI fields from the salt data and list them.

### Type 2: Research Trend Questions
For questions like "has any group studied X?", "what is the most promising salt for Y?", or "how to improve Z?":
1. First check the database for relevant data using run_bash
2. Then use web_search to find recent research and groups
3. Synthesize both sources in your answer

## Important Rules
- Always run actual code to get precise answers — do not guess counts or values.
- When generating plots, ALWAYS use display_file afterward to show the plot in the UI.
- Include references/DOIs when available.
- Be concise but thorough in explanations.
- For plot commands, always set MPLBACKEND=Agg before running matplotlib scripts.`;
}

/* ------------------------------------------------------------------ */
/*  Execute a tool call via MCP                                        */
/* ------------------------------------------------------------------ */

async function executeTool(
  toolName: string,
  toolInput: Record<string, unknown>
): Promise<ToolCallResult> {
  const result: ToolCallResult = {
    tool: toolName,
    args: toolInput,
    stdout: "",
    stderr: "",
    ok: false,
    plotPath: null,
    displayHtml: null,
  };

  try {
    if (toolName === "run_bash") {
      const { response, json } = await callMcpRpc(
        "tools/call",
        { name: "bash", arguments: { command: toolInput.command } },
        60000
      );

      const content = extractMcpContent(json);
      result.stdout = content.text;
      result.ok = response.ok;

      // Extract plot path if present
      const plotMatch = result.stdout.match(/Plot saved to\s+(.+)/);
      if (plotMatch) {
        result.plotPath = plotMatch[1].trim();
      }
    } else if (toolName === "display_file") {
      const { response, json } = await callMcpRpc(
        "tools/call",
        { name: "display_file", arguments: { uri: toolInput.uri } },
        20000
      );

      const content = extractMcpContent(json);
      result.stdout = content.text;
      result.ok = response.ok;
      result.displayHtml = content.html || null;
    } else if (toolName === "web_search") {
      // Web search — handled in the Anthropic agent loop via executeWebSearch.
      // For the OpenAI fallback, we provide a message indicating search was requested.
      const query = String(toolInput.query || "");
      result.stdout =
        `Web search was requested for: "${query}". ` +
        `Note: Web search is best supported with the Anthropic API backend (set ANTHROPIC_API_KEY). ` +
        `Please answer based on your existing knowledge about this topic.`;
      result.ok = true;
    }
  } catch (err) {
    result.stderr =
      err instanceof Error ? err.message : "Unknown tool execution error";
  }

  return result;
}

function extractMcpContent(json: unknown): { text: string; html: string | null } {
  let text = "";
  let html: string | null = null;

  if (!json || typeof json !== "object") return { text: String(json || ""), html };

  const obj = json as Record<string, unknown>;
  const resultObj = (obj.result ?? obj) as Record<string, unknown>;

  // Extract from content array
  const content = Array.isArray(resultObj?.content) ? resultObj.content : [];
  for (const item of content) {
    if (!item || typeof item !== "object") continue;
    const ci = item as Record<string, unknown>;
    if (ci.type === "text" && typeof ci.text === "string") {
      text += (text ? "\n" : "") + ci.text;
    }
    if (ci.type === "resource") {
      const resource = ci.resource as Record<string, unknown> | undefined;
      const mimeType = typeof resource?.mimeType === "string" ? resource.mimeType : "";
      if (mimeType.includes("text/html") && typeof resource?.text === "string") {
        html = resource.text;
      }
    }
  }

  // Fallback to stdout
  if (!text && typeof resultObj?.stdout === "string") {
    text = resultObj.stdout;
  }

  return { text, html };
}

/* ------------------------------------------------------------------ */
/*  Anthropic API agentic loop                                         */
/* ------------------------------------------------------------------ */

async function runAgentLoop(
  userMessage: string,
  history: Array<{ role: string; content: string }>
): Promise<{ response: string; toolCalls: ToolCallResult[] }> {
  const apiKey = process.env.ANTHROPIC_API_KEY;
  if (!apiKey) {
    // Fallback: try OPENAI_API_KEY with OpenAI-compatible endpoint
    return runFallbackAgent(userMessage);
  }

  const systemPrompt = buildSystemPrompt();
  const toolCalls: ToolCallResult[] = [];

  // Build messages from history
  const messages: Array<Record<string, unknown>> = [];

  // Add conversation history (last 10 turns)
  const recentHistory = history.slice(-10);
  for (const msg of recentHistory) {
    if (msg.role === "user" || msg.role === "assistant") {
      messages.push({ role: msg.role, content: msg.content });
    }
  }

  // Add current user message
  messages.push({ role: "user", content: userMessage });

  // Agentic loop — up to 8 tool-use rounds
  const maxIterations = 8;
  for (let i = 0; i < maxIterations; i++) {
    const anthropicTools = TOOLS.map((t) => ({
      name: t.name,
      description: t.description,
      input_schema: t.input_schema,
    }));

    const payload: Record<string, unknown> = {
      model: process.env.ANTHROPIC_MODEL || "claude-sonnet-4-20250514",
      max_tokens: 4096,
      system: systemPrompt,
      tools: anthropicTools,
      messages,
    };

    const resp = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-api-key": apiKey,
        "anthropic-version": "2023-06-01",
      },
      body: JSON.stringify(payload),
    });

    if (!resp.ok) {
      const errText = await resp.text();
      throw new Error(`Anthropic API error (${resp.status}): ${errText}`);
    }

    const data = await resp.json();
    const contentBlocks = data.content || [];
    const stopReason = data.stop_reason;

    // Collect assistant response blocks
    const assistantContent: Array<Record<string, unknown>> = [];
    let textResponse = "";

    for (const block of contentBlocks) {
      if (block.type === "text") {
        textResponse += block.text;
        assistantContent.push(block);
      } else if (block.type === "tool_use") {
        assistantContent.push(block);
      }
    }

    // Add the assistant message to the conversation
    messages.push({ role: "assistant", content: assistantContent });

    // If stop_reason is "end_turn" or no tool use, we're done
    if (stopReason === "end_turn" || stopReason !== "tool_use") {
      return { response: textResponse, toolCalls };
    }

    // Process tool calls
    const toolResultBlocks: Array<Record<string, unknown>> = [];

    for (const block of contentBlocks) {
      if (block.type !== "tool_use") continue;

      const toolName = block.name;
      const toolInput = block.input || {};
      const toolUseId = block.id;

      // Handle web_search differently — use Anthropic's server-side search
      if (toolName === "web_search") {
        const searchResult = await executeWebSearch(
          String(toolInput.query || ""),
          apiKey
        );
        const tcResult: ToolCallResult = {
          tool: "web_search",
          args: toolInput,
          stdout: searchResult,
          stderr: "",
          ok: true,
        };
        toolCalls.push(tcResult);
        toolResultBlocks.push({
          type: "tool_result",
          tool_use_id: toolUseId,
          content: searchResult,
        });
      } else {
        // Execute MCP tools
        const tcResult = await executeTool(toolName, toolInput);
        toolCalls.push(tcResult);

        let resultContent = tcResult.stdout || tcResult.stderr || "(no output)";
        if (tcResult.plotPath) {
          resultContent += `\n\nPlot saved to: ${tcResult.plotPath}`;
        }

        toolResultBlocks.push({
          type: "tool_result",
          tool_use_id: toolUseId,
          content: resultContent,
        });
      }
    }

    // Add tool results to conversation
    messages.push({ role: "user", content: toolResultBlocks });
  }

  return { response: "Agent reached maximum iterations.", toolCalls };
}

/* ------------------------------------------------------------------ */
/*  Web search helper                                                  */
/* ------------------------------------------------------------------ */

async function executeWebSearch(query: string, apiKey: string): Promise<string> {
  try {
    const resp = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-api-key": apiKey,
        "anthropic-version": "2023-06-01",
      },
      body: JSON.stringify({
        model: process.env.ANTHROPIC_MODEL || "claude-sonnet-4-20250514",
        max_tokens: 2048,
        tools: [{ type: "web_search_20250305", name: "web_search" }],
        messages: [
          {
            role: "user",
            content: `Search the web for: ${query}\n\nProvide a concise summary of the most relevant findings, including any research groups, recent papers, and key developments.`,
          },
        ],
      }),
    });

    if (!resp.ok) {
      return `Web search failed: HTTP ${resp.status}`;
    }

    const data = await resp.json();
    const textBlocks = (data.content || [])
      .filter((b: Record<string, unknown>) => b.type === "text")
      .map((b: Record<string, unknown>) => b.text)
      .join("\n");

    return textBlocks || "No search results found.";
  } catch (err) {
    return `Web search error: ${err instanceof Error ? err.message : "Unknown error"}`;
  }
}

/* ------------------------------------------------------------------ */
/*  Fallback agent (OpenAI-compatible, with tool-use loop)             */
/* ------------------------------------------------------------------ */

function buildOpenAiUrl(): string {
  const chatUrlOverride = process.env.OPENAI_CHAT_URL?.trim().replace(/\/+$/, "");
  const model = process.env.OPENAI_MODEL || "gpt-4o-mini";
  const baseUrl = (
    process.env.OPENAI_BASE_URL || "https://api.openai.com/v1"
  ).replace(/\/+$/, "");
  const apiStyle = (process.env.OPENAI_API_STYLE || "chat_completions").toLowerCase();
  const authMode = (process.env.OPENAI_AUTH_MODE || "bearer").toLowerCase();

  // If OPENAI_CHAT_URL is set, figure out if it's a full URL or just a host
  if (chatUrlOverride) {
    // Azure-style: if the URL is just a host (no /openai/ path), build the full deployment URL
    if (authMode === "api_key" && !chatUrlOverride.includes("/openai/")) {
      const apiVersion = process.env.AZURE_API_VERSION || "2025-01-01-preview";
      return `${chatUrlOverride}/openai/deployments/${model}/chat/completions?api-version=${apiVersion}`;
    }
    // If it already has a path (like /v1/chat/completions), use as-is
    if (chatUrlOverride.includes("/chat/completions") || chatUrlOverride.includes("/responses")) {
      return chatUrlOverride;
    }
    // Otherwise append the standard chat completions path
    return `${chatUrlOverride}/chat/completions`;
  }

  // No override — build from base URL
  if (apiStyle === "chat_completions") {
    return `${baseUrl}/chat/completions`;
  }
  return `${baseUrl}/responses`;
}

async function runFallbackAgent(
  message: string
): Promise<{ response: string; toolCalls: ToolCallResult[] }> {
  // Strip quotes and whitespace that .env parsers may leave
  const rawKey = process.env.OPENAI_API_KEY || "";
  const apiKey = rawKey.replace(/^["'\s]+|["'\s]+$/g, "");

  // --- Diagnostic: dump ALL OPENAI_* env vars to find conflicts ---
  const envDiag: Record<string, string> = {};
  for (const [k, v] of Object.entries(process.env)) {
    if (k.startsWith("OPENAI_") || k.startsWith("AZURE_") || k === "MCP_BASE_URL") {
      envDiag[k] = k.includes("KEY")
        ? `len=${(v || "").length} first8=${(v || "").slice(0, 8)} last4=${(v || "").slice(-4)}`
        : (v || "").slice(0, 120);
    }
  }
  console.log("[VISTA Agent] ENV DIAGNOSTIC:", JSON.stringify(envDiag, null, 2));
  // --- End diagnostic ---

  if (!apiKey) {
    return {
      response:
        "No API key configured. Please set ANTHROPIC_API_KEY (preferred) or OPENAI_API_KEY in your .env file.",
      toolCalls: [],
    };
  }

  const model = process.env.OPENAI_MODEL || "gpt-4o-mini";
  const authMode = (process.env.OPENAI_AUTH_MODE || "bearer").toLowerCase();
  const apiStyle = (process.env.OPENAI_API_STYLE || "chat_completions").toLowerCase();

  const systemPrompt = buildSystemPrompt();
  const url = buildOpenAiUrl();

  console.log("[VISTA Agent] Using URL:", url);
  console.log("[VISTA Agent] Model:", model, "Auth:", authMode, "Style:", apiStyle);
  console.log("[VISTA Agent] API key length:", apiKey.length, "first 8 chars:", apiKey.slice(0, 8), "last 4 chars:", apiKey.slice(-4));
  if (rawKey !== apiKey) {
    console.log("[VISTA Agent] WARNING: API key had surrounding quotes/whitespace that were stripped");
  }

  const headers: Record<string, string> = { "content-type": "application/json" };
  if (authMode === "api_key") {
    headers["api-key"] = apiKey;
  } else {
    headers.authorization = `Bearer ${apiKey}`;
  }

  // OpenAI tool definitions
  const openaiTools = TOOLS.map((t) => ({
    type: "function" as const,
    function: {
      name: t.name,
      description: t.description,
      parameters: t.input_schema,
    },
  }));

  const messages: Array<Record<string, unknown>> = [
    { role: "system", content: systemPrompt },
    { role: "user", content: message },
  ];

  const toolCalls: ToolCallResult[] = [];

  // Agentic loop for OpenAI / Azure OpenAI
  for (let i = 0; i < 8; i++) {
    const payload: Record<string, unknown> = {
      model,
      messages,
      tools: openaiTools,
      tool_choice: "auto",
    };

    let resp: Response;
    try {
      resp = await fetch(url, {
        method: "POST",
        headers,
        body: JSON.stringify(payload),
      });
    } catch (fetchErr) {
      throw new Error(
        `Failed to connect to LLM endpoint (${url}): ${fetchErr instanceof Error ? fetchErr.message : "Unknown error"}`
      );
    }

    const responseText = await resp.text();

    if (!resp.ok) {
      const diagnostic = resp.status === 401
        ? ` | URL: ${url} | Auth header: api-key (len=${apiKey.length}, first8=${apiKey.slice(0, 8)})`
        : "";
      throw new Error(
        `OpenAI/Azure API error (${resp.status}): ${responseText.slice(0, 500)}${diagnostic}`
      );
    }

    // Safely parse JSON
    let data: Record<string, unknown>;
    try {
      data = JSON.parse(responseText);
    } catch {
      throw new Error(
        `LLM returned non-JSON response (${resp.status}, content-type: ${resp.headers.get("content-type") || "unknown"}). ` +
        `First 200 chars: ${responseText.slice(0, 200)}`
      );
    }

    // Handle OpenAI Responses API format vs Chat Completions
    if (apiStyle !== "chat_completions" && typeof data.output_text === "string") {
      return { response: data.output_text, toolCalls };
    }

    const choice = (data.choices as Array<Record<string, unknown>> | undefined)?.[0];
    const assistantMsg = choice?.message as Record<string, unknown> | undefined;

    if (!assistantMsg) {
      return {
        response: typeof data.output_text === "string"
          ? data.output_text
          : "No response from LLM.",
        toolCalls,
      };
    }

    messages.push(assistantMsg);

    const finishReason = choice?.finish_reason;
    const msgToolCalls = assistantMsg.tool_calls as Array<Record<string, unknown>> | undefined;

    if (finishReason !== "tool_calls" || !msgToolCalls?.length) {
      return {
        response: typeof assistantMsg.content === "string" ? assistantMsg.content : "",
        toolCalls,
      };
    }

    // Process tool calls
    for (const tc of msgToolCalls) {
      const fnObj = tc.function as Record<string, unknown> | undefined;
      const toolName = typeof fnObj?.name === "string" ? fnObj.name : "";
      let toolInput: Record<string, unknown> = {};
      try {
        const rawArgs = typeof fnObj?.arguments === "string" ? fnObj.arguments : "{}";
        toolInput = JSON.parse(rawArgs);
      } catch {
        toolInput = {};
      }

      const tcResult = await executeTool(toolName, toolInput);
      toolCalls.push(tcResult);

      let resultContent = tcResult.stdout || tcResult.stderr || "(no output)";
      if (tcResult.plotPath) {
        resultContent += `\n\nPlot saved to: ${tcResult.plotPath}`;
      }

      messages.push({
        role: "tool",
        tool_call_id: tc.id,
        content: resultContent,
      });
    }
  }

  return { response: "Agent reached maximum iterations.", toolCalls };
}

/* ------------------------------------------------------------------ */
/*  POST handler                                                       */
/* ------------------------------------------------------------------ */

export async function POST(request: Request) {
  let body: ChatRequest;
  try {
    body = (await request.json()) as ChatRequest;
  } catch {
    return NextResponse.json(
      { ok: false, response: "", error: "Invalid JSON body." } satisfies ChatResponse,
      { status: 400 }
    );
  }

  const message = typeof body.message === "string" ? body.message.trim() : "";
  if (!message) {
    return NextResponse.json(
      { ok: false, response: "", error: "Message is required." } satisfies ChatResponse,
      { status: 400 }
    );
  }

  const history = Array.isArray(body.history) ? body.history : [];

  try {
    const { response, toolCalls } = await runAgentLoop(message, history);
    return NextResponse.json(
      {
        ok: true,
        response,
        toolCalls,
      } satisfies ChatResponse,
      { status: 200 }
    );
  } catch (error) {
    const errMsg = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json(
      { ok: false, response: "", error: errMsg } satisfies ChatResponse,
      { status: 502 }
    );
  }
}
