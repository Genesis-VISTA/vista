"use client";

import { useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import SandboxedHtmlCard from "@/components/SandboxedHtmlCard";
import ElicitationModal from "@/components/ElicitationModal";
import ToolApprovalModal, { type DecisionMetadata } from "@/components/ToolApprovalModal";
import {
  SkillEditorModal,
  type SkillDraftFields,
  type SkillSavedPayload,
} from "@/components/SkillEditorModal";
import type { ChatMessage, ExecutionResult } from "@/lib/types";
import { readActiveProjectName, useActiveProject } from "@/lib/projects";
import { readAdditions, writeAdditions } from "@/lib/loaded-skills";
import {
  htmlFromToolReturnContent,
  type AgentRunResultEvent,
  type FunctionToolCallEvent,
  type FunctionToolResultEvent,
  type LogEvent,
  type McpFormElicitationEvent,
  type McpUrlElicitationEvent,
  type McpToolApprovalEvent,
  type ModelMessage,
  type PartDeltaEvent,
  type PartEndEvent,
  type PartStartEvent,
} from "@/lib/agent-events";

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

const MODEL_SERVICES = ["AmSC model services"];
const MODEL_FAMILIES = ["gpt-5", "claude", "open models"];
const OPEN_MODELS = ["open-ai/gpt-oss-20b"];

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
   * Active project for the topbar badge. The hook uses
   * `useSyncExternalStore` so SSR and the first client paint both read
   * `null`, then React updates with the real value after hydration. Other
   * tabs / pages that write the active-project slug are picked up via the
   * hook's storage subscription.
   */
  const activeProject = useActiveProject();

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  /**
   * Raw PydanticAI `ModelMessage` history — accumulated across turns from each
   * `agent_run_result.new_messages` and sent back as `message_history` on the
   * next turn so the agent has full conversational context. Separate from the
   * display `messages` state; we never construct or mutate these payloads.
   */
  const [messageHistory, setMessageHistory] = useState<ModelMessage[]>([]);
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

  /**
   * Reset the conversation when the active project changes. `messageHistory`
   * is tied to a specific project's system prompt and tool set; replaying it
   * under a different project would leak context across projects. The sentinel
   * `undefined` lets us skip the initial `null → resolved` hydration step.
   */
  const prevProjectIdRef = useRef<string | null | undefined>(undefined);
  useEffect(() => {
    const currentId = activeProject?.id ?? null;
    const prev = prevProjectIdRef.current;
    prevProjectIdRef.current = currentId;
    if (prev === undefined || prev === currentId) return;
    setMessages([]);
    setMessageHistory([]);
    setLatestIntermediateId(null);
    setExpandedIntermediates(new Set());
  }, [activeProject?.id]);

  const [showAnalyzeModal, setShowAnalyzeModal] = useState(false);
  const [saltInput, setSaltInput] = useState("AlCl3-KCl");
  const [showPredictModal, setShowPredictModal] = useState(false);
  const [predictFormulaInput, setPredictFormulaInput] = useState("NaCl");
  const [predictCompInput, setPredictCompInput] = useState("Pure Salt");

  // Save-as-skill state. `initial: null` while the LLM is drafting; the
  // modal swaps into edit mode once the draft arrives. We keep an error
  // banner separately so we can show backend rejections (duplicate slug,
  // malformed name) without dropping the user's in-flight edits.
  const [showSkillEditor, setShowSkillEditor] = useState(false);
  const [skillDraft, setSkillDraft] = useState<SkillDraftFields | null>(null);
  const [skillSaveError, setSkillSaveError] = useState<string | null>(null);

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
  const [pendingToolApproval, setPendingToolApproval] = useState<{
    id: string;
    toolName: string;
    message: string;
    args: Record<string, unknown> | null;
    decisionMetadata: DecisionMetadata | null;
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
    setPendingToolApproval(null);
    // Resolve against the project that issued the elicitation. We only ever
    // start an agent run with an active project, so falling back to the
    // current selection is correct in practice.
    const projectName = readActiveProjectName();
    if (!projectName) return;
    try {
      await fetch("/api/chat/elicitation", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ project_name: projectName, id, action, content })
      });
    } catch {
      // bridge timeout will auto-cancel if POST fails
    }
  }

  /**
   * Open the Save-as-Skill modal and kick off the LLM draft in parallel.
   * The modal opens immediately in a "Drafting…" state and switches to the
   * editable form once `/api/skills/generate` returns.
   */
  async function openSaveAsSkill() {
    setSkillDraft(null);
    setSkillSaveError(null);
    setShowSkillEditor(true);
    try {
      const resp = await fetch("/api/skills/generate", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ message_history: messageHistory }),
      });
      if (!resp.ok) {
        const text = await resp.text().catch(() => "");
        setSkillSaveError(text || `Drafting failed (${resp.status}).`);
        setSkillDraft({
          name: "",
          description: "",
          body: "",
          author: "",
          repoUrl: "",
          tags: "",
        });
        return;
      }
      const draft = (await resp.json()) as {
        name_suggestion: string;
        description_suggestion: string;
        body: string;
      };
      setSkillDraft({
        name: draft.name_suggestion ?? "",
        description: draft.description_suggestion ?? "",
        body: draft.body ?? "",
        author: "",
        repoUrl: "",
        tags: "",
      });
    } catch (err) {
      setSkillSaveError(err instanceof Error ? err.message : "Drafting failed.");
      setSkillDraft({
        name: "",
        description: "",
        body: "",
        author: "",
        repoUrl: "",
        tags: "",
      });
    }
  }

  async function saveSkill(payload: SkillSavedPayload) {
    setSkillSaveError(null);
    const resp = await fetch("/api/skills", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        name: payload.name,
        description: payload.description,
        body: payload.body,
        author: payload.author,
        repo_url: payload.repoUrl,
        tags: payload.tags,
        is_public: payload.isPublic,
      }),
    });
    if (!resp.ok) {
      const text = await resp.text().catch(() => "");
      let detail = text;
      try {
        const parsed = JSON.parse(text);
        if (parsed && typeof parsed === "object" && "detail" in parsed) {
          detail = String((parsed as { detail: unknown }).detail);
        } else if (parsed && typeof parsed === "object" && "error" in parsed) {
          detail = String((parsed as { error: unknown }).error);
        }
      } catch {
        // text wasn't JSON; use it as-is
      }
      setSkillSaveError(detail || `Save failed (${resp.status}).`);
      return;
    }
    // Auto-load the new skill into the active project's additions set so the
    // user sees it on /skills immediately.
    const projectName = activeProject?.name ?? null;
    if (projectName) {
      const additions = readAdditions(projectName);
      additions.add(payload.name);
      writeAdditions(projectName, additions);
    }
    setShowSkillEditor(false);
    setSkillDraft(null);
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

    const projectName = readActiveProjectName();
    if (!projectName) {
      setMessages((prev) => [
        ...prev,
        {
          id: crypto.randomUUID(),
          role: "assistant",
          content: "Select a project first — open the Projects page from the sidebar."
        }
      ]);
      return;
    }

    setIsChatLoading(true);
    // Per-turn streaming-part accumulator, keyed by PartStartEvent.index.
    // Text parts also track the id of the live assistant bubble they update.
    type PartAcc =
      | { kind: "text"; bubbleId: string; text: string }
      | { kind: "thinking"; text: string }
      | { kind: "tool-call"; toolName: string; argsText: string };
    const parts = new Map<number, PartAcc>();
    let finalSignaled = false;
    let liveAssistantBubbleId: string | null = null;
    let sawAgentRunResult = false;

    function pushLog(level: string, area: string, message: string) {
      setAgentLogs((prev) => [
        ...prev,
        { id: crypto.randomUUID(), ts: new Date().toISOString(), level, area, message }
      ]);
    }

    function updateBubbleContent(bubbleId: string, content: string) {
      setMessages((prev) =>
        prev.map((m) => (m.id === bubbleId ? { ...m, content } : m))
      );
    }

    function promoteBubbleToFinal(bubbleId: string) {
      setMessages((prev) =>
        prev.map((m) => (m.id === bubbleId ? { ...m, intermediate: false } : m))
      );
    }

    function dispatchEvent(eventName: string | null, data: unknown) {
      const kind =
        eventName ??
        (data && typeof data === "object"
          ? ((data as { event_kind?: string }).event_kind ?? null)
          : null);

      switch (kind) {
        case "log": {
          const ev = data as LogEvent;
          pushLog(ev.level, ev.area, ev.message);
          break;
        }

        case "part_start": {
          const ev = data as PartStartEvent;
          const part = ev.part;
          if (!part || typeof part !== "object") break;
          const pkind = (part as { part_kind?: string }).part_kind;
          if (pkind === "text") {
            const initial = ((part as { content?: unknown }).content as string) ?? "";
            const bubbleId = crypto.randomUUID();
            const intermediate = !finalSignaled;
            setMessages((prev) => [
              ...prev,
              { id: bubbleId, role: "assistant", content: initial, intermediate }
            ]);
            if (intermediate) {
              setLatestIntermediateId(bubbleId);
              requestAnimationFrame(() => scrollChatToLatest("smooth"));
            }
            liveAssistantBubbleId = bubbleId;
            parts.set(ev.index, { kind: "text", bubbleId, text: initial });
          } else if (pkind === "thinking") {
            const initial = ((part as { content?: unknown }).content as string) ?? "";
            parts.set(ev.index, { kind: "thinking", text: initial });
            if (initial) pushLog("INFO", "Agent", `thinking: ${initial}`);
          } else if (pkind === "tool-call" || pkind === "builtin-tool-call") {
            const toolName = ((part as { tool_name?: unknown }).tool_name as string) ?? "";
            parts.set(ev.index, { kind: "tool-call", toolName, argsText: "" });
          }
          break;
        }

        case "part_delta": {
          const ev = data as PartDeltaEvent;
          const acc = parts.get(ev.index);
          if (!acc) break;
          const delta = ev.delta;
          if (!delta || typeof delta !== "object") break;
          const dkind = (delta as { part_delta_kind?: string }).part_delta_kind;
          if (dkind === "text" && acc.kind === "text") {
            const chunk = ((delta as { content_delta?: unknown }).content_delta as string) ?? "";
            if (chunk) {
              acc.text += chunk;
              updateBubbleContent(acc.bubbleId, acc.text);
            }
          } else if (dkind === "thinking" && acc.kind === "thinking") {
            const chunk = ((delta as { content_delta?: unknown }).content_delta as string) ?? "";
            if (chunk) acc.text += chunk;
          } else if (dkind === "tool_call" && acc.kind === "tool-call") {
            const nameChunk =
              ((delta as { tool_name_delta?: unknown }).tool_name_delta as string) ?? "";
            const argsChunk = (delta as { args_delta?: unknown }).args_delta;
            if (nameChunk) acc.toolName += nameChunk;
            if (typeof argsChunk === "string") acc.argsText += argsChunk;
          }
          break;
        }

        case "part_end": {
          const ev = data as PartEndEvent;
          const acc = parts.get(ev.index);
          const part = ev.part;
          if (acc?.kind === "text" && part && typeof part === "object") {
            const final = ((part as { content?: unknown }).content as string) ?? acc.text;
            updateBubbleContent(acc.bubbleId, final);
          } else if (acc?.kind === "thinking" && acc.text) {
            pushLog("INFO", "Agent", `thinking: ${acc.text}`);
          }
          parts.delete(ev.index);
          break;
        }

        case "final_result": {
          finalSignaled = true;
          // If a text bubble is already streaming, this turn's text part IS
          // the final answer — promote it now so it doesn't get collapsed.
          if (liveAssistantBubbleId) {
            promoteBubbleToFinal(liveAssistantBubbleId);
            setLatestIntermediateId((prev) => (prev === liveAssistantBubbleId ? null : prev));
          }
          break;
        }

        case "function_tool_call": {
          const ev = data as FunctionToolCallEvent;
          const toolName = ev.part?.tool_name ?? "tool";
          const newId = crypto.randomUUID();
          setMessages((prev) => [
            ...prev,
            {
              id: newId,
              role: "assistant",
              content: `Calling \`${toolName}\`…`,
              intermediate: true
            }
          ]);
          setLatestIntermediateId(newId);
          requestAnimationFrame(() => scrollChatToLatest("smooth"));
          break;
        }

        case "function_tool_result": {
          const ev = data as FunctionToolResultEvent;
          // PydanticAI 1.105 renamed `result` -> `part`; fall back to
          // `result` for older backends.
          const result = ev.part ?? ev.result;
          if (!result) break;
          if (result.part_kind === "retry-prompt") {
            pushLog(
              "WARNING",
              `Tool:${result.tool_name ?? "unknown"}`,
              `Tool failed`
            );
            break;
          }
          if (result.part_kind === "tool-return" || result.part_kind === "builtin-tool-return") {
            const html = htmlFromToolReturnContent(result.content);
            if (html) {
              setLatestResult({
                ok: true,
                stdout: "",
                stderr: "",
                artifacts: [],
                meta: { tool: result.tool_name },
                ui: { kind: "html", html }
              });
            }
          }
          break;
        }

        case "agent_run_result": {
          const ev = data as AgentRunResultEvent;
          sawAgentRunResult = true;
          if (ev.result?.new_messages?.length) {
            setMessageHistory((prev) => [...prev, ...ev.result.new_messages]);
          }
          if (liveAssistantBubbleId) {
            promoteBubbleToFinal(liveAssistantBubbleId);
          }
          setLatestIntermediateId(null);
          break;
        }

        case "mcp_form_elicitation": {
          const ev = data as McpFormElicitationEvent;
          setPendingElicitation({
            id: ev.elicitation_id,
            message: ev.message,
            schema: ev.requested_schema
          });
          break;
        }

        case "mcp_url_elicitation": {
          const ev = data as McpUrlElicitationEvent;
          // v1: synchronous confirm. The backend is blocked waiting for a
          // POST to /api/chat/elicitation, so briefly blocking the UI is
          // acceptable. Refine to a proper modal when the URL flow gets
          // first-class UX.
          const allow = window.confirm(
            `${ev.message}\n\nAllow the agent to open:\n${ev.url}`
          );
          void handleElicitationSubmit(
            ev.elicitation_id,
            allow ? "accept" : "cancel"
          );
          if (allow) window.open(ev.url, "_blank", "noopener,noreferrer");
          break;
        }

        case "mcp_tool_approval": {
          const ev = data as McpToolApprovalEvent;
          setPendingToolApproval({
            id: ev.elicitation_id,
            toolName: ev.tool_name,
            message: ev.message,
            args: ev.args ?? null,
            decisionMetadata: (ev.decision_metadata as DecisionMetadata) ?? null,
          });
          break;
        }
      }
    }

    let currentEvent: string | null = null;
    let dataLines: string[] = [];

    function dispatchBlock() {
      if (dataLines.length === 0) {
        currentEvent = null;
        return;
      }
      const raw = dataLines.join("\n");
      dataLines = [];
      const eventName = currentEvent;
      currentEvent = null;
      try {
        dispatchEvent(eventName, JSON.parse(raw));
      } catch {
        // Drop malformed event blocks rather than aborting the stream.
      }
    }

    try {
      const response = await fetch("/api/chat", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          project_name: projectName,
          user_prompt: text,
          message_history: messageHistory,
        })
      });

      if (!response.ok || !response.body) {
        let detail = `Backend returned ${response.status}`;
        try {
          const errData = await response.json();
          detail = errData?.error || errData?.detail || detail;
        } catch {
          // ignore
        }
        setMessages((prev) => [
          ...prev,
          { id: crypto.randomUUID(), role: "assistant", content: `Agent error: ${detail}` }
        ]);
        return;
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() ?? "";

        for (const rawLine of lines) {
          const line = rawLine.replace(/\r$/, "");
          if (line === "") {
            dispatchBlock();
            continue;
          }
          if (line.startsWith(":")) continue; // SSE comment
          if (line.startsWith("event:")) {
            currentEvent = line.slice(6).trim();
          } else if (line.startsWith("data:")) {
            // Strip the single leading space SSE permits after "data:".
            dataLines.push(line.slice(5).replace(/^ /, ""));
          }
        }
      }
      // Flush any trailing block held in the buffer.
      if (buffer) {
        const line = buffer.replace(/\r$/, "");
        if (line.startsWith("event:")) currentEvent = line.slice(6).trim();
        else if (line.startsWith("data:")) dataLines.push(line.slice(5).replace(/^ /, ""));
      }
      dispatchBlock();

      if (!sawAgentRunResult) {
        setMessages((prev) => [
          ...prev,
          {
            id: crypto.randomUUID(),
            role: "assistant",
            content: "Agent run did not complete."
          }
        ]);
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
    const projectName = readActiveProjectName();
    if (!projectName) {
      setMessages((prev) => [
        ...prev,
        {
          id: crypto.randomUUID(),
          role: "assistant",
          content: "Select a project first — open the Projects page from the sidebar."
        }
      ]);
      setShowAnalyzeModal(false);
      return;
    }
    setIsCalling(true);
    const salt = saltInput.trim() || "AlCl3-KCl";
    const command = `MPLBACKEND=Agg python3 /mnt/skills/salt-analysis/scripts/analyze_salt.py --salt ${salt} --output-dir /mnt/data/output/salt-plots`;
    const t0 = performance.now();

    try {
      const response = await fetch("/api/mcp/call", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ project_name: projectName, tool, args: { command } })
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
            body: JSON.stringify({ project_name: projectName, tool: "display_file", args: { uri: plotPath } })
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
    const projectName = readActiveProjectName();
    if (!projectName) {
      setMessages((prev) => [
        ...prev,
        {
          id: crypto.randomUUID(),
          role: "assistant",
          content: "Select a project first — open the Projects page from the sidebar."
        }
      ]);
      setShowPredictModal(false);
      return;
    }
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
        body: JSON.stringify({ project_name: projectName, tool, args: { command } })
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
            body: JSON.stringify({ project_name: projectName, tool: "display_file", args: { uri: plotPath } })
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
    const projectName = readActiveProjectName();
    if (!projectName) {
      setMcpHealth({
        ok: false,
        mcpBaseUrl: "unknown",
        detail: "Select a project to probe the MCP connection."
      });
      return;
    }
    setIsCheckingHealth(true);
    try {
      const response = await fetch(
        `/api/mcp/health?project_name=${encodeURIComponent(projectName)}`
      );
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
    const projectName = readActiveProjectName();
    if (!projectName) {
      setMcpTools({
        ok: false,
        tools: [],
        error: "Select a project to list MCP tools."
      });
      return;
    }
    setIsLoadingTools(true);
    try {
      const response = await fetch(
        `/api/mcp/tools?project_name=${encodeURIComponent(projectName)}`
      );
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
            <span className="app-active-project-name">{activeProject.name}</span>
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
            <button
              className="quick-chip"
              disabled={messageHistory.length === 0}
              title={
                messageHistory.length === 0
                  ? "Have a conversation first; the skill is drafted from it."
                  : "Distill this conversation into a reusable SKILL.md"
              }
              onClick={() => void openSaveAsSkill()}
            >
              Save as skill…
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

      {pendingToolApproval && (
        <ToolApprovalModal
          id={pendingToolApproval.id}
          toolName={pendingToolApproval.toolName}
          message={pendingToolApproval.message}
          args={pendingToolApproval.args}
          decisionMetadata={pendingToolApproval.decisionMetadata}
          onSubmit={handleElicitationSubmit}
        />
      )}

      <SkillEditorModal
        open={showSkillEditor}
        initial={skillDraft}
        errorMessage={skillSaveError}
        onSave={saveSkill}
        onCancel={() => {
          setShowSkillEditor(false);
          setSkillDraft(null);
          setSkillSaveError(null);
        }}
      />
    </main>
  );
}
