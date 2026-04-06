"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import ReactMarkdown from "react-markdown";
import SandboxedHtmlCard from "@/components/SandboxedHtmlCard";
import ElicitationModal from "@/components/ElicitationModal";
import type { ChatMessage, ExecutionResult, SkillDetail, SkillSummary } from "@/lib/types";

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

type UploadFileInfo = {
  name: string;
  size: number;
  modifiedAt: string;
};

type UploadResponse = {
  ok: boolean;
  saved?: string[];
  error?: string;
};

const MODEL_SERVICES = ["AmSC model services"];
const MODEL_FAMILIES = ["gpt-5", "claude", "open models"];
const OPEN_MODELS = ["open-ai/gpt-oss-20b"];

const PLACEHOLDER_DATASETS = [
  "allenai/scientific_papers",
  "OpenDFM/ScienceQA",
  "bigbio/pubmed_qa"
];

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

function formatTimestampUtc(value: string): string {
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toISOString().replace("T", " ").replace(".000Z", " UTC");
}

export default function HomePage() {
  const mainRef = useRef<HTMLElement | null>(null);
  const leftSplitRef = useRef<HTMLDivElement | null>(null);
  const outputSplitRef = useRef<HTMLDivElement | null>(null);
  const chatListRef = useRef<HTMLDivElement | null>(null);
  const uploadInputRef = useRef<HTMLInputElement | null>(null);
  const modelMenuRef = useRef<HTMLDivElement | null>(null);
  const [activeColumnResizer, setActiveColumnResizer] = useState<"left" | "right" | null>(null);
  const [activeRowResizer, setActiveRowResizer] = useState<"left" | "right" | null>(null);
  const [skillsWidth, setSkillsWidth] = useState(300);
  const [vizWidth, setVizWidth] = useState(460);
  const [leftTopHeight, setLeftTopHeight] = useState(500);
  const [rightTopHeight, setRightTopHeight] = useState(430);

  const [skills, setSkills] = useState<SkillSummary[]>([]);
  const [filter, setFilter] = useState("");
  const [selectedSkill, setSelectedSkill] = useState<SkillDetail | null>(null);
  const [showSkillModal, setShowSkillModal] = useState(false);

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");

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
  const [uploads, setUploads] = useState<UploadFileInfo[]>([]);
  const [isLoadingUploads, setIsLoadingUploads] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [uploadError, setUploadError] = useState("");
  const [uploadMessage, setUploadMessage] = useState("");
  const [isDragOverUploads, setIsDragOverUploads] = useState(false);
  const [deletingUploadName, setDeletingUploadName] = useState("");
  const [chatService] = useState(MODEL_SERVICES[0]);
  const [chatFamily, setChatFamily] = useState(MODEL_FAMILIES[0]);
  const [chatOpenModel, setChatOpenModel] = useState(OPEN_MODELS[0]);
  const [isModelMenuOpen, setIsModelMenuOpen] = useState(false);
  const [isServiceExpanded, setIsServiceExpanded] = useState(false);
  const [isOpenModelsExpanded, setIsOpenModelsExpanded] = useState(false);
  const [showJumpToLatest, setShowJumpToLatest] = useState(false);
  const [dataModel, setDataModel] = useState(PLACEHOLDER_DATASETS[0]);
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
    if (!activeColumnResizer) return;

    const minSkills = 180;
    const minViz = 320;
    const minConsole = 420;
    const splitterTotal = 20;

    const onPointerMove = (event: PointerEvent) => {
      const container = mainRef.current;
      if (!container || window.innerWidth <= 1100) return;

      const rect = container.getBoundingClientRect();
      const maxSkills = rect.width - vizWidth - minConsole - splitterTotal;
      const maxViz = rect.width - skillsWidth - minConsole - splitterTotal;

      if (activeColumnResizer === "left") {
        const raw = event.clientX - rect.left;
        const next = Math.max(minSkills, Math.min(raw, maxSkills));
        setSkillsWidth(next);
      } else if (activeColumnResizer === "right") {
        const raw = rect.right - event.clientX;
        const next = Math.max(minViz, Math.min(raw, maxViz));
        setVizWidth(next);
      }
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
  }, [activeColumnResizer, skillsWidth, vizWidth]);

  useEffect(() => {
    if (!activeRowResizer) return;

    const splitterSize = 10;
    const minTop = 170;
    const minBottom = 180;

    const onPointerMove = (event: PointerEvent) => {
      if (window.innerWidth <= 1100) return;

      const container = activeRowResizer === "left" ? leftSplitRef.current : outputSplitRef.current;
      if (!container) return;

      const rect = container.getBoundingClientRect();
      const raw = event.clientY - rect.top;
      const maxTop = rect.height - minBottom - splitterSize;
      const next = Math.max(minTop, Math.min(raw, maxTop));

      if (activeRowResizer === "left") {
        setLeftTopHeight(next);
      } else {
        setRightTopHeight(next);
      }
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
    fetch("/api/skills")
      .then((res) => res.json())
      .then((data) => setSkills(Array.isArray(data) ? data : []))
      .catch(() => setSkills([]));
  }, []);

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

  const filteredSkills = useMemo(() => {
    const term = filter.trim().toLowerCase();
    if (!term) return skills;
    return skills.filter((skill) => {
      return (
        skill.slug.toLowerCase().includes(term) ||
        skill.name.toLowerCase().includes(term) ||
        skill.description.toLowerCase().includes(term)
      );
    });
  }, [skills, filter]);

  async function openSkill(slug: string) {
    try {
      const response = await fetch(`/api/skills/${slug}`);
      if (!response.ok) return;
      const detail = (await response.json()) as SkillDetail;
      setSelectedSkill(detail);
      setShowSkillModal(true);
    } catch {
      setSelectedSkill(null);
    }
  }

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

    if (!useLlm) return;

    setIsChatLoading(true);
    try {
      const history = messages
        .filter((m) => m.role === "user" || m.role === "assistant")
        .map((m) => ({ role: m.role, content: m.content }));

      const response = await fetch("/api/chat", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ message: text, history })
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

  const loadUploads = useCallback(async () => {
    setIsLoadingUploads(true);
    try {
      const response = await fetch("/api/uploads");
      const data = (await response.json()) as UploadFileInfo[];
      setUploads(Array.isArray(data) ? data : []);
    } catch {
      setUploads([]);
    } finally {
      setIsLoadingUploads(false);
    }
  }, []);

  async function refreshUploads() {
    await loadUploads();
    setUploadMessage((prev) => (prev.startsWith("Deleted ") ? "" : prev));
  }

  useEffect(() => {
    void loadUploads();
  }, [loadUploads]);

  useEffect(() => {
    if (showJumpToLatest) return;
    scrollChatToLatest("auto");
  }, [messages, isChatLoading, showJumpToLatest, agentLogs]);

  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [agentLogs]);


  async function uploadFiles(files: FileList | null) {
    if (!files || files.length === 0) return;

    setIsUploading(true);
    setUploadError("");
    setUploadMessage("");

    try {
      const form = new FormData();
      for (const file of Array.from(files)) {
        form.append("files", file);
      }

      const response = await fetch("/api/uploads", {
        method: "POST",
        body: form
      });
      const data = (await response.json()) as UploadResponse;

      if (!response.ok || !data.ok) {
        setUploadError(data.error || "Upload failed.");
        return;
      }

      const savedCount = Array.isArray(data.saved) ? data.saved.length : 0;
      setUploadMessage(savedCount > 0 ? `${savedCount} file(s) uploaded.` : "Upload complete.");
      await loadUploads();
    } catch {
      setUploadError("Upload failed.");
    } finally {
      setIsUploading(false);
    }
  }

  async function deleteUpload(name: string) {
    setDeletingUploadName(name);
    setUploadError("");
    setUploadMessage("");
    try {
      const response = await fetch(`/api/uploads/${encodeURIComponent(name)}`, {
        method: "DELETE"
      });
      const data = (await response.json()) as UploadResponse;
      if (!response.ok || !data.ok) {
        setUploadError(data.error || "Delete failed.");
        return;
      }
      setUploadMessage(`Deleted ${name}.`);
      await loadUploads();
    } catch {
      setUploadError("Delete failed.");
    } finally {
      setDeletingUploadName("");
    }
  }

  return (
    <main
      ref={mainRef}
      style={
        {
          "--skills-width": `${skillsWidth}px`,
          "--viz-width": `${vizWidth}px`,
          "--left-top-height": `${leftTopHeight}px`,
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
      </header>

      <div className="workspace">
        <section className="left-stack">
        <div className="left-split" ref={leftSplitRef}>
          <section className="panel">
            <div className="panel-header">
              <div className="panel-title">Data</div>
              <span className="tag">/mnt/data/uploads</span>
            </div>
            <div className="panel-body">
              <div style={{ display: "flex", gap: 8, marginBottom: 12, flexWrap: "wrap" }}>
                <button
                  className="button button-sm"
                  onClick={() => uploadInputRef.current?.click()}
                  disabled={isUploading}
                >
                  {isUploading ? "Uploading..." : "Upload +"}
                </button>
                <button className="button ghost button-sm" onClick={() => void refreshUploads()} disabled={isLoadingUploads}>
                  {isLoadingUploads ? "Refreshing..." : "Refresh"}
                </button>
              </div>
              <div className="catalog-row">
                <label className="model-label catalog-label" htmlFor="data-model-select">AmSC Data Catalog</label>
                <select
                  id="data-model-select"
                  className="input model-select model-select-full"
                  value={dataModel}
                  onChange={(event) => setDataModel(event.target.value)}
                >
                  {PLACEHOLDER_DATASETS.map((dataset) => (
                    <option key={dataset} value={dataset}>
                      {dataset}
                    </option>
                  ))}
                </select>
              </div>
              <div
                className={`dropzone ${isDragOverUploads ? "active" : ""}`}
                onDragOver={(event) => {
                  event.preventDefault();
                  setIsDragOverUploads(true);
                }}
                onDragEnter={(event) => {
                  event.preventDefault();
                  setIsDragOverUploads(true);
                }}
                onDragLeave={(event) => {
                  event.preventDefault();
                  if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
                    setIsDragOverUploads(false);
                  }
                }}
                onDrop={(event) => {
                  event.preventDefault();
                  setIsDragOverUploads(false);
                  void uploadFiles(event.dataTransfer.files);
                }}
                onClick={() => uploadInputRef.current?.click()}
                role="button"
                tabIndex={0}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    uploadInputRef.current?.click();
                  }
                }}
              >
                Drag and drop files here, or click to browse.
              </div>
              <input
                ref={uploadInputRef}
                type="file"
                multiple
                style={{ display: "none" }}
                onChange={(event) => {
                  void uploadFiles(event.target.files);
                  event.currentTarget.value = "";
                }}
              />

              {uploadMessage && (
                <div className="chat-bubble tool" style={{ marginBottom: 8 }}>
                  {uploadMessage}
                </div>
              )}
              {uploadError && (
                <div className="chat-bubble" style={{ marginBottom: 8 }}>
                  <div className="error">{uploadError}</div>
                </div>
              )}

              <div className="upload-list">
                {uploads.length === 0 && (
                  <div className="chat-bubble">
                    {isLoadingUploads ? "Loading uploads..." : "No uploaded files yet."}
                  </div>
                )}
                {uploads.map((file) => (
                  <div key={file.name} className="upload-item">
                    <div className="upload-name">{file.name}</div>
                    <div className="upload-meta">
                      {(file.size / 1024).toFixed(1)} KB - {formatTimestampUtc(file.modifiedAt)}
                    </div>
                    <div className="upload-actions">
                      <a
                        className="button ghost button-xs"
                        href={`/api/uploads/${encodeURIComponent(file.name)}`}
                        download={file.name}
                      >
                        Download
                      </a>
                      <button
                        className="button ghost button-xs"
                        onClick={() => void deleteUpload(file.name)}
                        disabled={deletingUploadName === file.name}
                      >
                        {deletingUploadName === file.name ? "Deleting..." : "Delete"}
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </section>
          <div
            className="stack-resizer"
            role="separator"
            aria-orientation="horizontal"
            aria-label="Resize left stack"
            onPointerDown={() => setActiveRowResizer("left")}
          />

          <section className="panel">
            <div className="panel-header">
              <div className="panel-title">Skills</div>
              <span className="tag">{skills.length} loaded</span>
            </div>
            <div className="panel-body">
              <input
                className="input"
                placeholder="Search skills"
                value={filter}
                onChange={(event) => setFilter(event.target.value)}
              />
              <div style={{ marginTop: 12, display: "flex", flexDirection: "column", gap: 8 }}>
                {filteredSkills.map((skill) => (
                  <div
                    key={skill.slug}
                    className="skill-item"
                    onClick={() => openSkill(skill.slug)}
                  >
                    <div className="skill-name">{skill.name}</div>
                    <div className="skill-desc">{skill.description || "No description"}</div>
                  </div>
                ))}
              </div>
            </div>
          </section>
        </div>
      </section>

      <div
        className="panel-resizer"
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize skills panel"
        onPointerDown={() => setActiveColumnResizer("left")}
      />

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
            {messages.map((msg) => (
              <div key={msg.id} className={`chat-bubble ${msg.role}`}>
                {msg.role === "assistant" ? (
                  <ReactMarkdown>{msg.content}</ReactMarkdown>
                ) : (
                  msg.content
                )}
                {msg.result && !msg.result.ok && (
                  <div className="error" style={{ marginTop: 6 }}>
                    {msg.result.stderr}
                  </div>
                )}
              </div>
            ))}
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

      {showSkillModal && selectedSkill && (
        <div className="modal-backdrop" onClick={() => setShowSkillModal(false)}>
          <div className="modal" onClick={(event) => event.stopPropagation()}>
            <div className="panel-header">
              <div className="panel-title">
                {typeof selectedSkill.frontmatter?.name === "string" ? selectedSkill.frontmatter.name : selectedSkill.slug}
              </div>
              <button className="button ghost" onClick={() => setShowSkillModal(false)}>
                Close
              </button>
            </div>
            <div className="modal-body">
              <ReactMarkdown>{selectedSkill.markdown}</ReactMarkdown>
            </div>
          </div>
        </div>
      )}

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
