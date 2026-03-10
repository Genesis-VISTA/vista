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

/** Shape returned by MCP tools/list */
type McpToolDef = {
  name: string;
  description?: string;
  inputSchema?: Record<string, unknown>;
};

/** OpenAI function-calling tool format */
type OpenAiTool = {
  type: "function";
  function: {
    name: string;
    description: string;
    parameters: Record<string, unknown>;
  };
};

/* ------------------------------------------------------------------ */
/*  Logging                                                            */
/* ------------------------------------------------------------------ */

function log(
  level: "INFO" | "WARN" | "ERROR",
  area: string,
  message: string,
  extra?: Record<string, unknown>
) {
  const ts = new Date().toISOString();
  const prefix = `[VISTA ${level}] [${ts}] [${area}]`;
  const suffix = extra ? " " + JSON.stringify(extra) : "";
  if (level === "ERROR") console.error(`${prefix} ${message}${suffix}`);
  else if (level === "WARN") console.warn(`${prefix} ${message}${suffix}`);
  else console.log(`${prefix} ${message}${suffix}`);
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
/*  MCP tool discovery                                                 */
/* ------------------------------------------------------------------ */

/**
 * Discover tools from the MCP server via JSON-RPC tools/list,
 * then convert them to OpenAI function-calling format.
 *
 * The MCP server exposes: bash, create_file, view, display_file.
 * We expose bash (as "run_bash") to the LLM.
 */

// Which MCP tools to expose to the LLM, and how to rename them
const TOOL_EXPOSE_MAP: Record<string, string> = {
  bash: "run_bash",
};

// Reverse map: OpenAI tool name → MCP tool name
const TOOL_REVERSE_MAP: Record<string, string> = {};
for (const [mcpName, openaiName] of Object.entries(TOOL_EXPOSE_MAP)) {
  TOOL_REVERSE_MAP[openaiName] = mcpName;
}

let cachedTools: OpenAiTool[] | null = null;
let cachedToolsTime = 0;
const TOOL_CACHE_TTL_MS = 60_000; // re-discover every 60s

async function discoverTools(): Promise<OpenAiTool[]> {
  const now = Date.now();
  if (cachedTools && now - cachedToolsTime < TOOL_CACHE_TTL_MS) {
    return cachedTools;
  }

  log("INFO", "ToolDiscovery", "Fetching tools from MCP server via tools/list");

  try {
    const { response, json } = await callMcpRpc("tools/list", {}, 5000);
    if (!response.ok) {
      log("WARN", "ToolDiscovery", `MCP tools/list failed (HTTP ${response.status})`);
      return cachedTools ?? buildFallbackTools();
    }

    const envelope = json as Record<string, unknown> | null;
    const result = (envelope?.result ?? envelope) as Record<string, unknown> | null;
    const rawTools = Array.isArray(result?.tools) ? result.tools : [];

    const mcpTools: McpToolDef[] = rawTools
      .filter((t: unknown) => t && typeof t === "object")
      .map((t: unknown) => {
        const obj = t as Record<string, unknown>;
        return {
          name: typeof obj.name === "string" ? obj.name : "",
          description: typeof obj.description === "string" ? obj.description : undefined,
          inputSchema: typeof obj.inputSchema === "object" && obj.inputSchema ? obj.inputSchema as Record<string, unknown> : undefined,
        };
      });

    log("INFO", "ToolDiscovery", `Discovered ${mcpTools.length} MCP tools`, {
      names: mcpTools.map((t) => t.name),
    });

    // Convert MCP tools → OpenAI function-calling format, filtering to exposed set
    const openaiTools: OpenAiTool[] = [];
    for (const mcp of mcpTools) {
      const exposedName = TOOL_EXPOSE_MAP[mcp.name];
      if (!exposedName) continue; // not exposed to the LLM

      const description = mcp.description ?? "";
      const parameters = mcp.inputSchema ?? { type: "object", properties: {} };

      openaiTools.push({
        type: "function",
        function: { name: exposedName, description, parameters },
      });
    }

    if (openaiTools.length === 0) {
      log("WARN", "ToolDiscovery", "No matching tools after filtering — using fallback");
      cachedTools = buildFallbackTools();
    } else {
      cachedTools = openaiTools;
    }

    cachedToolsTime = now;
    log("INFO", "ToolDiscovery", `Exposing ${cachedTools.length} tools to LLM`, {
      names: cachedTools.map((t) => t.function.name),
    });
    return cachedTools;
  } catch (err) {
    log("ERROR", "ToolDiscovery", `tools/list failed: ${err instanceof Error ? err.message : "Unknown"}`);
    return cachedTools ?? buildFallbackTools();
  }
}

/** Hardcoded fallback if MCP discovery fails — minimal descriptions */
function buildFallbackTools(): OpenAiTool[] {
  return [
    {
      type: "function",
      function: {
        name: "run_bash",
        description: "Run a bash command inside the sandbox. Use for all database queries and script execution.",
        parameters: {
          type: "object",
          properties: { command: { type: "string", description: "The bash command to execute" } },
          required: ["command"],
        },
      },
    },
  ];
}

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
    `You have access to a molten salt database and analysis scripts via run_bash.`,
    `For questions beyond the database (research trends, groups, general nuclear science), answer using your own scientific knowledge.`,
    ``,
    skillsPrompt,
    ``,
    `## CRITICAL RULES — read these first`,
    `1. You MUST call run_bash for ANY question about the database. NEVER answer data questions from memory or guess values/counts.`,
    `2. For "show me the phase diagram" or any phase diagram / liquidus / eutectic request, use plot_phase_diagram.py:`,
    `     MPLBACKEND=Agg python3 /mnt/skills/salt-analysis/scripts/plot_phase_diagram.py --salt <SALT_NAME> --output-dir /mnt/data/output/salt-plots`,
    `3. For property statistics, references, data overview, or general salt visualization, use analyze_salt.py:`,
    `     MPLBACKEND=Agg python3 /mnt/skills/salt-analysis/scripts/analyze_salt.py --salt <SALT_NAME> --output-dir /mnt/data/output/salt-plots`,
    `4. For database-wide queries (counting salts, finding extremes across all salts), write a short Python script via run_bash.`,
    `5. Plots are AUTOMATICALLY displayed when stdout contains "Plot saved to ...". Do NOT call any display tool.`,
    `6. ALWAYS set MPLBACKEND=Agg before running any matplotlib code.`,
    `7. For research trend questions beyond the database, first query the database with run_bash if a salt/property is mentioned, then answer using your scientific knowledge.`,
    ``,
    `## Skill Scripts`,
    ``,
    `### plot_phase_diagram.py — Liquidus Phase Diagrams`,
    `Location: /mnt/skills/salt-analysis/scripts/plot_phase_diagram.py`,
    `Usage:  MPLBACKEND=Agg python3 /mnt/skills/salt-analysis/scripts/plot_phase_diagram.py --salt <SALT_NAME> --output-dir /mnt/data/output/salt-plots`,
    ``,
    `What it produces:`,
    `- Binary: spline-fitted liquidus curve, eutectic point, pure-component endpoints, uncertainty band, labeled phase regions`,
    `- Ternary: isothermal liquidus contours, eutectic valley minimum, endpoint temperatures`,
    `- Quaternary: four faceted ternary projections with contours`,
    ``,
    `Use when the user says: "phase diagram", "liquidus", "eutectic", "phase regions", "melting curve"`,
    ``,
    `### analyze_salt.py — Property Statistics and Raw Data`,
    `Location: /mnt/skills/salt-analysis/scripts/analyze_salt.py`,
    `Usage:  MPLBACKEND=Agg python3 /mnt/skills/salt-analysis/scripts/analyze_salt.py --salt <SALT_NAME> --output-dir /mnt/data/output/salt-plots`,
    ``,
    `What it does: prints statistics, generates raw data plots, prints all references with DOIs`,
    `Use when the user says: "statistics", "properties", "references", "how many measurements", "data overview"`,
    ``,
    `## Database path and structure`,
    `Path: /mnt/skills/salt-analysis/assets/Molten_Salt_Thermophysical_Properties.json`,
    `Structure: { "MSTDBTP": { "estimates": {...}, "evaluated": { "<salt>": { "<composition>": { "<property>": { ... } } } } } }`,
    ``,
    `### Property data formats`,
    `**Scalar properties (melt, boil):** have a "value" field with a single number.`,
    `  { "value": 1024.0, "abs_uncertainty": 5.0, "reference": "...", "DOI": "..." }`,
    ``,
    `**Temperature-dependent properties (viscosity, density, heat_capacity, thermal_conductivity, surface_tension):**`,
    `  have a "values" field containing model COEFFICIENTS, NOT direct measurements.`,
    `  { "values": [A, B, ...], "range": [T_min, T_max], "pct_uncertainty": ..., "reference": "...", "DOI": "..." }`,
    ``,
    `  Viscosity model (2-coefficient entries):  mu(mPa·s) = A * exp(B / (R*T))  where R=8.314 J/(mol·K)`,
    `  Viscosity model (3-coefficient entries):  mu(mPa·s) = exp(A + B/T + C/T²)`,
    `  Density model:  rho(g/cm³) = A + B*T  (polynomial)`,
    `  NOTE: "range" can be [0.0, 0.0] meaning unset — treat those entries as valid.`,
    `  NOTE: Viscosity unit is mPa·s (millipascal-seconds), NOT Pa·s.`,
    ``,
    `Available properties: melt, boil, density, viscosity, heat_capacity, thermal_conductivity, surface_tension, molecular_weight`,
    `Compositions: dash-separated mole fractions (e.g. "0.055-0.945").`,
    saltListSummary,
    `## Query types and how to handle them`,
    ``,
    `### Type 1a: Phase diagram → use plot_phase_diagram.py`,
    `"Show me the phase diagram for BeF2-LiF" → run_bash: plot_phase_diagram.py --salt BeF2-LiF ...`,
    `"What is the eutectic point of KCl-MgCl2?" → run_bash: plot_phase_diagram.py --salt KCl-MgCl2 ...`,
    ``,
    `### Type 1b: Properties / statistics / references → use analyze_salt.py`,
    `"What are the properties of AlCl3-KCl?" → run_bash: analyze_salt.py --salt AlCl3-KCl ...`,
    `"Where does the data come from?" → run_bash: analyze_salt.py --salt <SALT> ... (prints refs)`,
    ``,
    `### Type 1c: Database-wide statistics → Python script via run_bash`,
    `"How many fluoride salts?" → Python: count salts with "F" in name (exclude "Fe")`,
    `"Most studied salt?" → Python: find salt with most composition entries`,
    `"Which salt has highest melting temperature?" → Python: scan all melt values`,
    ``,
    `### Type 1d: Custom plot (zoom, different property) → Python/matplotlib via run_bash`,
    `"Zoom in to mole percentage around 0.5" → matplotlib with plt.xlim(0.4, 0.6)`,
    `"What about thermal conductivity?" → extract thermal_conductivity and plot vs composition`,
    `For follow-up queries, use the salt name from the conversation history.`,
    ``,
    `### Type 2: Research trends and general scientific questions`,
    `These questions ask about research groups, literature trends, promising materials, or topics beyond the database.`,
    ``,
    `**Step 1: Check the local database first (if the question mentions a specific salt or property).**`,
    `Call run_bash with a Python script to check what data exists.`,
    ``,
    `**Step 2: Answer using your scientific knowledge.**`,
    `Combine what the database shows (compositions, property values, references) with your knowledge of molten salt science, nuclear engineering, and relevant research.`,
    `Cite DOIs from the database when available.`,
    ``,
    `Examples:`,
    `User: "Has any group studied FLiBe?"`,
    `→ Step 1: run_bash — check if BeF2-LiF exists in the database, list its properties and references`,
    `→ Step 2: Answer combining DB data with your knowledge of FLiBe research (ORNL, MSR program, etc.)`,
    ``,
    `User: "What is the most promising salt for better viscosity?"`,
    `→ Step 1: run_bash — compute viscosity at T=973K for all salts, rank by lowest`,
    `→ Step 2: Answer with DB rankings + your knowledge of promising candidates and trade-offs`,
    ``,
    `User: "How to improve the yield of tritium?"`,
    `→ Step 1: skip (general nuclear engineering question, not a database query)`,
    `→ Step 2: Answer directly from your knowledge about breeding blanket design, Li-6 enrichment, etc.`,
    ``,
    `For viscosity ranking, use this exact script via run_bash:`,
    `  python3 -c "`,
    `  import json, math`,
    `  R=8.314; T=973.0`,
    `  d=json.load(open('/mnt/skills/salt-analysis/assets/Molten_Salt_Thermophysical_Properties.json'))`,
    `  e=d['MSTDBTP']['evaluated']; rows=[]`,
    `  for s,comps in e.items():`,
    `    for c,props in comps.items():`,
    `      v=props.get('viscosity')`,
    `      if not isinstance(v,dict): continue`,
    `      vals=v.get('values'); rng=v.get('range',[0,0])`,
    `      if not isinstance(vals,list) or len(vals)<2: continue`,
    `      Tmin,Tmax=(rng if isinstance(rng,list) and len(rng)==2 else (0,0))`,
    `      if not ((Tmin<=T<=Tmax) or (Tmin==0 and Tmax==0)): continue`,
    `      try:`,
    `        if len(vals)==2: mu=float(vals[0])*math.exp(float(vals[1])/(R*T))`,
    `        else: mu=math.exp(float(vals[0])+float(vals[1])/T+float(vals[2])/(T*T))`,
    `      except: continue`,
    `      if 0<mu<100: rows.append((s,c,mu,v.get('DOI'),v.get('reference')))`,
    `  rows.sort(key=lambda x:x[2])`,
    `  print(f'Salts ranked by viscosity at {T}K ({len(rows)} entries):')`,
    `  for i,(s,c,mu,doi,ref) in enumerate(rows[:20],1): print(f'{i}. {s} ({c}): {mu:.4f} mPa-s DOI:{doi}')`,
    `  "`,
    ``,
    `## Rules`,
    `- ALWAYS call run_bash — never answer data questions without running code first.`,
    `- For "phase diagram" / "liquidus" / "eutectic" → plot_phase_diagram.py.`,
    `- For "statistics" / "properties" / "references" → analyze_salt.py.`,
    `- If ambiguous ("show me salt X"), use plot_phase_diagram.py for multi-component salts.`,
    `- ALWAYS set MPLBACKEND=Agg. ALWAYS print "Plot saved to <path>".`,
    `- For research/trend questions: query the database first if relevant, then answer from your own scientific knowledge.`,
    `- Include references/DOIs from the database when available.`,
    `- Be concise but thorough.`,
  ].join("\n");
}

/* ------------------------------------------------------------------ */
/*  Generic MCP tool execution                                         */
/* ------------------------------------------------------------------ */

/**
 * Execute a tool call by routing it through the MCP server.
 *
 * The LLM sees "run_bash" which maps back to MCP tool "bash"
 * via TOOL_REVERSE_MAP, then calls it via JSON-RPC tools/call.
 *
 * After the MCP call returns, we apply post-processing:
 *   - detect "Plot saved to /path/file.png" in stdout and embed the image
 */
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

  // Map OpenAI tool name back to MCP tool name
  const mcpToolName = TOOL_REVERSE_MAP[toolName] ?? toolName;

  // Log skill-script detection for run_bash
  if (mcpToolName === "bash") {
    const command = String(toolInput.command || "");
    if (command.includes("plot_phase_diagram.py")) {
      const salt = command.match(/--salt\s+(\S+)/)?.[1] || "unknown";
      log("INFO", "Tool:run_bash", `Calling plot_phase_diagram.py skill script`, { salt });
    } else if (command.includes("analyze_salt.py")) {
      const salt = command.match(/--salt\s+(\S+)/)?.[1] || "unknown";
      log("INFO", "Tool:run_bash", `Calling analyze_salt.py skill script`, { salt });
    } else if (command.includes("python")) {
      log("INFO", "Tool:run_bash", `Running custom Python script`, { commandPreview: command.slice(0, 200) });
    } else {
      log("INFO", "Tool:run_bash", `Running bash command`, { commandPreview: command.slice(0, 200) });
    }
  }

  try {
    // Route through MCP
    const timeoutMs = mcpToolName === "bash" ? 60000 : 20000;
    const { response, json } = await callMcpRpc(
      "tools/call",
      { name: mcpToolName, arguments: toolInput },
      timeoutMs
    );

    const content = extractMcpContent(json);
    result.stdout = content.text;
    result.ok = response.ok;

    const elapsed = Date.now() - t0;
    log("INFO", `Tool:${toolName}`, `Completed in ${elapsed}ms`, {
      ok: result.ok,
      stdoutLength: result.stdout.length,
      stdoutPreview: result.stdout.slice(0, 300),
    });

    // Post-processing: detect saved plots and embed as base64
    const plotMatch = result.stdout.match(/Plot saved to\s+(\/\S+\.(?:png|jpg|jpeg|svg|gif))/);
    if (plotMatch) {
      result.plotPath = plotMatch[1].trim();
      log("INFO", `Tool:${toolName}`, `Plot detected, reading image`, { plotPath: result.plotPath });
      const html = readImageAsBase64Html(result.plotPath);
      if (html) {
        result.displayHtml = html;
        log("INFO", `Tool:${toolName}`, `Image embedded (${html.length} chars)`);
      } else {
        log("WARN", `Tool:${toolName}`, `Failed to read plot image`, { plotPath: result.plotPath });
      }
    }
  } catch (err) {
    const elapsed = Date.now() - t0;
    result.stderr = err instanceof Error ? err.message : "Unknown tool execution error";
    log("ERROR", `Tool:${toolName}`, `Failed after ${elapsed}ms: ${result.stderr}`);
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

  // Discover tools from MCP server
  const tools = await discoverTools();

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
    toolCount: tools.length,
    toolNames: tools.map((t) => t.function.name),
  });

  const maxIterations = 10;
  for (let i = 0; i < maxIterations; i++) {
    const iterStart = Date.now();
    log("INFO", "Agent:LLM", `Iteration ${i + 1}/${maxIterations} — sending request to LLM`);

    const payload: Record<string, unknown> = {
      messages,
      tools,
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

    if (finishReason !== "tool_calls" || !msgToolCalls?.length) {
      log("INFO", "Agent", `Completed after ${i + 1} iteration(s), ${toolCalls.length} tool call(s)`);
      return { response: textContent, toolCalls };
    }

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

  log("WARN", "Agent", `Reached max iterations (${maxIterations})`);
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
