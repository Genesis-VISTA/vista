import { NextResponse } from "next/server";
import { getMcpClient } from "@/lib/mcp-client";
import { ElicitRequestSchema, type ElicitRequestFormParams, type ElicitResult } from "@modelcontextprotocol/sdk/types.js";
import { registerElicitation } from "@/lib/elicitation-bridge";
import { toPrompt, findSkills, readProperties } from "@/lib/skills";
import { config } from "@/app/config";
import { readFileSync } from "fs";
import { join } from "path";

const DEFAULT_TAB = "molten-salt";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

type ChatRequest = {
  message: string;
  history?: Array<{ role: string; content: string }>;
  tab?: string;
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

/** OpenAI function-calling tool format */
type OpenAiTool = {
  type: "function";
  function: {
    name: string;
    description: string;
    parameters: Record<string, unknown>;
  };
};

/** SSE event types sent to the browser */
type SseEvent =
  | { type: "elicitation"; id: string; message: string; schema: Record<string, unknown> }
  | { type: "agent_response"; response: string; toolCalls: ToolCallResult[] }
  // Intermediate updates streamed during a multi-iteration agent loop.
  // `text` is the LLM's message-level content for an iteration that is NOT
  // the terminal iteration (the terminal one arrives via `agent_response`).
  // `toolCall` carries a just-executed tool result that produced a figure —
  // the UI uses it to refresh the output panel before the loop finishes.
  | { type: "agent_turn"; text?: string; toolCall?: ToolCallResult }
  | { type: "error"; error: string }
  | { type: "log"; level: string; area: string; message: string; extra?: Record<string, unknown> }
  | { type: "done" };

/* ------------------------------------------------------------------ */
/*  Logging                                                            */
/* ------------------------------------------------------------------ */

/** Active SSE sender — set during runAgentLoop so log() can stream to browser */
let activeSendEvent: ((event: SseEvent) => void) | null = null;

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

  // Stream to browser if an SSE connection is active
  if (activeSendEvent) {
    try {
      activeSendEvent({ type: "log", level, area, message, extra });
    } catch {
      // ignore SSE write failures
    }
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

let cachedTools: OpenAiTool[] | null = null;
let cachedToolsTime = 0;
const TOOL_CACHE_TTL_MS = 60_000; // re-discover every 60s

/**
 * Discover tools from the MCP server then convert them to OpenAI function-calling format.
 */
async function discoverTools(): Promise<OpenAiTool[]> {
  const now = Date.now();
  if (cachedTools && now - cachedToolsTime < TOOL_CACHE_TTL_MS) {
    return cachedTools;
  }

  log("INFO", "ToolDiscovery", "Fetching tools from MCP server");

  try {
    const client = await getMcpClient();
    const { tools: rawTools } = await client.listTools(undefined, {
      signal: AbortSignal.timeout(5000),
    });

    log("INFO", "ToolDiscovery", `Discovered ${rawTools.length} MCP tools`, {
      names: rawTools.map((t) => t.name),
    });

    const openaiTools: OpenAiTool[] = rawTools.map((mcp) => ({
      type: "function" as const,
      function: {
        name: mcp.name,
        description: mcp.description ?? "",
        parameters: (mcp.inputSchema ?? { type: "object", properties: {} }) as Record<string, unknown>,
      },
    }));

    cachedTools = openaiTools;
    cachedToolsTime = now;
    return cachedTools;
  } catch (err) {
    log("ERROR", "ToolDiscovery", `tools/list failed: ${err instanceof Error ? err.message : "Unknown"}`);
    return cachedTools ?? [];
  }
}

/**
 * Filter the discovered tool list down to what is appropriate for the active tab.
 *
 * - molten-salt: exclude every `agenthpc_*` tool (they belong to the Alloy Design tab).
 * - alloy-design: exclude `submit_hpc_job` / `get_hpc_job_status` / `list_hpc_jobs`
 *   (those target Frontier for the molten-salt workflow; MoNbTaW uses `agenthpc_*`
 *   on Andes instead). Keep sandbox (`run_bash`/`create_file`/`view`) and
 *   `display_file` available for post-hoc analysis.
 */
/**
 * Per-tab cap on how many LLM rounds we'll run in a single chat turn.
 *
 * - molten-salt: 10 is plenty for the existing query-and-plot workflows.
 * - alloy-design: each optimization trial uses ~5 tool-call rounds
 *   (get_all_results → submit → wait → get_job_result → optional
 *   display), so we budget 600 by default to comfortably cover ~100 trials.
 *   Override with VISTA_MAX_AGENT_ITERATIONS_ALLOY if you need to run longer
 *   optimizations (e.g. 1200 for 200 trials).
 */
function maxIterationsForTab(tab: string): number {
  if (tab === "alloy-design") {
    const envOverride = parseInt(process.env.VISTA_MAX_AGENT_ITERATIONS_ALLOY ?? "", 10);
    return Number.isFinite(envOverride) && envOverride > 0 ? envOverride : 600;
  }
  return 10;
}

/**
 * Client-side timeout for a single MCP tool invocation.
 *
 * Default: 60s (or 5min when an elicitation is possible).
 * Overrides:
 * - agenthpc_wait_for_job blocks on the server up to `timeout_s` (default
 *   1800s) polling squeue — the client must outlast that with a small buffer
 *   or it will trip a harmless but misleading "-32001 Request timed out".
 * - agenthpc_submit_parameter_set runs remote `mkdir`/`cp`/`sbatch` over SSH
 *   (which on first call also elicits credentials) — 10 min covers normal
 *   latency plus user reaction time on the credential prompt.
 * - agenthpc_plot_progress renders matplotlib locally — rare to hit 60s but
 *   occasional cold imports can come close, so give it 180s headroom.
 */
function toolTimeoutMs(
  toolName: string,
  toolInput: Record<string, unknown>,
  onElicitation: boolean,
): number {
  if (toolName === "agenthpc_wait_for_job") {
    const raw = Number(toolInput.timeout_s);
    const serverTimeoutS = Number.isFinite(raw) && raw > 0 ? raw : 1800;
    return (serverTimeoutS + 60) * 1000;
  }
  if (toolName === "agenthpc_submit_parameter_set") {
    return 600_000;
  }
  if (toolName === "agenthpc_plot_progress") {
    return 180_000;
  }
  return onElicitation ? 300_000 : 60_000;
}

function filterToolsForTab(tools: OpenAiTool[], tab: string): OpenAiTool[] {
  const saltFrontierTools = new Set([
    "submit_hpc_job",
    "get_hpc_job_status",
    "list_hpc_jobs",
  ]);

  return tools.filter((t) => {
    const name = t.function.name;
    if (tab === "alloy-design") {
      return !saltFrontierTools.has(name);
    }
    // molten-salt (default)
    return !name.startsWith("agenthpc_");
  });
}

/* ------------------------------------------------------------------ */
/*  System prompt                                                      */
/* ------------------------------------------------------------------ */

/**
 * Return the subset of SKILL.md directories whose `metadata.tab` matches `tab`
 * (skills without a tab field default to the molten-salt tab).
 */
function skillDirsForTab(tab: string): string[] {
  return findSkills([config.skillsDir]).filter((dir) => {
    try {
      const props = readProperties(dir);
      const skillTab = props.metadata?.tab ?? DEFAULT_TAB;
      return skillTab === tab;
    } catch {
      return false;
    }
  });
}

function buildSystemPrompt(tab: string): string {
  if (tab === "alloy-design") {
    return buildAlloyDesignPrompt();
  }
  return buildMoltenSaltPrompt();
}

function buildAlloyDesignPrompt(): string {
  const skillsPrompt = toPrompt(skillDirsForTab("alloy-design"));
  return [
    `You are VISTA, operating in **High Entropy Alloy Design** mode.`,
    `Your job is to run an agentic optimization loop on the Andes HPC cluster to find refractory high-entropy alloy compositions that meet the user's targeted critical transition temperature (Tc). Currently supports MoNbTaW (4-element).`,
    ``,
    skillsPrompt,
    ``,
    `## Before you start — gather inputs`,
    `On the FIRST user message of an optimization request, ask ONE short question to collect:`,
    `  1) target_score: targeted Tc in K (stopping criterion; e.g. 1250)`,
    `  2) max_trials: maximum number of HPC jobs to submit (e.g. 50)`,
    `  3) any composition constraints the user wants (optional, e.g. "keep Mo ≥ 0.2")`,
    `If the user omits target_score or max_trials, proceed without them and the YAML defaults will be used — but ALWAYS ask for both on the first turn.`,
    ``,
    `## Critical workflow rules`,
    `- Follow the optimization loop in the \`alloy-design\` SKILL.md exactly.`,
    `- Pass the user's \`target_score\` and \`max_trials\` to EVERY tool call that accepts them — \`agenthpc_get_search_space\`, \`agenthpc_get_all_results\`, and \`agenthpc_get_job_result\`. The server is stateless.`,
    `- Use ONLY the \`agenthpc_*\` tools. Do not call \`run_bash\`, \`submit_hpc_job\`, \`get_hpc_job_status\`, or \`list_hpc_jobs\` for MoNbTaW submissions.`,
    `- The first \`agenthpc_submit_parameter_set\` call elicits Andes SSH credentials; subsequent calls reuse the cached connection.`,
    `- After every successful \`agenthpc_get_job_result\`, do TWO things in order: (1) call \`agenthpc_plot_progress("monbtaw")\` to refresh the cumulative specific-heat curves in the output panel, (2) write a structured **Trial Report** in chat following the exact format in the alloy-design SKILL.md (Ran / Why this point / Trajectory table / Best so far / Next proposed + Reason). Never skip either step — the user is relying on the chat report and the figure together to track the campaign.`,
    `- Stop when \`agenthpc_get_all_results\` returns \`should_stop: true\` (i.e. threshold_reached OR budget_exhausted). Then summarize best composition, best score vs target, trial count, and the search trajectory.`,
    `- When the user asks for a single trial, skip the loop and just submit once.`,
    `- If the user asks to stop / cancel / abort / kill the optimization, follow the "Cancellation" section of the SKILL.md: \`agenthpc_list_pending_jobs\` → \`agenthpc_cancel_all_pending\` → one final \`agenthpc_get_all_results\` summary, and do NOT submit any further jobs.`,
    ``,
    `## Search strategy guidance`,
    `- **sum = 1.0 is non-negotiable.** The four numbers are atom fractions. Before you call \`agenthpc_submit_parameter_set\`, add Mo + Nb + Ta + W explicitly and confirm the total equals 1.0 (tolerance 1e-3). If your draft sums to e.g. 0.95, rescale: divide each value by the sum and round to two decimals, then nudge one coordinate to absorb rounding error so the total is exactly 1.00. The server will reject malformed sums, but every rejected submission wastes a round-trip.`,
    `- Early trials (first ~5): spread across the space — include the equiatomic point (0.25, 0.25, 0.25, 0.25) and a few corner-biased compositions.`,
    `- Later trials: exploit near \`best_parameters\` returned by \`agenthpc_get_all_results\`, perturbing one or two elements at a time while preserving sum=1.0.`,
    `- Never resubmit a composition that already appears in the trials list — check \`agenthpc_get_all_results\` at the top of every iteration.`,
  ].join("\n");
}

function buildMoltenSaltPrompt(): string {
  const skillsPrompt = toPrompt(skillDirsForTab("molten-salt"));

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
    `You have access to a molten salt database and analysis scripts via run_bash, and a literature search tool (rag_search) over indexed research papers.`,
    `For questions beyond the database and literature corpus (general nuclear science, broad research trends), answer using your own scientific knowledge.`,
    ``,
    skillsPrompt,
    ``,
    `## HPC Job Submission (Frontier)`,
    `To run jobs on the Frontier HPC cluster, use these tools directly — do NOT use run_bash:`,
    `- submit_hpc_job(job, nodes?, time_limit?, script_args?): Submit a Slurm job. Available jobs: example, forge-tune.`,
    `- get_hpc_job_status(job_id): Check the status and logs of a submitted job.`,
    `- list_hpc_jobs(): List all recently submitted jobs.`,
    `Use submit_hpc_job whenever the user asks to run, launch, or execute anything on Frontier or the HPC cluster.`,
    `For model fine-tuning requests, do this iteratively: first ask exactly one gating question: "Do you want me to submit this as a Frontier job now?"`,
    `In that first fine-tuning response, do NOT ask for model path, hyperparameters, dataset path, checkpoint path, or any other setup details.`,
    `Wait for the user's yes/no answer before asking any additional fine-tuning questions.`,
    `Only call submit_hpc_job for fine-tuning after the user explicitly says yes.`,
    ``,
    `## Literature Search (RAG)`,
    `You have access to a rag_search tool that searches over an indexed corpus of molten salt research papers, reports, and technical notes.`,
    ``,
    `**When to use rag_search:**`,
    `- Qualitative or conceptual questions: "What corrosion challenges exist for FLiBe?", "How is thermal conductivity typically measured?"`,
    `- Literature review questions: "What do recent studies say about tritium management in FHRs?"`,
    `- Finding references, experimental methods, or discussion of specific phenomena from the literature`,
    `- When the user asks about research context, background, or the state of knowledge on a topic`,
    ``,
    `**When NOT to use rag_search (use run_bash instead):**`,
    `- Quantitative lookups: melting points, viscosity values, density at a specific temperature`,
    `- Database statistics: counting salts, finding extremes, ranking properties`,
    `- Phase diagrams, plots, or any visualization`,
    ``,
    `**When to use BOTH rag_search and run_bash:**`,
    `- "What is known about FLiBe corrosion and what does our database say?" → rag_search for qualitative context, run_bash for database values`,
    `- Research trend questions that also reference specific salts or properties in the database`,
    ``,
    `When citing rag_search results, ALWAYS include the citation information returned by the tool (title, authors, year, DOI).`,
    `Format citations inline like: (Author et al., Year, DOI: ...) or as a references section at the end of your response.`,
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
    `**Step 2: Search the literature corpus with rag_search.**`,
    `Call rag_search to find relevant passages from indexed papers. This provides qualitative context, experimental details, and additional references beyond the structured database.`,
    ``,
    `**Step 3: Synthesize your answer.**`,
    `Combine the database results (Step 1), literature passages (Step 2), and your own scientific knowledge.`,
    `Cite DOIs from both the database and rag_search results.`,
    ``,
    `Examples:`,
    `User: "Has any group studied FLiBe?"`,
    `→ Step 1: run_bash — check if BeF2-LiF exists in the database, list its properties and references`,
    `→ Step 2: rag_search("FLiBe research experimental studies") — find literature discussing FLiBe`,
    `→ Step 3: Answer combining DB data, literature passages, and your knowledge of FLiBe research (ORNL, MSR program, etc.)`,
    ``,
    `User: "What corrosion challenges exist for fluoride salts in reactor piping?"`,
    `→ Step 1: skip (not a database lookup)`,
    `→ Step 2: rag_search("corrosion fluoride salt reactor piping") — find relevant literature passages`,
    `→ Step 3: Answer combining literature findings with your own knowledge, citing all sources`,
    ``,
    `User: "What is the most promising salt for better viscosity?"`,
    `→ Step 1: run_bash — compute viscosity at T=973K for all salts, rank by lowest`,
    `→ Step 2: rag_search("low viscosity molten salt candidates") — find literature on promising salts`,
    `→ Step 3: Answer with DB rankings + literature context + your knowledge of trade-offs`,
    ``,
    `User: "How to improve the yield of tritium?"`,
    `→ Step 1: skip (general nuclear engineering question, not a database query)`,
    `→ Step 2: rag_search("tritium breeding yield improvement") — check if literature corpus has relevant papers`,
    `→ Step 3: Answer combining any literature findings with your knowledge about breeding blanket design, Li-6 enrichment, etc.`,
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
    `- For literature/qualitative questions → use rag_search. Include full citations (title, authors, DOI) from the returned results.`,
    `- For complex questions, combine rag_search (for literature context) with run_bash (for quantitative data). Cite both the literature and database DOIs.`,
    `- For research/trend questions: query the database first if relevant, use rag_search for literature context, then synthesize with your own scientific knowledge.`,
    `- Include references/DOIs from the database and rag_search results when available.`,
    `- Be concise but thorough.`,
  ].join("\n");
}

/* ------------------------------------------------------------------ */
/*  Generic MCP tool execution                                         */
/* ------------------------------------------------------------------ */

/**
 * Extract text and HTML from an SDK CallToolResult.
 * The SDK returns `{ content: [...], isError? }` directly — no JSON-RPC
 * envelope to unwrap.
 */
function extractSdkContent(sdkResult: unknown): { text: string; html: string | null } {
  let text = "";
  let html: string | null = null;

  if (!sdkResult || typeof sdkResult !== "object") return { text: String(sdkResult || ""), html };

  const obj = sdkResult as Record<string, unknown>;
  const content = Array.isArray(obj.content) ? obj.content : [];

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

  return { text, html };
}

/**
 * Execute a tool call via the MCP SDK client.
 *
 * When an `onElicitation` callback is provided, registers it as the
 * client's elicitation request handler so mid-call credential prompts
 * are relayed to the browser.
 */
async function executeTool(
  toolName: string,
  toolInput: Record<string, unknown>,
  onElicitation?: (req: ElicitRequestFormParams) => Promise<ElicitResult>
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

  // Log skill-script detection for run_bash
  if (toolName === "bash") {
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
    const client = await getMcpClient();
    const timeoutMs = toolTimeoutMs(toolName, toolInput, !!onElicitation);

    // Register elicitation handler directly on the client for this call
    if (onElicitation) {
      client.setRequestHandler(ElicitRequestSchema, async (request) => {
        const params = request.params;
        if (!("requestedSchema" in params)) {
          return { action: "decline" as const };
        }
        return await onElicitation(params);
      });
    }

    const sdkResult = await client.callTool(
      { name: toolName, arguments: toolInput },
      undefined,
      { signal: AbortSignal.timeout(timeoutMs) }
    );

    const content = extractSdkContent(sdkResult);
    result.stdout = content.text;
    result.ok = !sdkResult.isError;

    if (toolName === "display_file") {
      const direct = content.html || content.text;
      if (direct && direct.includes("<img")) {
        result.displayHtml = direct;
      }
    }

    const elapsed = Date.now() - t0;
    log("INFO", `Tool:${toolName}`, `Completed in ${elapsed}ms`, {
      ok: result.ok,
      stdoutLength: result.stdout.length,
      stdoutPreview: result.stdout.slice(0, 300),
    });

    // Post-processing: detect saved plots and display via display_file MCP tool
    const plotMatch = result.stdout.match(/Plot saved to\s+(\/\S+\.(?:png|jpg|jpeg|svg|gif))/);
    if (plotMatch) {
      result.plotPath = plotMatch[1].trim();
      log("INFO", `Tool:${toolName}`, `Plot detected, calling display_file`, { plotPath: result.plotPath });

      let dfSuccess = false;
      try {
        log("INFO", `Tool:${toolName}`, `Calling display_file MCP tool now...`);
        const dfResult = await client.callTool(
          { name: "display_file", arguments: { uri: result.plotPath } },
          undefined,
          { signal: AbortSignal.timeout(30000) }
        );

        const dfContent = extractSdkContent(dfResult);
        const imgHtml = dfContent.html || dfContent.text;

        log("INFO", `Tool:${toolName}`, `display_file MCP response received`, {
          isError: !!dfResult.isError,
          htmlLen: dfContent.html?.length || 0,
          textLen: dfContent.text?.length || 0,
        });

        if (imgHtml && imgHtml.includes("<img")) {
          result.displayHtml = imgHtml;
          dfSuccess = true;
          log("INFO", `Tool:${toolName}`, `display_file SUCCESS — image HTML (${imgHtml.length} chars)`);
        } else {
          log("INFO", `Tool:${toolName}`, `display_file returned no <img> tag`, {
            textPreview: (dfContent.text || "").slice(0, 300),
          });
        }
      } catch (dfErr) {
        const errMsg = dfErr instanceof Error ? `${dfErr.name}: ${dfErr.message}` : String(dfErr);
        log("INFO", `Tool:${toolName}`, `display_file EXCEPTION: ${errMsg}`);
      }

      if (!dfSuccess) {
        log("INFO", `Tool:${toolName}`, `display_file did not produce HTML — plotPath set for frontend fallback.`);
      }
    }
  } catch (err) {
    const elapsed = Date.now() - t0;
    result.stderr = err instanceof Error ? err.message : "Unknown tool execution error";
    log("ERROR", `Tool:${toolName}`, `Failed after ${elapsed}ms: ${result.stderr}`);
  }

  return result;
}

/* ------------------------------------------------------------------ */
/*  Azure OpenAI agentic loop                                          */
/* ------------------------------------------------------------------ */

async function runAgentLoop(
  userMessage: string,
  history: Array<{ role: string; content: string }>,
  tab: string,
  sendEvent: (event: SseEvent) => void
): Promise<{ response: string; toolCalls: ToolCallResult[] }> {
  const azureConfig = getAzureConfig();

  const hasAzure = !!process.env.AZURE_OPENAI_ENDPOINT && !!process.env.AZURE_OPENAI_API_KEY;
  const hasOpenAI = !!process.env.OPENAI_API_KEY;

  // Wire up SSE log streaming for this request
  activeSendEvent = sendEvent;

  if (!hasAzure && !hasOpenAI) {
    log("ERROR", "Agent", "No API key configured");
    return {
      response:
        "No API key configured. Set AZURE_OPENAI_ENDPOINT + AZURE_OPENAI_API_KEY + AZURE_OPENAI_DEPLOYMENT_NAME, " +
        "or OPENAI_API_KEY + OPENAI_BASE_URL for a generic OpenAI-compatible endpoint.",
      toolCalls: [],
    };
  }

  // Discover tools from MCP server and filter for the active tab.
  const allTools = await discoverTools();
  const tools = filterToolsForTab(allTools, tab);
  log("INFO", "ToolDiscovery", `Exposing ${tools.length}/${allTools.length} tools to LLM for tab "${tab}"`, {
    names: tools.map((t) => t.function.name),
  });

  const systemPrompt = buildSystemPrompt(tab);
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

  // Build the onElicitation callback that bridges to the browser via SSE
  const onElicitation = async (req: ElicitRequestFormParams): Promise<ElicitResult> => {
    const bridgeId = `elicit-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    log("INFO", "Elicitation", `Received elicitation request`, { bridgeId, message: req.message });

    // Send elicitation event to browser via SSE
    sendEvent({
      type: "elicitation",
      id: bridgeId,
      message: req.message,
      schema: req.requestedSchema,
    });

    // Wait for browser to POST back via /api/chat/elicitation
    const resp = await registerElicitation(bridgeId);
    log("INFO", "Elicitation", `Got response from browser`, { bridgeId, action: resp.action });
    return resp;
  };

  const maxIterations = maxIterationsForTab(tab);
  log("INFO", "Agent", `Agent iteration budget for tab "${tab}": ${maxIterations}`);
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

    // Mid-loop iteration: the LLM wrote message content alongside its next
    // tool calls (e.g. a Trial Report for alloy-design). Stream it now —
    // waiting for `agent_response` would bury it until the whole campaign
    // ends, and only the final iteration's content survives in that path.
    if (textContent.trim()) {
      sendEvent({ type: "agent_turn", text: textContent });
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

      const tcResult = await executeTool(toolName, toolInput, onElicitation);
      toolCalls.push(tcResult);

      // If this tool produced a figure (displayHtml from display_file, or a
      // plotPath detected in stdout), stream it so the output panel updates
      // immediately rather than waiting for the end of the agent loop.
      if (tcResult.displayHtml || tcResult.plotPath) {
        sendEvent({ type: "agent_turn", toolCall: tcResult });
      }

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
  activeSendEvent = null;
  return { response: "Agent reached maximum iterations.", toolCalls };
}

/* ------------------------------------------------------------------ */
/*  POST handler — returns SSE stream                                  */
/* ------------------------------------------------------------------ */

export async function POST(request: Request) {
  const requestStart = Date.now();
  let body: ChatRequest;
  try {
    body = (await request.json()) as ChatRequest;
  } catch {
    return NextResponse.json(
      { ok: false, response: "", error: "Invalid JSON body." },
      { status: 400 }
    );
  }

  const message = typeof body.message === "string" ? body.message.trim() : "";
  if (!message) {
    return NextResponse.json(
      { ok: false, response: "", error: "Message is required." },
      { status: 400 }
    );
  }

  const history = Array.isArray(body.history) ? body.history : [];
  const tab = typeof body.tab === "string" && body.tab ? body.tab : DEFAULT_TAB;

  const encoder = new TextEncoder();
  const stream = new TransformStream();
  const writer = stream.writable.getWriter();

  const sendEvent = (event: SseEvent) => {
    const data = `data: ${JSON.stringify(event)}\n\n`;
    writer.write(encoder.encode(data)).catch(() => {});
  };

  // Run agent loop in background, writing SSE events as it goes
  (async () => {
    try {
      const { response, toolCalls } = await runAgentLoop(message, history, tab, sendEvent);
      const elapsed = Date.now() - requestStart;
      log("INFO", "POST", `Request completed in ${elapsed}ms`, {
        toolCallCount: toolCalls.length,
        responseLength: response.length,
      });
      sendEvent({ type: "agent_response", response, toolCalls });
    } catch (error) {
      const elapsed = Date.now() - requestStart;
      const errMsg = error instanceof Error ? error.message : "Unknown error";
      log("ERROR", "POST", `Request failed after ${elapsed}ms: ${errMsg}`);
      sendEvent({ type: "error", error: errMsg });
    } finally {
      activeSendEvent = null;
      sendEvent({ type: "done" });
      writer.close().catch(() => {});
    }
  })();

  return new Response(stream.readable, {
    headers: {
      "content-type": "text/event-stream",
      "cache-control": "no-cache",
      connection: "keep-alive",
    },
  });
}
