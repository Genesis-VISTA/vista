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
  const azureEndpoint = (process.env.AZURE_OPENAI_ENDPOINT || "").replace(
    /\/+$/,
    ""
  );
  const azureKey = process.env.AZURE_OPENAI_API_KEY || "";
  const azureDeployment = process.env.AZURE_OPENAI_DEPLOYMENT_NAME || "";
  const azureApiVersion =
    process.env.AZURE_OPENAI_API_VERSION || "2025-01-01-preview";

  if (azureEndpoint && azureKey && azureDeployment) {
    return {
      url: `${azureEndpoint}/openai/deployments/${azureDeployment}/chat/completions?api-version=${azureApiVersion}`,
      headers: { "content-type": "application/json", "api-key": azureKey },
      model: azureDeployment,
    };
  }

  // Fallback: generic OpenAI-compatible endpoint
  const chatUrlOverride = (process.env.OPENAI_CHAT_URL || "")
    .trim()
    .replace(/\/+$/, "");
  const baseUrl = (
    process.env.OPENAI_BASE_URL || "https://api.openai.com/v1"
  ).replace(/\/+$/, "");
  const apiKey = (process.env.OPENAI_API_KEY || "").replace(
    /^["'\s]+|["'\s]+$/g,
    ""
  );
  const model = process.env.OPENAI_MODEL || "gpt-4o-mini";
  const authMode = (process.env.OPENAI_AUTH_MODE || "bearer").toLowerCase();

  let url: string;
  if (chatUrlOverride) {
    if (
      authMode === "api_key" &&
      !chatUrlOverride.includes("/openai/") &&
      !chatUrlOverride.includes("/chat/completions")
    ) {
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
/*  Web search implementation                                          */
/* ------------------------------------------------------------------ */

/**
 * Execute a real web search using Bing Search API (Azure) or SerpAPI.
 *
 * Required env vars (pick one):
 *   BING_SEARCH_API_KEY              – Azure Bing Search v7 key
 *   BING_SEARCH_ENDPOINT             – optional, defaults to https://api.bing.microsoft.com
 *   SERP_API_KEY                     – SerpAPI key (alternative)
 *
 * If neither is set, returns a fallback message asking the LLM to use
 * its own knowledge.
 */
async function executeWebSearch(query: string): Promise<string> {
  const bingKey = process.env.BING_SEARCH_API_KEY || "";
  const bingEndpoint = (
    process.env.BING_SEARCH_ENDPOINT || "https://api.bing.microsoft.com"
  ).replace(/\/+$/, "");
  const serpKey = process.env.SERP_API_KEY || "";

  // ---- Bing Search API v7 ----
  if (bingKey) {
    try {
      const url = `${bingEndpoint}/v7.0/search?q=${encodeURIComponent(query)}&count=5&responseFilter=Webpages`;
      log("INFO", "WebSearch:Bing", `Searching Bing`, { query, url });

      const resp = await fetch(url, {
        method: "GET",
        headers: { "Ocp-Apim-Subscription-Key": bingKey },
      });

      if (!resp.ok) {
        const errText = await resp.text();
        log("ERROR", "WebSearch:Bing", `Bing API error (${resp.status})`, {
          error: errText.slice(0, 300),
        });
        return `Web search failed (Bing HTTP ${resp.status}). Answer using your scientific knowledge about: "${query}"`;
      }

      const data = await resp.json();
      const pages =
        (data as Record<string, unknown>).webPages as
          | Record<string, unknown>
          | undefined;
      const results = (pages?.value as Array<Record<string, unknown>>) || [];

      if (results.length === 0) {
        log("INFO", "WebSearch:Bing", `No results for query`, { query });
        return `No web results found for "${query}". Answer using your scientific knowledge.`;
      }

      const formatted = results
        .slice(0, 5)
        .map((r, i) => {
          const name = r.name || "Untitled";
          const snippet = r.snippet || "";
          const url = r.url || "";
          return `[${i + 1}] ${name}\n    ${snippet}\n    URL: ${url}`;
        })
        .join("\n\n");

      log("INFO", "WebSearch:Bing", `Got ${results.length} results`, {
        query,
      });
      return `Web search results for "${query}":\n\n${formatted}\n\nUse these results to inform your answer. Cite sources when relevant.`;
    } catch (err) {
      log("ERROR", "WebSearch:Bing", `Bing search exception`, {
        error: err instanceof Error ? err.message : "Unknown",
      });
      return `Web search failed. Answer using your scientific knowledge about: "${query}"`;
    }
  }

  // ---- SerpAPI fallback ----
  if (serpKey) {
    try {
      const url = `https://serpapi.com/search.json?q=${encodeURIComponent(query)}&api_key=${serpKey}&num=5`;
      log("INFO", "WebSearch:Serp", `Searching SerpAPI`, { query });

      const resp = await fetch(url);
      if (!resp.ok) {
        log("ERROR", "WebSearch:Serp", `SerpAPI error (${resp.status})`);
        return `Web search failed (SerpAPI HTTP ${resp.status}). Answer using your scientific knowledge about: "${query}"`;
      }

      const data = (await resp.json()) as Record<string, unknown>;
      const organic = (data.organic_results as Array<Record<string, unknown>>) || [];

      if (organic.length === 0) {
        return `No web results found for "${query}". Answer using your scientific knowledge.`;
      }

      const formatted = organic
        .slice(0, 5)
        .map((r, i) => {
          const title = r.title || "Untitled";
          const snippet = r.snippet || "";
          const link = r.link || "";
          return `[${i + 1}] ${title}\n    ${snippet}\n    URL: ${link}`;
        })
        .join("\n\n");

      log("INFO", "WebSearch:Serp", `Got ${organic.length} results`, { query });
      return `Web search results for "${query}":\n\n${formatted}\n\nUse these results to inform your answer. Cite sources when relevant.`;
    } catch (err) {
      log("ERROR", "WebSearch:Serp", `SerpAPI exception`, {
        error: err instanceof Error ? err.message : "Unknown",
      });
      return `Web search failed. Answer using your scientific knowledge about: "${query}"`;
    }
  }

  // ---- No search API configured — graceful fallback ----
  log("WARN", "WebSearch", `No search API key configured (set BING_SEARCH_API_KEY or SERP_API_KEY). Falling back to LLM knowledge.`);
  return (
    `No web search API is configured. Answer the following question using your scientific knowledge:\n"${query}"\n\n` +
    `To enable live web search, set BING_SEARCH_API_KEY (Azure Bing Search v7) or SERP_API_KEY (SerpAPI) in your .env file.`
  );
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
        "The phase diagram script is at /mnt/skills/salt-analysis/scripts/plot_phase_diagram.py. " +
        "When a plot is saved, it is automatically displayed to the user.",
      parameters: {
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
  },
  {
    type: "function" as const,
    function: {
      name: "web_search",
      description:
        "Search the web for scientific literature, research groups, recent studies, " +
        "and trends related to molten salts or nuclear energy topics. " +
        "If a web search API key is configured, returns real search results with titles, snippets, and URLs. " +
        "If no API key is available, you should answer using your own scientific knowledge. " +
        "Use this AFTER querying the local database (when relevant) to supplement " +
        "with external research. Good for: finding research groups, recent papers, " +
        "emerging trends, or answering questions beyond the database scope.",
      parameters: {
        type: "object" as const,
        properties: {
          query: {
            type: "string",
            description:
              "A specific, well-formed search query. Use scientific terms. " +
              'Example: "FLiBe molten salt viscosity measurement recent studies"',
          },
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

  return [
    `You are VISTA, a scientific assistant for molten salt thermophysical properties.`,
    `You have access to a molten salt database and analysis scripts via run_bash, and web search via web_search.`,
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
    `7. For research trend questions, follow the Type 2 workflow below — it REQUIRES calling tools.`,
    ``,
    `## Skill Scripts`,
    ``,
    `### plot_phase_diagram.py — Liquidus Phase Diagrams (for "phase diagram" requests)`,
    `Location: /mnt/skills/salt-analysis/scripts/plot_phase_diagram.py`,
    `Usage:  MPLBACKEND=Agg python3 /mnt/skills/salt-analysis/scripts/plot_phase_diagram.py --salt <SALT_NAME> --output-dir /mnt/data/output/salt-plots`,
    ``,
    `What it produces:`,
    `- Binary: spline-fitted liquidus curve, eutectic point (green triangle), pure-component endpoint melting points, uncertainty band, labeled phase regions (Liquid / Liquid+Solid A / Liquid+Solid B / Solid A+Solid B)`,
    `- Ternary: isothermal liquidus contours on a ternary triangle, eutectic valley minimum, endpoint temperatures`,
    `- Quaternary: four faceted ternary projections with contours`,
    ``,
    `Use when the user says: "phase diagram", "liquidus", "eutectic", "phase regions", "melting curve"`,
    ``,
    `### analyze_salt.py — Property Statistics and Raw Data`,
    `Location: /mnt/skills/salt-analysis/scripts/analyze_salt.py`,
    `Usage:  MPLBACKEND=Agg python3 /mnt/skills/salt-analysis/scripts/analyze_salt.py --salt <SALT_NAME> --output-dir /mnt/data/output/salt-plots`,
    ``,
    `What it does:`,
    `- Prints statistics: total measurements, compositions, property ranges`,
    `- Generates raw melting-temperature-vs-composition plots (no phase labels, no spline, no eutectic detection)`,
    `- Prints all references with DOIs`,
    `- Saves plot to /mnt/data/output/salt-plots/<SALT_NAME>.png`,
    ``,
    `Use when the user says: "statistics", "properties", "references", "how many measurements", "data overview"`,
    ``,
    `## Database path and structure`,
    `Path: /mnt/skills/salt-analysis/assets/Molten_Salt_Thermophysical_Properties.json`,
    `Structure: { "MSTDBTP": { "estimates": {...}, "evaluated": { "<salt>": { "<composition>": { "<property>": { ... } } } } } }`,
    ``,
    `### Property data formats`,
    `Properties store data in TWO different formats depending on the property:`,
    ``,
    `**Scalar properties (melt, boil):** have a "value" field with a single number.`,
    `  { "value": 1024.0, "abs_uncertainty": 5.0, "reference": "...", "DOI": "..." }`,
    ``,
    `**Temperature-dependent properties (viscosity, density, heat_capacity, thermal_conductivity, surface_tension):**`,
    `  have a "values" field containing Arrhenius/polynomial COEFFICIENTS, NOT direct measurements.`,
    `  { "values": [A, B], "range": [T_min, T_max], "pct_uncertainty": ..., "reference": "...", "DOI": "..." }`,
    ``,
    `  For viscosity: mu(T) = A * exp(B / T)  where A and B are the first two entries in "values".`,
    `  For density:   rho(T) = A + B*T        where "values": [A, B] are polynomial coefficients.`,
    `  Some entries have 3+ coefficients for higher-order fits.`,
    `  "range": [T_min, T_max] gives the valid temperature range in Kelvin.`,
    ``,
    `  IMPORTANT: To compare viscosity across salts, compute mu(T) = values[0] * exp(values[1] / T)`,
    `  at a reference temperature (e.g., T = 973 K). Do NOT compare the raw coefficient values directly.`,
    ``,
    `Available properties: melt, boil, density, viscosity, heat_capacity, thermal_conductivity, surface_tension, molecular_weight`,
    `Compositions: dash-separated mole fractions (e.g. "0.055-0.945").`,
    saltListSummary,
    `## Query types and how to handle them`,
    ``,
    `### Type 1a: Phase diagram → use plot_phase_diagram.py`,
    `"Show me the phase diagram for BeF2-LiF" → run_bash: plot_phase_diagram.py --salt BeF2-LiF ...`,
    `"Plot the phase diagram for AlCl3-KCl" → run_bash: plot_phase_diagram.py --salt AlCl3-KCl ...`,
    `"What is the eutectic point of KCl-MgCl2?" → run_bash: plot_phase_diagram.py --salt KCl-MgCl2 ...`,
    ``,
    `### Type 1b: Properties / statistics / references → use analyze_salt.py`,
    `"What are the properties of AlCl3-KCl?" → run_bash: analyze_salt.py --salt AlCl3-KCl ...`,
    `"Where does the AlCl3-KCl data come from?" → run_bash: analyze_salt.py --salt AlCl3-KCl ... (prints refs)`,
    `"Show me the data for UF3" → run_bash: analyze_salt.py --salt UF3 ...`,
    ``,
    `### Type 1b: Database-wide statistics → Python script via run_bash`,
    `"How many fluoride salts?" → Python: count salts with "F" in name (exclude "Fe")`,
    `"Most studied salt?" → Python: find salt with most composition entries`,
    `"Which salt has highest melting temperature?" → Python: scan all melt values`,
    ``,
    `### Type 1c: Custom plot (zoom, different property) → Python/matplotlib via run_bash`,
    `These are follow-up requests that modify a previous plot. Write a custom Python script.`,
    ``,
    `"Zoom in to mole percentage around 0.5" → Write matplotlib script that:`,
    `  1. Loads the JSON, extracts the same salt's melt data`,
    `  2. Plots with plt.xlim(0.4, 0.6) to zoom in`,
    `  3. Saves to /mnt/data/output/salt-plots/custom_zoom.png`,
    `  4. Prints "Plot saved to /mnt/data/output/salt-plots/custom_zoom.png"`,
    ``,
    `"What about thermal conductivity?" → Write matplotlib script that:`,
    `  1. Loads the JSON, extracts the same salt's thermal_conductivity data instead of melt`,
    `  2. For each composition, reads props["thermal_conductivity"]["values"] (coefficient list) or props["thermal_conductivity"]["value"]`,
    `  3. Plots thermal_conductivity vs composition`,
    `  4. Saves to /mnt/data/output/salt-plots/custom_thermal_conductivity.png`,
    `  5. Prints "Plot saved to /mnt/data/output/salt-plots/custom_thermal_conductivity.png"`,
    ``,
    `IMPORTANT: For follow-up queries, use the salt name from the conversation history (the previous turn).`,
    `IMPORTANT: Always set MPLBACKEND=Agg and save to /mnt/data/output/salt-plots/.`,
    ``,
    `### Type 2: Research trends and external knowledge — REQUIRES MULTI-STEP TOOL USE`,
    ``,
    `These questions ask about research groups, literature trends, promising materials, or topics beyond the database.`,
    `You MUST follow this multi-step workflow:`,
    ``,
    `**Step 1: Check the local database first (if the question mentions a specific salt or property).**`,
    `Call run_bash with a Python script to check what data exists. Example:`,
    `  python3 -c "import json; d=json.load(open('/mnt/skills/salt-analysis/assets/Molten_Salt_Thermophysical_Properties.json')); e=d['MSTDBTP']['evaluated']; print('FLiBe' if 'BeF2-LiF' in e else 'Not in DB'); [print(f'  {k}: {len(v)} compositions') for k,v in e.items() if 'BeF2' in k or 'LiF' in k]"`,
    ``,
    `**Step 2: Search the web for external research.**`,
    `Call web_search with a specific, scientific query. Examples:`,
    `  "FLiBe molten salt viscosity research groups 2024"`,
    `  "molten salt reactor tritium breeding ratio improvement methods"`,
    `  "low viscosity fluoride salt candidates nuclear applications"`,
    ``,
    `Good queries are specific — include the salt name, property, and context (e.g., "nuclear", "MSR", "coolant").`,
    `Bad queries are vague — avoid just "molten salt research" or "best salt".`,
    ``,
    `**Step 3: Synthesize both sources in your answer.**`,
    `Combine what the database shows (compositions, property values, references) with what web search found (research groups, recent papers, trends).`,
    `Always cite sources: DOIs from the database and URLs from web search.`,
    ``,
    `#### Type 2 examples:`,
    ``,
    `User: "Has any group studied FLiBe?"`,
    `→ Step 1: run_bash — check if BeF2-LiF (FLiBe) exists in the database, list its properties and references`,
    `→ Step 2: web_search — "FLiBe BeF2-LiF molten salt research groups studies"`,
    `→ Step 3: Answer combining DB data (X compositions, Y properties measured, refs) with web results (groups at ORNL, MIT, etc.)`,
    ``,
    `User: "What is the most promising salt for better viscosity?"`,
    `→ Step 1: run_bash — Python script to compute viscosity at a reference temperature and rank salts:`,
    `  python3 -c "`,
    `  import json, math`,
    `  d=json.load(open('/mnt/skills/salt-analysis/assets/Molten_Salt_Thermophysical_Properties.json'))`,
    `  e=d['MSTDBTP']['evaluated']`,
    `  T=973.0  # reference temperature in K`,
    `  results=[]`,
    `  for salt,comps in e.items():`,
    `    for comp,props in comps.items():`,
    `      v=props.get('viscosity')`,
    `      if isinstance(v,dict) and isinstance(v.get('values'),list) and len(v['values'])>=2:`,
    `        A,B=float(v['values'][0]),float(v['values'][1])`,
    `        r=v.get('range',[0,9999])`,
    `        if isinstance(r,list) and len(r)==2 and r[0]<=T<=r[1]:`,
    `          try: mu=A*math.exp(B/T)`,
    `          except: continue`,
    `          if 0<mu<10: results.append((salt,comp,mu,v.get('DOI'),v.get('reference')))`,
    `  results.sort(key=lambda x:x[2])`,
    `  print(f'Salts ranked by viscosity at {T}K ({len(results)} entries):')`,
    `  for i,(s,c,mu,doi,ref) in enumerate(results[:15],1): print(f'{i}. {s} ({c}): {mu:.4f} Pa·s  DOI:{doi}')`,
    `  "`,
    `→ Step 2: web_search — "low viscosity molten salt candidates nuclear reactor coolant"`,
    `→ Step 3: Answer with DB rankings + literature perspective on promising candidates`,
    ``,
    `User: "How to improve the yield of tritium?"`,
    `→ Step 1: (skip — this is a general nuclear engineering question, not a database query)`,
    `→ Step 2: web_search — "tritium breeding ratio improvement molten salt reactor lithium enrichment"`,
    `→ Step 3: Answer with web search findings about breeding blanket design, Li-6 enrichment, etc.`,
    ``,
    `## Rules`,
    `- ALWAYS call run_bash — never answer data questions without running code first.`,
    `- For "show me the phase diagram" / "phase diagram" / "liquidus" / "eutectic" → use plot_phase_diagram.py.`,
    `- For "statistics" / "properties" / "references" / "data overview" → use analyze_salt.py.`,
    `- For follow-up requests (zoom, different property) → write custom Python/matplotlib via run_bash.`,
    `- If ambiguous ("show me salt X"), use plot_phase_diagram.py as the default for multi-component salts.`,
    `- ALWAYS set MPLBACKEND=Agg before any matplotlib usage.`,
    `- ALWAYS print "Plot saved to <path>" when saving plots so the UI displays them.`,
    `- For Type 2 questions: ALWAYS call at least one tool (run_bash and/or web_search). Never answer a research trend question from memory alone.`,
    `- Include references/DOIs from the database and URLs from web search.`,
    `- Be concise but thorough.`,
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

      if (command.includes("plot_phase_diagram.py")) {
        const saltMatch = command.match(/--salt\s+(\S+)/);
        log("INFO", "Tool:run_bash", `Calling plot_phase_diagram.py skill script`, {
          salt: saltMatch?.[1] || "unknown",
        });
      } else if (command.includes("analyze_salt.py")) {
        const saltMatch = command.match(/--salt\s+(\S+)/);
        log("INFO", "Tool:run_bash", `Calling analyze_salt.py skill script`, {
          salt: saltMatch?.[1] || "unknown",
        });
      } else if (command.includes("python")) {
        log("INFO", "Tool:run_bash", `Running custom Python script`, {
          commandPreview: command.slice(0, 200),
        });
      } else {
        log("INFO", "Tool:run_bash", `Running bash command`, {
          commandPreview: command.slice(0, 200),
        });
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

      // Extract plot path — match only real file paths (starting with /)
      const plotMatch = result.stdout.match(/Plot saved to\s+(\/\S+\.(?:png|jpg|jpeg|svg|gif))/);
      if (plotMatch) {
        result.plotPath = plotMatch[1].trim();
        log("INFO", "Tool:run_bash", `Plot detected, reading image`, {
          plotPath: result.plotPath,
        });
        const html = readImageAsBase64Html(result.plotPath);
        if (html) {
          result.displayHtml = html;
          log(
            "INFO",
            "Tool:run_bash",
            `Image embedded as base64 HTML (${html.length} chars)`
          );
        } else {
          log("WARN", "Tool:run_bash", `Failed to read plot image from disk`, {
            plotPath: result.plotPath,
          });
        }
      }
    } else if (toolName === "display_file") {
      const uri = String(toolInput.uri || "");
      const filePath = uri.startsWith("file://")
        ? uri.slice("file://".length)
        : uri;
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
      log("INFO", "Tool:web_search", `Executing web search`, { query });

      const searchResult = await executeWebSearch(query);

      const elapsed = Date.now() - t0;
      result.stdout = searchResult;
      result.ok = true;
      log("INFO", "Tool:web_search", `Completed in ${elapsed}ms`, {
        resultLength: searchResult.length,
        resultPreview: searchResult.slice(0, 300),
      });
    } else {
      log("WARN", "Tool:Unknown", `Unknown tool called: ${toolName}`);
      result.stderr = `Unknown tool: ${toolName}`;
    }
  } catch (err) {
    const elapsed = Date.now() - t0;
    result.stderr =
      err instanceof Error ? err.message : "Unknown tool execution error";
    log("ERROR", `Tool:${toolName}`, `Tool failed after ${elapsed}ms: ${result.stderr}`);
  }

  return result;
}

function extractMcpContent(
  json: unknown
): { text: string; html: string | null } {
  let text = "";
  let html: string | null = null;

  if (!json || typeof json !== "object")
    return { text: String(json || ""), html };

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
      const mimeType =
        typeof resource?.mimeType === "string" ? resource.mimeType : "";
      if (
        mimeType.includes("text/html") &&
        typeof resource?.text === "string"
      ) {
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

  const hasAzure =
    !!process.env.AZURE_OPENAI_ENDPOINT && !!process.env.AZURE_OPENAI_API_KEY;
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

  const maxIterations = 10;
  for (let i = 0; i < maxIterations; i++) {
    const iterStart = Date.now();
    log(
      "INFO",
      "Agent:LLM",
      `Iteration ${i + 1}/${maxIterations} — sending request to LLM`
    );

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
      const msg =
        fetchErr instanceof Error ? fetchErr.message : "Unknown error";
      log("ERROR", "Agent:LLM", `Failed to connect: ${msg}`);
      throw new Error(
        `Failed to connect to LLM endpoint (${azureConfig.url}): ${msg}`
      );
    }

    const responseText = await resp.text();
    const llmElapsed = Date.now() - iterStart;

    if (!resp.ok) {
      log("ERROR", "Agent:LLM", `HTTP ${resp.status} after ${llmElapsed}ms`, {
        responsePreview: responseText.slice(0, 300),
      });
      throw new Error(
        `Azure OpenAI error (${resp.status}): ${responseText.slice(0, 500)}`
      );
    }

    let data: Record<string, unknown>;
    try {
      data = JSON.parse(responseText);
    } catch {
      log("ERROR", "Agent:LLM", `Non-JSON response after ${llmElapsed}ms`);
      throw new Error(
        `LLM returned non-JSON (${resp.status}). First 200 chars: ${responseText.slice(0, 200)}`
      );
    }

    const choices = data.choices as
      | Array<Record<string, unknown>>
      | undefined;
    const choice = choices?.[0];
    const assistantMsg = choice?.message as
      | Record<string, unknown>
      | undefined;

    if (!assistantMsg) {
      log("WARN", "Agent:LLM", `No message in response after ${llmElapsed}ms`);
      return { response: "No response from LLM.", toolCalls };
    }

    messages.push(assistantMsg);

    const finishReason = choice?.finish_reason as string | undefined;
    const msgToolCalls = assistantMsg.tool_calls as
      | Array<Record<string, unknown>>
      | undefined;
    const textContent =
      typeof assistantMsg.content === "string" ? assistantMsg.content : "";

    log("INFO", "Agent:LLM", `Response received in ${llmElapsed}ms`, {
      finishReason,
      toolCallCount: msgToolCalls?.length || 0,
      textLength: textContent.length,
      textPreview: textContent.slice(0, 200),
    });

    if (finishReason !== "tool_calls" || !msgToolCalls?.length) {
      log(
        "INFO",
        "Agent",
        `Completed after ${i + 1} iteration(s), ${toolCalls.length} tool call(s)`
      );
      return { response: textContent, toolCalls };
    }

    for (const tc of msgToolCalls) {
      const fnObj = tc.function as Record<string, unknown> | undefined;
      const toolName = typeof fnObj?.name === "string" ? fnObj.name : "";
      let toolInput: Record<string, unknown> = {};
      try {
        toolInput = JSON.parse(
          typeof fnObj?.arguments === "string" ? fnObj.arguments : "{}"
        );
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
      {
        ok: false,
        response: "",
        error: "Invalid JSON body.",
      } satisfies ChatResponse,
      { status: 400 }
    );
  }

  const message =
    typeof body.message === "string" ? body.message.trim() : "";
  if (!message) {
    return NextResponse.json(
      {
        ok: false,
        response: "",
        error: "Message is required.",
      } satisfies ChatResponse,
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
