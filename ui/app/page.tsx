"use client";

import { useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import SandboxedHtmlCard from "@/components/SandboxedHtmlCard";
import ElicitationModal from "@/components/ElicitationModal";
import type { ChatMessage, ExecutionResult } from "@/lib/types";
import { useActiveProject } from "@/lib/projects";

type LogEntry = {
  id: string;
  ts: string;
  level: string;
  area: string;
  message: string;
  extra?: Record<string, unknown>;
};

type McpHealth = {
  ok: boolean;
  mcpBaseUrl: string;
  detail?: string;
};

type McpToolsResponse = {
  ok: boolean;
  tools: Array<{ name: string; description?: string; inputSchema?: any }>;
  error?: string;
};

type ToolCallInfo = {
  tool: string;
  args: Record<string, unknown>;
  stdout: string;
  stderr: string;
  ok: boolean;
  plotPath?: string | null;
  displayHtml?: string | null;
};

type ChatApiResponse = {
  ok: boolean;
  response: string;
  tools?: Array<{ name: string; description?: string; inputSchema?: any }>;
  toolCalls?: ToolCallInfo[];
  error?: string;
};

const MODEL_SERVICES = ["AmSC model services"];
const MODEL_FAMILIES = ["gpt-5", "claude", "open models"];
const OPEN_MODELS = ["open-ai/gpt-oss-20b"];

/** localStorage key for the user's per-browser loaded-skill set. */
const LOADED_SKILLS_STORAGE_KEY = "vista.loadedSkills.v1";

function formatResultSummary(result: ExecutionResult): string {
  const status = result.ok ? "OK" : "ERROR";
  const output = result.stdout ? result.stdout.slice(0, 240) : "";
  return `${status}${output ? `: ${output}` : ""}`;
}

function extractPlotPath(stdout: string): string | null {
  const match = stdout.match(/Plot saved to\s+(.+)/);
  if (!match) return null;
  return match[1].trim();
}

function parseReferencesFromStdout(stdout: string): string[] {
  if (!stdout) return [];
  const refs: string[] = [];
  const lines = stdout.split(/\r?\n/);
  let inReferencesSection = false;

  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed) continue;

    if (/^references\b/i.test(trimmed)) {
      inReferencesSection = true;
      continue;
    }

    if (inReferencesSection) {
      if (/^=+$/.test(trimmed)) continue;
      const cleaned = trimmed
        .replace(/^\[\d+\]\s*/, "")
        .replace(/^[-*]\s*/, "")
        .trim();
      if (cleaned) refs.push(cleaned);
      continue;
    }

    const inlineRef = trimmed.match(/\b(10\.\d{4,9}\/\S+|https?:\/\/\S+)/i);
    if (inlineRef?.[1]) refs.push(inlineRef[1]);
  }

  return refs;
}

function extractReferences(result: ExecutionResult | null): string[] {
  if (!result) return [];
  const candidates: string[] = [];

  if (Array.isArray(result.meta?.references)) {
    for (const item of result.meta.references) {
      if (typeof item === "string" && item.trim()) candidates.push(item.trim());
    }
  }

  const dataRefs = (result.data as Record<string, unknown> | undefined)?.references;
  if (Array.isArray(dataRefs)) {
    for (const item of dataRefs) {
      if (typeof item === "string" && item.trim()) candidates.push(item.trim());
    }
  }

  candidates.push(...parseReferencesFromStdout(result.stdout || ""));
  return Array.from(new Set(candidates));
}

function extractPredictionSummary(result: ExecutionResult | null): Record<string, unknown> | null {
  if (!result?.stdout) return null;

  const summaryLine = result.stdout
    .split(/\r?\n/)
    .find((line) => line.trim().startsWith("SUMMARY_JSON:"));
  if (!summaryLine) return null;

  const jsonText = summaryLine.replace(/^.*SUMMARY_JSON:\s*/, "").trim();
  if (!jsonText) return null;

  try {
    const parsed = JSON.parse(jsonText);
    return parsed && typeof parsed === "object" ? (parsed as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

/**
 * One-line preview of an intermediate agent report, used when the bubble is
 * collapsed. Pulls the first markdown heading/meaningful line and strips
 * bullet/heading punctuation so it reads like a chip label.
 */
function intermediatePreview(content: string): string {
  const firstLine = content
    .split(/\n+/)
    .map((line) => line.trim())
    .find((line) => line && !line.startsWith("```"));
  if (!firstLine) return "Agent update";
  const cleaned = firstLine
    .replace(/^#+\s*/, "")
    .replace(/^[-*>]\s*/, "")
    .replace(/^\*+|\*+$/g, "")
    .trim();
  return cleaned || "Agent update";
}

export default function HomePage() {
  const mainRef = useRef<HTMLElement | null>(null);
  const outputSplitRef = useRef<HTMLDivElement | null>(null);
  const chatListRef = useRef<HTMLDivElement | null>(null);
  const modelMenuRef = useRef<HTMLDivElement | null>(null);
  const [activeColumnResizer, setActiveColumnResizer] = useState<"right" | null>(null);
  const [activeRowResizer, setActiveRowResizer] = useState<"right" | null>(null);
  const [vizWidth, setVizWidth] = useState(460);
  const [rightTopHeight, setRightTopHeight] = useState(430);
  /**
   * Slugs of skills the user has loaded for the current chat session via the
   * Skill Hub. Persisted across reloads in localStorage and shared with the
   * /skill-hub page via the same storage key. The lazy initializer reads the
   * stored value at mount — using a `useEffect` here is unsafe because the
   * persist effect would race with the hydrate effect and overwrite the hub's
   * writes with an empty Set on every navigation back to this page.
   */
  const [loadedSlugs, setLoadedSlugs] = useState<Set<string>>(() => {
    if (typeof window === "undefined") return new Set();
    try {
      const raw = window.localStorage.getItem(LOADED_SKILLS_STORAGE_KEY);
      if (!raw) return new Set();
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed)) {
        return new Set(parsed.filter((s): s is string => typeof s === "string"));
      }
    } catch {
      // ignore corrupt entries
    }
    return new Set();
  });

  // Persist loadedSlugs back to localStorage on every change.
  useEffect(() => {
    try {
      window.localStorage.setItem(
        LOADED_SKILLS_STORAGE_KEY,
        JSON.stringify(Array.from(loadedSlugs))
      );
    } catch {
      // localStorage may be unavailable (private mode, quota); chat still works.
    }
  }, [loadedSlugs]);

  // Sync from other tabs / the Skill Hub when it writes the same key, and
  // re-read on window focus so navigation from /skill-hub back here picks up
  // changes even if Next.js's router cache kept this page mounted.
  useEffect(() => {
    function reread() {
      try {
        const raw = window.localStorage.getItem(LOADED_SKILLS_STORAGE_KEY);
        if (!raw) return;
        const parsed = JSON.parse(raw);
        if (!Array.isArray(parsed)) return;
        const next = new Set(parsed.filter((s): s is string => typeof s === "string"));
        setLoadedSlugs((prev) => {
          if (prev.size === next.size && Array.from(prev).every((s) => next.has(s))) {
            return prev;
          }
          return next;
        });
      } catch {
        // ignore
      }
    }
    function onStorage(event: StorageEvent) {
      if (event.key === LOADED_SKILLS_STORAGE_KEY) reread();
    }
    window.addEventListener("storage", onStorage);
    window.addEventListener("focus", reread);
    return () => {
      window.removeEventListener("storage", onStorage);
      window.removeEventListener("focus", reread);
    };
  }, []);

  /**
   * Active project for the topbar badge. The hook uses
   * `useSyncExternalStore` so SSR and the first client paint both read
   * `null`, then React updates with the real value after hydration. Other
   * tabs / pages that write the active-project slug are picked up via the
   * hook's storage subscription.
   */
  const activeProject = useActiveProject();

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  /**
   * Id of the most recently streamed intermediate agent turn.
   * Rendered expanded while it is the latest; demoted (collapsed) when a
   * newer intermediate arrives or when the final agent_response lands.
   */
  const [latestIntermediateId, setLatestIntermediateId] = useState<string | null>(null);
  /**
   * Ids of older intermediate messages the user has manually expanded.
   * Overrides the collapsed-by-default state for non-latest intermediates.
   */
  const [expandedIntermediates, setExpandedIntermediates] = useState<Set<string>>(new Set());

  function toggleIntermediate(id: string) {
    setExpandedIntermediates((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  const [showAnalyzeModal, setShowAnalyzeModal] = useState(false);
  const [saltInput, setSaltInput] = useState("AlCl3-KCl");
  const [showPredictModal, setShowPredictModal] = useState(false);
  const [predictFormulaInput, setPredictFormulaInput] = useState("NaCl");
  const [predictCompInput, setPredictCompInput] = useState("Pure Salt");

  const [latestResult, setLatestResult] = useState<ExecutionResult | null>(null);
  const [isCalling, setIsCalling] = useState(false);
  const [mcpHealth, setMcpHealth] = useState<McpHealth | null>(null);
  const [isCheckingHealth, setIsCheckingHealth] = useState(false);
  const [mcpTools, setMcpTools] = useState<McpToolsResponse | null>(null);
  const [isLoadingTools, setIsLoadingTools] = useState(false);
  const [useLlm, setUseLlm] = useState(true);
  const [isChatLoading, setIsChatLoading] = useState(false);
  const [pendingElicitation, setPendingElicitation] = useState<{
    id: string;
    message: string;
    schema: Record<string, unknown>;
  } | null>(null);
  const [chatService] = useState(MODEL_SERVICES[0]);
  const [chatFamily, setChatFamily] = useState(MODEL_FAMILIES[0]);
  const [chatOpenModel, setChatOpenModel] = useState(OPEN_MODELS[0]);
  const [isModelMenuOpen, setIsModelMenuOpen] = useState(false);
  const [isServiceExpanded, setIsServiceExpanded] = useState(false);
  const [isOpenModelsExpanded, setIsOpenModelsExpanded] = useState(false);
  const [showJumpToLatest, setShowJumpToLatest] = useState(false);
  const [agentLogs, setAgentLogs] = useState<LogEntry[]>([]);
  const logEndRef = useRef<HTMLDivElement | null>(null);
  const latestReferences = useMemo(() => extractReferences(latestResult), [latestResult]);
  const latestPredictionSummary = useMemo(() => extractPredictionSummary(latestResult), [latestResult]);

  function scrollChatToLatest(behavior: ScrollBehavior = "smooth") {
    const node = chatListRef.current;
    if (!node) return;
    node.scrollTo({ top: node.scrollHeight, behavior });
  }

  function handleChatScroll() {
    const node = chatListRef.current;
    if (!node) return;
    const distanceFromBottom = node.scrollHeight - node.scrollTop - node.clientHeight;
    setShowJumpToLatest(distanceFromBottom > 96);
  }

  useEffect(() => {
    if (activeColumnResizer !== "right") return;

    const minViz = 320;
    const minConsole = 420;
    const splitterTotal = 10;

    const onPointerMove = (event: PointerEvent) => {
      const container = mainRef.current;
      if (!container || window.innerWidth <= 1100) return;

      const rect = container.getBoundingClientRect();
      const maxViz = rect.width - minConsole - splitterTotal;
      const raw = rect.right - event.clientX;
      const next = Math.max(minViz, Math.min(raw, maxViz));
      setVizWidth(next);
    };

    const onPointerUp = () => {
      setActiveColumnResizer(null);
    };

    document.body.style.userSelect = "none";
    document.body.style.cursor = "col-resize";
    window.addEventListener("pointermove", onPointerMove);
    window.addEventListener("pointerup", onPointerUp);

    return () => {
      document.body.style.userSelect = "";
      document.body.style.cursor = "";
      window.removeEventListener("pointermove", onPointerMove);
      window.removeEventListener("pointerup", onPointerUp);
    };
  }, [activeColumnResizer, vizWidth]);

  useEffect(() => {
    if (activeRowResizer !== "right") return;

    const splitterSize = 10;
    const minTop = 170;
    const minBottom = 180;

    const onPointerMove = (event: PointerEvent) => {
      if (window.innerWidth <= 1100) return;

      const container = outputSplitRef.current;
      if (!container) return;

      const rect = container.getBoundingClientRect();
      const raw = event.clientY - rect.top;
      const maxTop = rect.height - minBottom - splitterSize;
      const next = Math.max(minTop, Math.min(raw, maxTop));
      setRightTopHeight(next);
    };

    const onPointerUp = () => {
      setActiveRowResizer(null);
    };

    document.body.style.userSelect = "none";
    document.body.style.cursor = "row-resize";
    window.addEventListener("pointermove", onPointerMove);
    window.addEventListener("pointerup", onPointerUp);

    return () => {
      document.body.style.userSelect = "";
      document.body.style.cursor = "";
      window.removeEventListener("pointermove", onPointerMove);
      window.removeEventListener("pointerup", onPointerUp);
    };
  }, [activeRowResizer]);

  useEffect(() => {
    if (!isModelMenuOpen) return;
    const onPointerDown = (event: PointerEvent) => {
      const node = modelMenuRef.current;
      if (!node) return;
      if (!node.contains(event.target as Node)) {
        setIsModelMenuOpen(false);
      }
    };
    window.addEventListener("pointerdown", onPointerDown);
    return () => window.removeEventListener("pointerdown", onPointerDown);
  }, [isModelMenuOpen]);

  async function handleElicitationSubmit(
    id: string,
    action: "accept" | "decline" | "cancel",
    content?: Record<string, unknown>
  ) {
    setPendingElicitation(null);
    try {
      await fetch("/api/chat/elicitation", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ id, action, content })
      });
    } catch {
      // bridge timeout will auto-cancel if POST fails
    }
  }

  function processAgentResponse(data: { response: string; toolCalls: ToolCallInfo[] }) {
    const content = data.response || "(no response)";
    // Terminal message closes the streaming phase — demote whatever was the
    // latest intermediate so it collapses with the rest of the thinking log.
    setLatestIntermediateId(null);
    setMessages((prev) => [
      ...prev,
      { id: crypto.randomUUID(), role: "assistant", content }
    ]);

    if (Array.isArray(data.toolCalls)) {
      for (const tc of data.toolCalls) {
        if (tc.displayHtml) {
          setLatestResult({
            ok: true,
            stdout: tc.stdout || "",
            stderr: tc.stderr || "",
            artifacts: [],
            meta: {
              tool: tc.tool,
              analysisSummary: tc.plotPath ? { plotPath: tc.plotPath } : undefined,
              references: parseReferencesFromStdout(tc.stdout || "")
            },
            ui: { kind: "html", html: tc.displayHtml }
          });
        }
      }

      const toolSummaries = data.toolCalls
        .filter((tc) => tc.tool === "run_bash" || tc.tool === "web_search")
        .map((tc) => {
          const label = tc.tool === "run_bash" ? "Code execution" : "Web search";
          const status = tc.ok ? "completed" : "failed";
          return `${label} ${status}`;
        });
      if (toolSummaries.length > 0) {
        setMessages((prev) => [
          ...prev,
          { id: crypto.randomUUID(), role: "tool", content: `Agent actions: ${toolSummaries.join(", ")}` }
        ]);
      }
    }
  }

  async function sendUserMessage() {
    const text = input.trim();
    if (!text) return;
    const userMessage: ChatMessage = {
      id: crypto.randomUUID(),
      role: "user",
      content: text
    };
    setMessages((prev) => [
      ...prev,
      userMessage
    ]);
    setInput("");
    setShowJumpToLatest(false);
    requestAnimationFrame(() => scrollChatToLatest("auto"));
    setAgentLogs([]);
    setLatestIntermediateId(null);

    if (!useLlm) return;

    setIsChatLoading(true);
    try {
      const history = messages
        .filter((m) => m.role === "user" || m.role === "assistant")
        .map((m) => ({ role: m.role, content: m.content }));

      const response = await fetch("/api/chat", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          message: text,
          history,
          loadedSlugs: Array.from(loadedSlugs),
        })
      });

      const reader = response.body!.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split(/\r?\n/);
        buffer = lines.pop() ?? "";

        for (const line of lines) {
          if (!line.startsWith("data:")) continue;
          const data = line.slice(5).trim();
          if (!data || data === "[DONE]") continue;

          let event: { type: string; [key: string]: unknown };
          try {
            event = JSON.parse(data);
          } catch {
            continue;
          }

          switch (event.type) {
            case "elicitation":
              setPendingElicitation({
                id: event.id as string,
                message: event.message as string,
                schema: event.schema as Record<string, unknown>
              });
              break;

            case "agent_response":
              processAgentResponse({
                response: event.response as string,
                toolCalls: event.toolCalls as ToolCallInfo[]
              });
              break;

            case "agent_turn": {
              // Intermediate update streamed mid-loop. Two shapes:
              //   { text: "..." }        → append as an intermediate chat
              //                             message (collapsed on arrival of
              //                             the next one)
              //   { toolCall: { ... } }  → refresh the output panel if the
              //                             tool produced a figure
              const text = typeof event.text === "string" ? event.text.trim() : "";
              if (text) {
                const newId = crypto.randomUUID();
                setMessages((prev) => [
                  ...prev,
                  { id: newId, role: "assistant", content: text, intermediate: true }
                ]);
                setLatestIntermediateId(newId);
                requestAnimationFrame(() => scrollChatToLatest("smooth"));
              }
              const tc = event.toolCall as ToolCallInfo | undefined;
              if (tc && tc.displayHtml) {
                setLatestResult({
                  ok: true,
                  stdout: tc.stdout || "",
                  stderr: tc.stderr || "",
                  artifacts: [],
                  meta: {
                    tool: tc.tool,
                    analysisSummary: tc.plotPath ? { plotPath: tc.plotPath } : undefined,
                    references: parseReferencesFromStdout(tc.stdout || "")
                  },
                  ui: { kind: "html", html: tc.displayHtml }
                });
              }
              break;
            }

            case "error":
              setMessages((prev) => [
                ...prev,
                {
                  id: crypto.randomUUID(),
                  role: "assistant",
                  content: `Agent error: ${event.error || "Unknown error"}`
                }
              ]);
              break;

            case "log":
              setAgentLogs((prev) => [...prev, {
                id: crypto.randomUUID(),
                ts: new Date().toISOString(),
                level: event.level as string,
                area: event.area as string,
                message: event.message as string,
                extra: event.extra as Record<string, unknown> | undefined,
              }]);
              break;

            case "done":
              break;
          }
        }
      }
    } catch {
      setMessages((prev) => [
        ...prev,
        {
          id: crypto.randomUUID(),
          role: "assistant",
          content: "Agent unavailable: failed to call /api/chat."
        }
      ]);
    } finally {
      setIsChatLoading(false);
    }
  }

  async function runSaltAnalysis() {
    const tool = "run_bash";
    setIsCalling(true);
    const salt = saltInput.trim() || "AlCl3-KCl";
    const command = `MPLBACKEND=Agg python3 /mnt/skills/salt-analysis/scripts/analyze_salt.py --salt ${salt} --output-dir /mnt/data/output/salt-plots`;
    const t0 = performance.now();

    try {
      const response = await fetch("/api/mcp/call", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ tool, args: { command } })
      });
      const t1 = performance.now();

      const result = (await response.json()) as ExecutionResult;
      let finalResult = result;
      const plotPath = extractPlotPath(result.stdout || "");
      let previewMs = 0;
      if (plotPath) {
        try {
          const previewStart = performance.now();
          const previewResponse = await fetch("/api/mcp/call", {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({ tool: "display_file", args: { uri: plotPath } })
          });
          const previewResult = (await previewResponse.json()) as ExecutionResult;
          previewMs = performance.now() - previewStart;
          if (previewResult.ui?.kind === "html") {
            finalResult = {
              ...result,
              ui: previewResult.ui
            };
          }
        } catch {
          // Keep original result when preview lookup fails.
        }
      }

      setLatestResult(finalResult);
      setMessages((prev) => [
        ...prev,
        {
          id: crypto.randomUUID(),
          role: "tool",
          content:
            `Tool ${tool} finished. ${formatResultSummary(finalResult)} ` +
            `(mcp: ${Math.round(t1 - t0)}ms, preview: ${Math.round(previewMs)}ms, total: ${Math.round(performance.now() - t0)}ms)`,
          result: finalResult
        }
      ]);
    } catch {
      const result: ExecutionResult = {
        ok: false,
        stdout: "",
        stderr: "Failed to call orchestrator route.",
        artifacts: [],
        meta: { tool },
        ui: { kind: "none" }
      };
      setLatestResult(result);
      setMessages((prev) => [
        ...prev,
        {
          id: crypto.randomUUID(),
          role: "tool",
          content: `Tool ${tool} failed.`,
          result
        }
      ]);
    } finally {
      setIsCalling(false);
      setShowAnalyzeModal(false);
    }
  }

  async function runSaltPrediction() {
    const tool = "run_bash";
    setIsCalling(true);
    const formula = predictFormulaInput.trim() || "NaCl";
    const comp = predictCompInput.trim() || "Pure Salt";
    const command =
      "MPLBACKEND=Agg python3 /mnt/skills/salt-prediction/scripts/predict_salt.py " +
      `--formula "${formula}" --comp "${comp}" --output-dir /mnt/data/output/salt-prediction`;
    const t0 = performance.now();

    try {
      const response = await fetch("/api/mcp/call", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ tool, args: { command } })
      });
      const t1 = performance.now();

      const result = (await response.json()) as ExecutionResult;
      let finalResult = result;
      const plotPath = extractPlotPath(result.stdout || "");
      let previewMs = 0;
      if (plotPath) {
        try {
          const previewStart = performance.now();
          const previewResponse = await fetch("/api/mcp/call", {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({ tool: "display_file", args: { uri: plotPath } })
          });
          const previewResult = (await previewResponse.json()) as ExecutionResult;
          previewMs = performance.now() - previewStart;
          if (previewResult.ui?.kind === "html") {
            finalResult = {
              ...result,
              ui: previewResult.ui
            };
          }
        } catch {
          // Keep original result when preview lookup fails.
        }
      }

      setLatestResult(finalResult);
      setMessages((prev) => [
        ...prev,
        {
          id: crypto.randomUUID(),
          role: "tool",
          content:
            `Tool ${tool} finished. ${formatResultSummary(finalResult)} ` +
            `(mcp: ${Math.round(t1 - t0)}ms, preview: ${Math.round(previewMs)}ms, total: ${Math.round(performance.now() - t0)}ms)`,
          result: finalResult
        }
      ]);
    } catch {
      const result: ExecutionResult = {
        ok: false,
        stdout: "",
        stderr: "Failed to call orchestrator route.",
        artifacts: [],
        meta: { tool },
        ui: { kind: "none" }
      };
      setLatestResult(result);
      setMessages((prev) => [
        ...prev,
        {
          id: crypto.randomUUID(),
          role: "tool",
          content: `Tool ${tool} failed.`,
          result
        }
      ]);
    } finally {
      setIsCalling(false);
      setShowPredictModal(false);
    }
  }

  async function checkMcpHealth() {
    setIsCheckingHealth(true);
    try {
      const response = await fetch("/api/mcp/health");
      const data = (await response.json()) as McpHealth;
      setMcpHealth(data);
    } catch {
      setMcpHealth({
        ok: false,
        mcpBaseUrl: "unknown",
        detail: "Failed to call /api/mcp/health"
      });
    } finally {
      setIsCheckingHealth(false);
    }
  }

  async function listMcpTools() {
    setIsLoadingTools(true);
    try {
      const response = await fetch("/api/mcp/tools");
      const data = (await response.json()) as McpToolsResponse;
      setMcpTools(data);
    } catch {
      setMcpTools({
        ok: false,
        tools: [],
        error: "Failed to call /api/mcp/tools"
      });
    } finally {
      setIsLoadingTools(false);
    }
  }

  useEffect(() => {
    if (showJumpToLatest) return;
    scrollChatToLatest("auto");
  }, [messages, isChatLoading, showJumpToLatest, agentLogs]);

  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [agentLogs]);

  return (
    <main
      ref={mainRef}
      style={
        {
          "--viz-width": `${vizWidth}px`,
          "--right-top-height": `${rightTopHeight}px`
        } as CSSProperties
      }
    >
      <header className="app-topbar">
        <div className="app-brand">
          <img
            className="app-logo"
            src="/genesis-amsc-lockup-horizontal-white-cropped.svg"
            alt="Genesis VISTA"
          />
          <div className="app-title">VISTA</div>
        </div>
        {activeProject && (
          <div className="app-active-project" title="Active project">
            <span className="app-active-project-label">Project</span>
            <span className="app-active-project-name">{activeProject.title}</span>
          </div>
        )}
      </header>

      <div className="workspace">

      <section className="panel" style={{ minHeight: 0 }}>
        <div className="panel-header">
          <div className="panel-header-stack">
            <div className="panel-title">Chat with</div>
            <div className="model-cascade-menu" ref={modelMenuRef}>
              <button
                type="button"
                className="input model-menu-trigger"
                onClick={() => {
                  setIsModelMenuOpen((prev) => {
                    const next = !prev;
                    if (next) {
                      setIsServiceExpanded(true);
                      setIsOpenModelsExpanded(chatFamily === "open models");
                    }
                    return next;
                  });
                }}
              >
                {chatService} / {chatFamily === "open models" ? chatOpenModel : chatFamily}
              </button>

              {isModelMenuOpen && (
                <div className="model-menu level1">
                  <button
                    type="button"
                    className="model-menu-item has-children"
                    onMouseEnter={() => setIsServiceExpanded(true)}
                    onClick={() => setIsServiceExpanded((prev) => !prev)}
                  >
                    {chatService}
                  </button>

                  {isServiceExpanded && (
                    <div className="model-menu level2">
                      {MODEL_FAMILIES.map((family) => (
                        <button
                          type="button"
                          key={family}
                          className={`model-menu-item ${family === "open models" ? "has-children" : ""}`}
                          onMouseEnter={() => setIsOpenModelsExpanded(family === "open models")}
                          onClick={() => {
                            setChatFamily(family);
                            if (family !== "open models") {
                              setIsModelMenuOpen(false);
                              setIsOpenModelsExpanded(false);
                            } else {
                              setIsOpenModelsExpanded(true);
                            }
                          }}
                        >
                          {family}
                        </button>
                      ))}
                    </div>
                  )}

                  {isServiceExpanded && isOpenModelsExpanded && (
                    <div className="model-menu level3">
                      {OPEN_MODELS.map((model) => (
                        <button
                          type="button"
                          key={model}
                          className="model-menu-item"
                          onClick={() => {
                            setChatFamily("open models");
                            setChatOpenModel(model);
                            setIsModelMenuOpen(false);
                            setIsOpenModelsExpanded(false);
                          }}
                        >
                          {model}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <button className="quick-chip" onClick={() => setShowAnalyzeModal(true)}>
              Analyze salt…
            </button>
            <button className="quick-chip" onClick={() => setShowPredictModal(true)}>
              Predict salt…
            </button>
            <label className="toggle-wrap">
              <span className="toggle-label">Agent</span>
              <input
                className="toggle-input"
                type="checkbox"
                checked={useLlm}
                onChange={(event) => setUseLlm(event.target.checked)}
              />
              <span className="toggle-slider" />
            </label>
          </div>
        </div>
        <div className="panel-body" style={{ flex: 1, position: "relative", overflow: "hidden" }}>
          <div ref={chatListRef} className="chat-list" onScroll={handleChatScroll}>
            {messages.length === 0 && (
              <div className="chat-bubble">
                Ask me about molten salts! Try: &quot;Show me the phase diagram for AlCl3-KCl&quot; or &quot;How many fluoride salts are in the database?&quot;
              </div>
            )}
            {messages.map((msg) => {
              const isIntermediate = !!msg.intermediate;
              const isLatestIntermediate = isIntermediate && msg.id === latestIntermediateId;
              const isManuallyExpanded = expandedIntermediates.has(msg.id);
              const collapsed = isIntermediate && !isLatestIntermediate && !isManuallyExpanded;

              if (collapsed) {
                return (
                  <button
                    key={msg.id}
                    type="button"
                    className="chat-bubble intermediate collapsed"
                    onClick={() => toggleIntermediate(msg.id)}
                    aria-expanded="false"
                  >
                    <span className="intermediate-chevron" aria-hidden="true">▸</span>
                    <span className="intermediate-label">agent thinking</span>
                    <span className="intermediate-preview">{intermediatePreview(msg.content)}</span>
                  </button>
                );
              }

              return (
                <div
                  key={msg.id}
                  className={`chat-bubble ${msg.role}${isIntermediate ? ` intermediate${isLatestIntermediate ? " current" : " expanded"}` : ""}`}
                >
                  {isIntermediate && (
                    <div className="intermediate-header">
                      <span className="intermediate-label">
                        {isLatestIntermediate ? "agent thinking · latest" : "agent thinking"}
                      </span>
                      {!isLatestIntermediate && (
                        <button
                          type="button"
                          className="intermediate-toggle"
                          onClick={() => toggleIntermediate(msg.id)}
                          aria-expanded="true"
                        >
                          collapse
                        </button>
                      )}
                    </div>
                  )}
                  {msg.role === "assistant" ? (
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.content}</ReactMarkdown>
                  ) : (
                    msg.content
                  )}
                  {msg.result && !msg.result.ok && (
                    <div className="error" style={{ marginTop: 6 }}>
                      {msg.result.stderr}
                    </div>
                  )}
                </div>
              );
            })}
            {isChatLoading && (
              <div className="chat-bubble assistant thinking" role="status" aria-live="polite">
                <span className="thinking-loader" aria-hidden="true">
                  <span />
                  <span />
                  <span />
                </span>
                <span>Working on it...</span>
              </div>
            )}
          </div>
          {showJumpToLatest && (
            <button
              type="button"
              className="chat-jump-latest"
              aria-label="Jump to latest"
              title="Jump to latest"
              onClick={() => {
                setShowJumpToLatest(false);
                scrollChatToLatest("smooth");
              }}
            >
              ↓
            </button>
          )}
        </div>
        <div className="chat-input-row">
          <input
            className="input"
            placeholder="Ask about molten salts... (e.g., 'show phase diagram for LiF-NaF')"
            value={input}
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                void sendUserMessage();
              }
            }}
          />
          <button className="button" onClick={() => void sendUserMessage()} disabled={isChatLoading}>
            {isChatLoading ? "Agent working..." : "⏎"}
          </button>
        </div>
      </section>

      <div
        className="panel-resizer"
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize output panel"
        onPointerDown={() => setActiveColumnResizer("right")}
      />

      <section className="panel">
        <div className="panel-header">
          <div className="panel-title">Latest Output</div>
        </div>
        <div className="panel-body">
          <div className="output-split" ref={outputSplitRef}>
            <div className="output-top">
              {!latestResult && <div className="chat-bubble">No figure yet.</div>}
              {latestResult && latestResult.ui?.kind === "html" && (
                <SandboxedHtmlCard html={latestResult.ui.html} />
              )}
              {latestResult && latestResult.ui?.kind !== "html" && (
                <div className="chat-bubble">No image or plot rendered for this result.</div>
              )}
              {latestPredictionSummary && (
                <details className="reference-box" open>
                  <summary>Prediction Summary</summary>
                  <div className="reference-list">
                    <div className="reference-item">Samples: {String(latestPredictionSummary.samples ?? "n/a")}</div>
                    <div className="reference-item">Features: {String(latestPredictionSummary.features ?? "n/a")}</div>
                    <div className="reference-item">Train size: {String(latestPredictionSummary.train_size ?? "n/a")}</div>
                    <div className="reference-item">Test size: {String(latestPredictionSummary.test_size ?? "n/a")}</div>
                    <div className="reference-item">MAE: {String(latestPredictionSummary.mae_k ?? "n/a")} K</div>
                    <div className="reference-item">RMSE: {String(latestPredictionSummary.rmse_k ?? "n/a")} K</div>
                    <div className="reference-item">R2: {String(latestPredictionSummary.r2 ?? "n/a")}</div>
                    <div className="reference-item">
                      Predicted ({String(latestPredictionSummary.formula ?? "n/a")} |{" "}
                      {String(latestPredictionSummary.composition ?? "n/a")}):{" "}
                      {String(latestPredictionSummary.predicted_melting_point_k ?? "n/a")} +/-{" "}
                      {String(latestPredictionSummary.predicted_uncertainty_k ?? "n/a")} K
                    </div>
                  </div>
                </details>
              )}
              {!latestPredictionSummary && latestReferences.length > 0 && (
                <details className="reference-box" open>
                  <summary>References ({latestReferences.length})</summary>
                  <div className="reference-list">
                    {latestReferences.map((ref, index) => (
                      <div key={`${ref}-${index}`} className="reference-item">
                        [{index + 1}] {ref}
                      </div>
                    ))}
                  </div>
                </details>
              )}
            </div>
            <div
              className="stack-resizer"
              role="separator"
              aria-orientation="horizontal"
              aria-label="Resize output stack"
              onPointerDown={() => setActiveRowResizer("right")}
            />

            <div className="output-bottom">
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "6px 10px", borderBottom: "1px solid var(--border)" }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span style={{ fontSize: 13, fontWeight: 600, color: "var(--fg)" }}>Agent Logs</span>
                  <span style={{ fontSize: 11, color: "var(--fg-muted)", fontFamily: "monospace" }}>
                    {agentLogs.length > 0 ? `${agentLogs.length} entries` : ""}
                  </span>
                </div>
                <div style={{ display: "flex", gap: 6 }}>
                  <button className="button ghost button-xs" onClick={checkMcpHealth} disabled={isCheckingHealth}>
                    {isCheckingHealth ? "Checking..." : "MCP Status"}
                  </button>
                  <button className="button ghost button-xs" onClick={listMcpTools} disabled={isLoadingTools}>
                    {isLoadingTools ? "Loading..." : "Tools"}
                  </button>
                  <button className="button ghost button-xs" onClick={() => setAgentLogs([])}>
                    Clear
                  </button>
                </div>
              </div>

              {mcpHealth && (
                <div className="log-status-bar">
                  MCP: {mcpHealth.ok ? "✓ Connected" : "✗ Disconnected"} ({mcpHealth.mcpBaseUrl})
                  {mcpHealth.detail ? ` — ${mcpHealth.detail}` : ""}
                </div>
              )}

              {mcpTools && mcpTools.ok && mcpTools.tools.length > 0 && (
                <div className="log-status-bar">
                  Tools: {mcpTools.tools.map((t) => t.name).join(", ")}
                </div>
              )}

              <div className="log-viewer">
                {agentLogs.length === 0 && (
                  <div className="log-empty">Waiting for agent activity…</div>
                )}
                {agentLogs.map((entry) => (
                  <div key={entry.id} className={`log-line log-${entry.level.toLowerCase()}`}>
                    <span className="log-ts">{entry.ts.slice(11, 23)}</span>
                    <span className="log-level">{entry.level}</span>
                    <span className="log-area">[{entry.area}]</span>
                    <span className="log-msg">{entry.message}</span>
                    {entry.extra && (
                      <span className="log-extra"> {JSON.stringify(entry.extra)}</span>
                    )}
                  </div>
                ))}
                <div ref={logEndRef} />
              </div>
            </div>
          </div>
        </div>
        </section>
      </div>

      {showAnalyzeModal && (
        <div className="modal-backdrop" onClick={() => setShowAnalyzeModal(false)}>
          <div className="modal" onClick={(event) => event.stopPropagation()}>
            <div className="panel-header">
              <div className="panel-title">Analyze Salt</div>
              <button className="button ghost" onClick={() => setShowAnalyzeModal(false)}>
                Close
              </button>
            </div>
            <div className="modal-body">
              <label>
                Salt string
                <input
                  className="input"
                  value={saltInput}
                  onChange={(event) => setSaltInput(event.target.value)}
                />
              </label>
              <button className="button secondary" onClick={runSaltAnalysis} disabled={isCalling}>
                {isCalling ? "Running..." : "Run analysis"}
              </button>
            </div>
          </div>
        </div>
      )}

      {showPredictModal && (
        <div className="modal-backdrop" onClick={() => setShowPredictModal(false)}>
          <div className="modal" onClick={(event) => event.stopPropagation()}>
            <div className="panel-header">
              <div className="panel-title">Predict Salt</div>
              <button className="button ghost" onClick={() => setShowPredictModal(false)}>
                Close
              </button>
            </div>
            <div className="modal-body">
              <label>
                Formula
                <input
                  className="input"
                  value={predictFormulaInput}
                  onChange={(event) => setPredictFormulaInput(event.target.value)}
                />
              </label>
              <label>
                Composition
                <input
                  className="input"
                  value={predictCompInput}
                  onChange={(event) => setPredictCompInput(event.target.value)}
                />
              </label>
              <button className="button secondary" onClick={runSaltPrediction} disabled={isCalling}>
                {isCalling ? "Running..." : "Run prediction"}
              </button>
            </div>
          </div>
        </div>
      )}

      {pendingElicitation && (
        <ElicitationModal
          id={pendingElicitation.id}
          message={pendingElicitation.message}
          schema={pendingElicitation.schema}
          onSubmit={handleElicitationSubmit}
        />
      )}
    </main>
  );
}
