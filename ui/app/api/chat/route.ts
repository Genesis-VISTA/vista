import { NextResponse } from "next/server";
import { callMcpRpc } from "@/lib/mcp";
import { toPrompt, findSkills } from "@/lib/skills";
import { config } from "@/app/config";
import { readFileSync, existsSync } from "fs";
import { join, extname, resolve } from "path";

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
/*  Logging helper                                                     */
/* ------------------------------------------------------------------ */

function log(level: "INFO" | "WARN" | "ERROR", area: string, message: string, extra?: Record<string, unknown>) {
  const ts = new Date().toISOString();
  const prefix = `[VISTA ${level}] [${ts}] [${area}]`;
  const suffix = extra ? " " + JSON.stringify(extra) : "";
  if (level === "ERROR") {
    console.error(`${prefix} ${message}${suffix}`);
  } else if (level === "WARN") {
    console.warn(`${prefix} ${message}${suffix}`);
  } else {
    console.log(`${prefix} ${message}${suffix}`);
  }
}

/* ------------------------------------------------------------------ */
/*  Image / file helpers                                               */
/* ------------------------------------------------------------------ */

function sandboxPathToHost(sandboxPath: string): string | null {
  const projectRoot = resolve(config.skillsDir, "..");
  const mappings: Array<[string, string]> = [
    ["/mnt/data/output", join(projectRoot, "data", "output")],
    ["/mnt/data/uploads", join(projectRoot, "data", "uploads")],
    ["/mnt/skills", join(projectRoot, "skills")],
  ];
  for (const [sandboxPrefix, hostPrefix] of mappings) {
    if (sandboxPath.startsWith(sandboxPrefix)) {
      return hostPrefix + sandboxPath.slice(sandboxPrefix.length);
    }
  }
  return null;
}

function getMimeType(filePath: string): string {
  const ext = extname(filePath).toLowerCase();
  if (ext === ".png") return "image/png";
  if (ext === ".jpg" || ext === ".jpeg") return "image/jpeg";
  if (ext === ".gif") return "image/gif";
  if (ext === ".webp") return "image/webp";
  if (ext === ".svg") return "image/svg+xml";
  return "application/octet-stream";
}

function readImageAsBase64Html(sandboxPath: string): string | null {
  const hostPath = sandboxPathToHost(sandboxPath);
  if (!hostPath) return null;
  try {
    if (!existsSync(hostPath)) return null;
    const mimeType = getMimeType(hostPath);
    if (!mimeType.startsWith("image/")) return null;
    const bytes = readFileSync(hostPath);
    const base64 = bytes.toString("base64");
    return `<img src="data:${mimeType};base64,${base64}" alt="Plot output" style="max-width:100%;height:auto;display:block;margin:0 auto;" />`;
  } catch {
    return null;
  }
}

/* ------------------------------------------------------------------ */
/*  Azure OpenAI configuration                                         */
/* ------------------------------------------------------------------ */

function getAzureConfig(): {
  url: string;
  headers: Record<string, string>;
  model: string;
} {
  const azureEndpoint = (process.env.AZURE_OPENAI_ENDPOINT || "").replace(/\/+$/, "");
  const azureKey = process.env.AZURE_OPENAI_API_KEY || "";
  const azureDeployment = process.env.AZURE_OPENAI_DEPLOYMENT_NAME || "";
  const azureApiVersion = process.env.AZURE_OPENAI_API_VERSION || "2025-01-01-preview";

  if (azureEndpoint && azureKey && azureDeployment) {
    return {
      url: `${azureEndpoint}/openai/deployments/${azureDeployment}/chat/completions?api-version=${azureApiVersion}`,
      headers: { "content-type": "application/json", "api-key": azureKey },
      model: azureDeployment,
    };
  }

  const chatUrlOverride = (process.env.OPENAI_CHAT_URL || "").trim().replace(/\/+$/, "");
  const baseUrl = (process.env.OPENAI_BASE_URL || "https://api.openai.com/v1").replace(/\/+$/, "");
  const apiKey = (process.env.OPENAI_API_KEY || "").replace(/^["'\s]+|["'\s]+$/g, "");
  const model = process.env.OPENAI_MODEL || "gpt-4o-mini";
  const authMode = (process.env.OPENAI_AUTH_MODE || "bearer").toLowerCase();

  let url: string;
  if (chatUrlOverride) {
    if (authMode === "api_key" && !chatUrlOverride.includes("/openai/") && !chatUrlOverride.includes("/chat/completions")) {
      url = `${chatUrlOverride}/openai/deployments/${model}/chat/completions?api-version=${azureApiVersion}`;
    } else if (chatUrlOverride.includes("/chat/completions")) {
      url = chatUrlOverride;
    } else {
      url = `${chatUrlOverride}/chat/completions`;
    }
  } else {
    url = `${baseUrl}/chat/completions`;
  }

  const headers: Record<string, string> = { "content-type": "application/json" };
  if (authMode === "api_key") {
    headers["api-key"] = apiKey;
  } else {
    headers["authorization"] = `Bearer ${apiKey}`;
  }

  return { url, headers, model };
}

/* ------------------------------------------------------------------ */
/*  Tool definitions (OpenAI function-calling format)                  */
/* ------------------------------------------------------------------ */

const TOOLS = [
  {
    type: "function" as const,
    function: {
      name: "run_bash",
      description:
        "Run a bash command inside the MCP sandbox. " +
        "ALWAYS use this tool — never answer data questions from memory. " +
        "The sandbox has Python 3, numpy, and matplotlib. " +
        "Skills are at /mnt/skills/, output goes to /mnt/data/output/. " +
        "The salt database JSON is at /mnt/skills/salt-analysis/assets/Molten_Salt_Thermophysical_Properties.json. " +
        "The analysis script is at /mnt/skills/salt-analysis/scripts/analyze_salt.py. " +
        "When a plot is saved, it is automatically displayed to the user.",
      parameters: {
        type: "object" as const,
        properties: {
          command: { type: "string", description: "The bash command to execute" },
        },
        required: ["command"],
      },
    },
  },
  {
    type: "function" as const,
    function: {
      name: "web_search",
      description:
        "Signal intent to search the web for research trends, groups, or scientific topics " +
        "beyond the database. No live internet — answer using your own knowledge after calling this.",
      parameters: {
        type: "object" as const,
        properties: {
          query: { type: "string", description: "The search query" },
        },
        required: ["query"],
      },
    },
  },
];

/* ------------------------------------------------------------------ */
/*  System prompt                                                      */
/* ------------------------------------------------------------------ */

function buildSystemPrompt(): string {
  const skillsPrompt = toPrompt(findSkills([config.skillsDir]));

  const dbPath = join(config.skillsDir, "salt-analysis", "assets", "Molten_Salt_Thermophysical_Properties.json");
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

  return [
    `You are VISTA, a scientific assistant for molten salt thermophysical properties.`,
    `You have access to a molten salt database and analysis scripts via the run_bash tool.`,
    ``,
    skillsPrompt,
    ``,
    `## CRITICAL RULES — read these first`,
    `1. You MUST call run_bash for ANY question about the database. NEVER answer data questions from memory or guess values/counts.`,
    `2. For ANY request involving a specific salt (phase diagram, plot, properties, statistics, references), ALWAYS use the analyze_salt.py skill script FIRST:`,
    `     MPLBACKEND=Agg python3 /mnt/skills/salt-analysis/scripts/analyze_salt.py --salt <SALT_NAME> --output-dir /mnt/data/output/salt-plots`,
    `3. For database-wide queries (counting salts, finding extremes across all salts, listing all references), write a short Python script via run_bash.`,
    `4. Plots are AUTOMATICALLY displayed when stdout contains "Plot saved to ...". Do NOT call any display tool.`,
    `5. ALWAYS set MPLBACKEND=Agg before running any matplotlib code.`,
    ``,
    `## analyze_salt.py — your primary skill script`,
    `Location: /mnt/skills/salt-analysis/scripts/analyze_salt.py`,
    `Usage:  MPLBACKEND=Agg python3 /mnt/skills/salt-analysis/scripts/analyze_salt.py --salt <SALT_NAME> --output-dir /mnt/data/output/salt-plots`,
    ``,
    `What it does:`,
    `- Generates a phase diagram (binary → line plot, ternary → heatmap, quaternary → facet plots)`,
    `- Prints statistics: total measurements, compositions, property ranges`,
    `- Prints all references with DOIs`,
    `- Saves plot to /mnt/data/output/salt-plots/<SALT_NAME>.png`,
    ``,
    `Use this script whenever the user asks to:`,
    `- Show/plot/visualize a salt or its phase diagram`,
    `- Get properties or statistics for a specific salt`,
    `- Find references for a specific salt`,
    ``,
    `## Database path and structure`,
    `Path: /mnt/skills/salt-analysis/assets/Molten_Salt_Thermophysical_Properties.json`,
    `Structure: { "MSTDBTP": { "estimates": {...}, "evaluated": { "<salt>": { "<composition>": { "<property>": { "value":..., "reference":..., "DOI":..., "abs_uncertainty":... } } } } } }`,
    `Properties: melt, boil, density, viscosity, heat_capacity, thermal_conductivity, molecular_weight`,
    `Compositions: dash-separated mole fractions (e.g. "0.055-0.945").`,
    saltListSummary,
    `## Query types and how to handle them`,
    ``,
    `### Specific salt query → use analyze_salt.py`,
    `"Show me the phase diagram for LiF-NaF" → run_bash: analyze_salt.py --salt LiF-NaF ...`,
    `"What are the properties of AlCl3-KCl?" → run_bash: analyze_salt.py --salt AlCl3-KCl ...`,
    `"Where does the AlCl3-KCl data come from?" → run_bash: analyze_salt.py --salt AlCl3-KCl ... (prints refs)`,
    ``,
    `### Database-wide statistics → Python script via run_bash`,
    `"How many fluoride salts?" → Python: count salts with "F" in name (exclude "Fe")`,
    `"Most studied salt?" → Python: find salt with most composition entries`,
    `"Which salt has highest melting temperature?" → Python: scan all melt values`,
    ``,
    `### Custom plot (zoom, different property) → Python/matplotlib via run_bash`,
    `"Zoom in to mole fraction around 0.5" → custom matplotlib with xlim=[0.4, 0.6]`,
    `"What about thermal conductivity?" → plot thermal_conductivity instead of melt`,
    `Save to /mnt/data/output/salt-plots/<name>.png and print "Plot saved to ..."`,
    ``,
    `### Research trends → run_bash first, then web_search`,
    `"Has any group studied FLiBe?" → check DB with run_bash, then call web_search`,
    `"Most promising salt for viscosity?" → query DB, then web_search, then synthesize`,
    `"How to improve tritium yield?" → call web_search, answer from knowledge`,
  ].join("\n");
}

/* ------------------------------------------------------------------ */
/*  Execute a tool call via MCP                                        */
/* ------------------------------------------------------------------ */

async function executeTool(
  toolName: string,
  toolInput: Record<string, unknown>
): Promise<ToolCallResult> {
  const t0 = Date.now();
  log("INFO", "Tool:Start", `Invoking tool: ${toolName}`, { args: toolInput });

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
      const command = String(toolInput.command || "");

      // Log whether this is the skill script or a custom command
      if (command.includes("analyze_salt.py")) {
        const saltMatch = command.match(/--salt\s+(\S+)/);
        log("INFO", "Tool:run_bash", `Calling analyze_salt.py skill script`, { salt: saltMatch?.[1] || "unknown" });
      } else if (command.includes("python")) {
        log("INFO", "Tool:run_bash", `Running custom Python script`, { commandPreview: command.slice(0, 200) });
      } else {
        log("INFO", "Tool:run_bash", `Running bash command`, { commandPreview: command.slice(0, 200) });
      }

      const { response, json } = await callMcpRpc(
        "tools/call",
        { name: "bash", arguments: { command } },
        60000
      );

      const content = extractMcpContent(json);
      result.stdout = content.text;
      result.ok = response.ok;

      const elapsed = Date.now() - t0;
      log("INFO", "Tool:run_bash", `Completed in ${elapsed}ms`, {
        ok: result.ok,
        stdoutLength: result.stdout.length,
        stdoutPreview: result.stdout.slice(0, 300),
      });

      // Extract plot path if present
      const plotMatch = result.stdout.match(/Plot saved to\s+(.+)/);
      if (plotMatch) {
        result.plotPath = plotMatch[1].trim();
        log("INFO", "Tool:run_bash", `Plot detected, reading image`, { plotPath: result.plotPath });
        const html = readImageAsBase64Html(result.plotPath);
        if (html) {
          result.displayHtml = html;
          log("INFO", "Tool:run_bash", `Image embedded as base64 HTML (${html.length} chars)`);
        } else {
          log("WARN", "Tool:run_bash", `Failed to read plot image from disk`, { plotPath: result.plotPath });
        }
      }
    } else if (toolName === "display_file") {
      // Safety net — read image directly from disk
      const uri = String(toolInput.uri || "");
      const filePath = uri.startsWith("file://") ? uri.slice("file://".length) : uri;
      log("INFO", "Tool:display_file", `Reading file from disk`, { filePath });
      const html = readImageAsBase64Html(filePath);
      if (html) {
        result.displayHtml = html;
        result.ok = true;
        result.stdout = "The file has been displayed to the user.";
      } else {
        result.ok = false;
        result.stderr = `Could not read image file: ${filePath}`;
        log("WARN", "Tool:display_file", result.stderr);
      }
    } else if (toolName === "web_search") {
      const query = String(toolInput.query || "");
      log("INFO", "Tool:web_search", `Web search requested`, { query });
      result.stdout =
        `Web search requested for: "${query}". ` +
        `No live internet access is available. ` +
        `Please answer using your scientific knowledge about this topic.`;
      result.ok = true;
    } else {
      log("WARN", "Tool:Unknown", `Unknown tool called: ${toolName}`);
      result.stderr = `Unknown tool: ${toolName}`;
    }
  } catch (err) {
    const elapsed = Date.now() - t0;
    result.stderr = err instanceof Error ? err.message : "Unknown tool execution error";
    log("ERROR", `Tool:${toolName}`, `Tool failed after ${elapsed}ms: ${result.stderr}`);
  }

  return result;
}

function extractMcpContent(json: unknown): { text: string; html: string | null } {
  let text = "";
  let html: string | null = null;

  if (!json || typeof json !== "object") return { text: String(json || ""), html };

  const obj = json as Record<string, unknown>;
  const resultObj = (obj.result ?? obj) as Record<string, unknown>;

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

  if (!text && typeof resultObj?.stdout === "string") {
    text = resultObj.stdout;
  }

  return { text, html };
}

/* ------------------------------------------------------------------ */
/*  Azure OpenAI agentic loop                                          */
/* ------------------------------------------------------------------ */

async function runAgentLoop(
  userMessage: string,
  history: Array<{ role: string; content: string }>
): Promise<{ response: string; toolCalls: ToolCallResult[] }> {
  const azureConfig = getAzureConfig();

  const hasAzure = !!process.env.AZURE_OPENAI_ENDPOINT && !!process.env.AZURE_OPENAI_API_KEY;
  const hasOpenAI = !!process.env.OPENAI_API_KEY;
  if (!hasAzure && !hasOpenAI) {
    log("ERROR", "Agent", "No API key configured");
    return {
      response:
        "No API key configured. Set AZURE_OPENAI_ENDPOINT + AZURE_OPENAI_API_KEY + AZURE_OPENAI_DEPLOYMENT_NAME, " +
        "or OPENAI_API_KEY + OPENAI_BASE_URL for a generic OpenAI-compatible endpoint.",
      toolCalls: [],
    };
  }

  const systemPrompt = buildSystemPrompt();
  const toolCalls: ToolCallResult[] = [];

  const messages: Array<Record<string, unknown>> = [
    { role: "system", content: systemPrompt },
  ];

  const recentHistory = history.slice(-10);
  for (const msg of recentHistory) {
    if (msg.role === "user" || msg.role === "assistant") {
      messages.push({ role: msg.role, content: msg.content });
    }
  }

  messages.push({ role: "user", content: userMessage });

  log("INFO", "Agent", `New request`, {
    url: azureConfig.url,
    model: azureConfig.model,
    userMessage: userMessage.slice(0, 200),
    historyTurns: recentHistory.length,
  });

  const maxIterations = 8;
  for (let i = 0; i < maxIterations; i++) {
    const iterStart = Date.now();
    log("INFO", "Agent:LLM", `Iteration ${i + 1}/${maxIterations} — sending request to LLM`);

    const payload: Record<string, unknown> = {
      messages,
      tools: TOOLS,
      tool_choice: "auto",
    };
    if (!process.env.AZURE_OPENAI_ENDPOINT) {
      payload.model = azureConfig.model;
    }

    let resp: Response;
    try {
      resp = await fetch(azureConfig.url, {
        method: "POST",
        headers: azureConfig.headers,
        body: JSON.stringify(payload),
      });
    } catch (fetchErr) {
      const msg = fetchErr instanceof Error ? fetchErr.message : "Unknown error";
      log("ERROR", "Agent:LLM", `Failed to connect: ${msg}`);
      throw new Error(`Failed to connect to LLM endpoint (${azureConfig.url}): ${msg}`);
    }

    const responseText = await resp.text();
    const llmElapsed = Date.now() - iterStart;

    if (!resp.ok) {
      log("ERROR", "Agent:LLM", `HTTP ${resp.status} after ${llmElapsed}ms`, { responsePreview: responseText.slice(0, 300) });
      throw new Error(`Azure OpenAI error (${resp.status}): ${responseText.slice(0, 500)}`);
    }

    let data: Record<string, unknown>;
    try {
      data = JSON.parse(responseText);
    } catch {
      log("ERROR", "Agent:LLM", `Non-JSON response after ${llmElapsed}ms`);
      throw new Error(`LLM returned non-JSON (${resp.status}). First 200 chars: ${responseText.slice(0, 200)}`);
    }

    const choices = data.choices as Array<Record<string, unknown>> | undefined;
    const choice = choices?.[0];
    const assistantMsg = choice?.message as Record<string, unknown> | undefined;

    if (!assistantMsg) {
      log("WARN", "Agent:LLM", `No message in response after ${llmElapsed}ms`);
      return { response: "No response from LLM.", toolCalls };
    }

    messages.push(assistantMsg);

    const finishReason = choice?.finish_reason as string | undefined;
    const msgToolCalls = assistantMsg.tool_calls as Array<Record<string, unknown>> | undefined;
    const textContent = typeof assistantMsg.content === "string" ? assistantMsg.content : "";

    log("INFO", "Agent:LLM", `Response received in ${llmElapsed}ms`, {
      finishReason,
      toolCallCount: msgToolCalls?.length || 0,
      textLength: textContent.length,
      textPreview: textContent.slice(0, 200),
    });

    // If the model did not request tool calls, return the text
    if (finishReason !== "tool_calls" || !msgToolCalls?.length) {
      log("INFO", "Agent", `Completed after ${i + 1} iteration(s), ${toolCalls.length} tool call(s)`);
      return { response: textContent, toolCalls };
    }

    // Process each tool call
    for (const tc of msgToolCalls) {
      const fnObj = tc.function as Record<string, unknown> | undefined;
      const toolName = typeof fnObj?.name === "string" ? fnObj.name : "";
      let toolInput: Record<string, unknown> = {};
      try {
        toolInput = JSON.parse(typeof fnObj?.arguments === "string" ? fnObj.arguments : "{}");
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

  log("WARN", "Agent", `Reached max iterations (${maxIterations}), returning partial result`);
  return { response: "Agent reached maximum iterations.", toolCalls };
}

/* ------------------------------------------------------------------ */
/*  POST handler                                                       */
/* ------------------------------------------------------------------ */

export async function POST(request: Request) {
  const requestStart = Date.now();
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
    const elapsed = Date.now() - requestStart;
    log("INFO", "POST", `Request completed in ${elapsed}ms`, {
      toolCallCount: toolCalls.length,
      responseLength: response.length,
    });
    return NextResponse.json(
      { ok: true, response, toolCalls } satisfies ChatResponse,
      { status: 200 }
    );
  } catch (error) {
    const elapsed = Date.now() - requestStart;
    const errMsg = error instanceof Error ? error.message : "Unknown error";
    log("ERROR", "POST", `Request failed after ${elapsed}ms: ${errMsg}`);
    return NextResponse.json(
      { ok: false, response: "", error: errMsg } satisfies ChatResponse,
      { status: 502 }
    );
  }
}
