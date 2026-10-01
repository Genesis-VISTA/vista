"use client";

import { useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { useRouter } from "next/navigation";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import SandboxedHtmlCard from "@/components/SandboxedHtmlCard";
import ElicitationModal from "@/components/ElicitationModal";
import ToolApprovalModal, { type DecisionMetadata } from "@/components/ToolApprovalModal";
import CampaignPanel from "@/components/CampaignPanel";
import { AppTopBar } from "@/components/AppTopBar";
import { ModelPicker } from "@/components/ModelPicker";
import { ImageLightbox } from "@/components/ImageLightbox";
import {
  SkillEditorModal,
  type SkillDraftFields,
  type SkillSavedPayload,
} from "@/components/SkillEditorModal";
import {
  ReportModal,
  type ReportDraft,
  type ReportSavePayload,
} from "@/components/ReportModal";
import { extractError } from "@/lib/user";
import type { ChatMessage, ExecutionResult } from "@/lib/types";
import { labelForTool } from "@/lib/tool-labels";
import { fileLinkProps } from "@/lib/file-links";
import {
  readActiveProjectName,
  useActiveProject,
  useActiveProjectName,
  useProjects,
  writeActiveProjectName,
  notifyActiveProjectChanged,
} from "@/lib/projects";
import { writePendingDestination } from "@/lib/pending-destination";
import { readAdditions, writeAdditions } from "@/lib/loaded-skills";
import {
  extractPlotPath,
  extractPredictionSummary,
  extractReferences,
  formatResultSummary,
  intermediatePreview,
} from "@/lib/result-parsing";
import {
  createPersistedChatSession,
  deletePersistedChatSession,
  fetchPersistedChatSession,
  listPersistedChatSessions,
  notifyActiveChatSessionChanged,
  PersistedChatSessionError,
  renamePersistedChatSession,
  savePersistedChatSession,
  type PersistedChatSessionSummary,
  useActiveChatSessionId,
  writeActiveChatSessionId,
} from "@/lib/chat-session";
import {
  htmlFromToolReturnContent,
  fileFromToolReturnContent,
  textFromToolReturnContent,
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

type WorkspaceTab = "artifacts" | "activity" | "jobs";

/**
 * Openers for an empty conversation. These prefill the composer rather than
 * sending, so the user can edit before committing — and unlike the salt
 * buttons they replace, they go through the agent like any other message.
 */
const WORKSPACE_TABS: Array<{ id: WorkspaceTab; label: string }> = [
  { id: "artifacts", label: "Artifacts" },
  { id: "activity", label: "Activity" },
  { id: "jobs", label: "Jobs" },
];

const SUGGESTIONS = [
  "What can you help me with in this project?",
  "Which tools and skills do you have available?",
  "What knowledge bases can you search?",
];

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

/**
 * The figure in the artifacts column, which opens itself full size.
 *
 * Its own component only because the surrounding JSX narrows `ui` by a
 * discriminant a click handler's closure no longer sees.
 */
function ArtifactImage({
  url,
  name,
  onZoom,
}: {
  url: string;
  name?: string;
  onZoom: (image: { src: string; alt: string }) => void;
}) {
  const alt = name ?? "Tool output";
  return (
    <button
      type="button"
      className="artifact-zoom"
      onClick={() => onZoom({ src: url, alt })}
      title="Show this figure full size"
    >
      <img src={url} alt={alt} />
    </button>
  );
}

export default function HomePage() {
  const mainRef = useRef<HTMLElement | null>(null);
  const outputSplitRef = useRef<HTMLDivElement | null>(null);
  const chatListRef = useRef<HTMLDivElement | null>(null);
  const [activeColumnResizer, setActiveColumnResizer] = useState<"right" | null>(null);
  const [vizWidth, setVizWidth] = useState(460);
  /**
   * Active project for the topbar badge. The hook uses
   * `useSyncExternalStore` so SSR and the first client paint both read
   * `null`, then React updates with the real value after hydration. Other
   * tabs / pages that write the active-project slug are picked up via the
   * hook's storage subscription.
   */
  const activeProject = useActiveProject();
  const activeProjectName = useActiveProjectName();
  const { loading: projectsLoading } = useProjects();
  const router = useRouter();

  /**
   * Chat is the only route that redirects. Every other page has something to
   * show without a project; this one is a conversation with nobody.
   *
   * The pointer's name is checked rather than the resolved project, because
   * the resolved value is also null while the list loads — redirecting on that
   * would bounce everyone to the picker on every cold load. A name that no
   * longer matches any project (someone deleted it) is cleared here too,
   * otherwise the page would wait forever for a project that is not coming.
   */
  useEffect(() => {
    // Read storage directly rather than trusting the hook's value here. On the
    // hydration render `useSyncExternalStore` hands back the *server* snapshot
    // (null) so server and client markup agree, and acting on that would
    // redirect every cold load to the picker. Effects only run on the client,
    // after hydration, so this read is the real one. The hook still drives the
    // dependency list, so a later switch re-runs this.
    const name = readActiveProjectName();
    // The pointer names a project that no longer exists.
    const stale = Boolean(name) && !projectsLoading && !activeProject;
    if (name && !stale) return;
    if (stale) {
      writeActiveProjectName(null);
      notifyActiveProjectChanged();
    }
    writePendingDestination("/");
    router.replace("/projects");
  }, [activeProjectName, activeProject, projectsLoading, router]);
  const activeChatSessionId = useActiveChatSessionId(activeProject?.name ?? null);
  const isConversationListView = !!activeProject && !activeChatSessionId;
  const isConversationOpen = !!activeProject && !!activeChatSessionId;

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [chatSessions, setChatSessions] = useState<PersistedChatSessionSummary[]>([]);
  const [chatSessionsLoading, setChatSessionsLoading] = useState(false);
  const [chatSessionsError, setChatSessionsError] = useState<string | null>(null);
  const [activeChatSessionTitle, setActiveChatSessionTitle] = useState<string | null>(null);
  const [editingChatSessionId, setEditingChatSessionId] = useState<string | null>(null);
  const [draftChatSessionTitle, setDraftChatSessionTitle] = useState("");
  const [deleteSessionTarget, setDeleteSessionTarget] = useState<PersistedChatSessionSummary | null>(null);
  /**
   * Raw PydanticAI `ModelMessage` history — accumulated across turns from each
   * `agent_run_result.new_messages`. The backend now owns the canonical
   * session history for chat runs; we still keep this client copy for UI
   * restore, local features like "Save as skill", and Phase 1/2 compatibility.
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
  const [isSessionHydrated, setIsSessionHydrated] = useState(false);
  const hydratedSessionKeyRef = useRef<string | null>(null);
  const lastPersistedSnapshotRef = useRef<string | null>(null);
  /**
   * A conversation this client created a moment ago, mid-send.
   *
   * Creating one changes `activeChatSessionId`, which is also the signal the
   * hydration effect below uses to load a conversation you switched to. There
   * is nothing to load from a session created seconds ago — the backend has
   * only the empty record it just made — so hydrating it would replace the
   * message the user is in the middle of sending with an empty array.
   */
  const locallyCreatedSessionIdRef = useRef<string | null>(null);
  const [latestResult, setLatestResult] = useState<ExecutionResult | null>(null);

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
    setLatestResult(null);
    setLatestIntermediateId(null);
    setExpandedIntermediates(new Set());
  }, [activeProject?.id]);

  useEffect(() => {
    const projectName = activeProject?.name ?? null;
    if (!projectName) {
      hydratedSessionKeyRef.current = null;
      lastPersistedSnapshotRef.current = null;
      setActiveChatSessionTitle(null);
      setIsSessionHydrated(false);
      return;
    }
    if (!activeChatSessionId) {
      hydratedSessionKeyRef.current = null;
      lastPersistedSnapshotRef.current = null;
      setActiveChatSessionTitle(null);
      setMessages([]);
      setMessageHistory([]);
      setLatestResult(null);
      setLatestIntermediateId(null);
      setExpandedIntermediates(new Set());
      setIsSessionHydrated(false);
      return;
    }

    const sessionKey = `${projectName}:${activeChatSessionId ?? ""}`;

    // A conversation this client just created carries the turn being sent.
    // Fetching it back would hand us the empty record the backend made and
    // wipe that turn out of the thread — and the save below would then
    // persist the thread without it, so reopening the conversation would show
    // an answer with no question.
    if (activeChatSessionId && activeChatSessionId === locallyCreatedSessionIdRef.current) {
      locallyCreatedSessionIdRef.current = null;
      hydratedSessionKeyRef.current = sessionKey;
      // Nothing is persisted for it yet, so the next change must save.
      lastPersistedSnapshotRef.current = null;
      setIsSessionHydrated(true);
      return;
    }

    let cancelled = false;
    setIsSessionHydrated(false);

    void (async () => {
      try {
        const persisted = await fetchPersistedChatSession(projectName, activeChatSessionId);
        if (cancelled) return;
        if (persisted.id !== activeChatSessionId) {
          writeActiveChatSessionId(projectName, persisted.id);
          notifyActiveChatSessionChanged();
        }
        setActiveChatSessionTitle(persisted.title);
        const restoredMessages = Array.isArray(persisted.messages) ? persisted.messages : [];
        const restoredHistory = Array.isArray(persisted.message_history) ? persisted.message_history : [];
        const restoredLatestResult = persisted.latest_result ?? null;
        setMessages(restoredMessages);
        setMessageHistory(restoredHistory);
        setLatestResult(restoredLatestResult);
        setLatestIntermediateId(null);
        setExpandedIntermediates(new Set());
        lastPersistedSnapshotRef.current = JSON.stringify({
          messages: restoredMessages,
          messageHistory: restoredHistory,
          latestResult: restoredLatestResult,
        });
      } catch (error) {
        if (cancelled) return;
        if (error instanceof PersistedChatSessionError && error.status === 404) {
          writeActiveChatSessionId(projectName, null);
          notifyActiveChatSessionChanged();
        }
        setActiveChatSessionTitle(null);
        setMessages([]);
        setMessageHistory([]);
        setLatestResult(null);
        setLatestIntermediateId(null);
        setExpandedIntermediates(new Set());
        lastPersistedSnapshotRef.current = JSON.stringify({
          messages: [],
          messageHistory: [],
          latestResult: null,
        });
      } finally {
        if (!cancelled) {
          hydratedSessionKeyRef.current = sessionKey;
          setIsSessionHydrated(true);
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [activeProject?.id, activeProject?.name, activeChatSessionId]);

  useEffect(() => {
    const projectName = activeProject?.name ?? null;
    if (!projectName || !isConversationListView) return;

    let cancelled = false;
    setChatSessionsLoading(true);
    setChatSessionsError(null);

    void listPersistedChatSessions(projectName)
      .then((sessions) => {
        if (!cancelled) setChatSessions(sessions);
      })
      .catch((error) => {
        if (!cancelled) {
          setChatSessions([]);
          setChatSessionsError(error instanceof Error ? error.message : "Failed to load conversations.");
        }
      })
      .finally(() => {
        if (!cancelled) setChatSessionsLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [activeProject?.name, isConversationListView]);

  useEffect(() => {
    const projectName = activeProject?.name ?? null;
    if (!projectName || !isSessionHydrated) return;
    if (hydratedSessionKeyRef.current !== `${projectName}:${activeChatSessionId ?? ""}`) return;
    if (messageHistory.length === 0 && messages.length === 0 && latestResult == null) return;

    const snapshot = JSON.stringify({ messages, messageHistory, latestResult });
    if (snapshot === lastPersistedSnapshotRef.current) return;

    lastPersistedSnapshotRef.current = snapshot;
    void savePersistedChatSession(projectName, {
      chatSessionId: activeChatSessionId,
      messages,
      messageHistory,
      latestResult,
    }).catch(() => {
      // Best-effort persistence for Phase 1. A failed save should not break the live chat.
      if (lastPersistedSnapshotRef.current === snapshot) {
        lastPersistedSnapshotRef.current = null;
      }
    });
  }, [activeProject?.name, activeChatSessionId, isSessionHydrated, messages, messageHistory, latestResult]);

  // Save-as-skill state. `initial: null` while the LLM is drafting; the
  // modal swaps into edit mode once the draft arrives. We keep an error
  // banner separately so we can show backend rejections (duplicate slug,
  // malformed name) without dropping the user's in-flight edits.
  const [showSkillEditor, setShowSkillEditor] = useState(false);
  const [skillDraft, setSkillDraft] = useState<SkillDraftFields | null>(null);
  const [skillSaveError, setSkillSaveError] = useState<string | null>(null);

  // Generate-report state. `reportDraft: null` while a draft is in flight.
  // `reportRequestRef` numbers each draft request so a slow earlier one (the
  // user hit Regenerate) cannot overwrite the draft that replaced it.
  const [showReport, setShowReport] = useState(false);
  const [reportDraft, setReportDraft] = useState<ReportDraft | null>(null);
  const [reportError, setReportError] = useState<string | null>(null);
  const [reportSavedPath, setReportSavedPath] = useState<string | null>(null);
  const reportRequestRef = useRef(0);

  const [isCalling, setIsCalling] = useState(false);
  const [mcpHealth, setMcpHealth] = useState<McpHealth | null>(null);
  const [isCheckingHealth, setIsCheckingHealth] = useState(false);
  const [mcpTools, setMcpTools] = useState<McpToolsResponse | null>(null);
  const [isLoadingTools, setIsLoadingTools] = useState(false);
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
  const [showJumpToLatest, setShowJumpToLatest] = useState(false);
  /**
   * What the agent is doing right now, in one line.
   *
   * Replaces the stack of "Calling `x`…" bubbles the thread used to grow. The
   * steps themselves still go into `messages` so they persist and feed the
   * Activity tab; this is only what the conversation shows while a run is open.
   */
  const [liveStatus, setLiveStatus] = useState<string | null>(null);
  const [workspaceTab, setWorkspaceTab] = useState<WorkspaceTab>("artifacts");
  const [lightbox, setLightbox] = useState<{ src: string; alt: string } | null>(null);
  const [showRawLog, setShowRawLog] = useState(false);
  const [hasCampaign, setHasCampaign] = useState(false);
  const [agentLogs, setAgentLogs] = useState<LogEntry[]>([]);
  const logEndRef = useRef<HTMLDivElement | null>(null);
  const latestReferences = useMemo(() => extractReferences(latestResult), [latestResult]);
  /** What the conversation shows: user turns and final answers. */
  const conversationMessages = useMemo(
    () => messages.filter((msg) => !msg.intermediate),
    [messages]
  );
  /** What the Activity tab shows: every step the run took. */
  const activitySteps = useMemo(
    () => messages.filter((msg) => msg.intermediate),
    [messages]
  );
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
   * Draft a report of the open conversation into the report modal. Used both
   * to open it and to regenerate with a focus `hint`; either way the modal
   * shows its drafting state until `/reports/generate` returns.
   */
  async function draftReport(hint?: string) {
    const projectName = activeProject?.name;
    if (!projectName) return;
    const request = ++reportRequestRef.current;
    setReportDraft(null);
    setReportError(null);
    setShowReport(true);
    let draft: ReportDraft = { title: "", slug_suggestion: "", summary: "", body: "" };
    let error: string | null = null;
    try {
      const resp = await fetch(
        `/api/projects/${encodeURIComponent(projectName)}/reports/generate`,
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ message_history: messageHistory, hint: hint || null }),
        }
      );
      if (resp.ok) draft = (await resp.json()) as ReportDraft;
      else error = await extractError(resp);
    } catch (err) {
      error = err instanceof Error ? err.message : "Drafting failed.";
    }
    if (request !== reportRequestRef.current) return;
    setReportError(error);
    setReportDraft(draft);
  }

  function openGenerateReport() {
    setReportSavedPath(null);
    void draftReport();
  }

  function closeReport() {
    reportRequestRef.current++;
    setShowReport(false);
    setReportDraft(null);
    setReportError(null);
    setReportSavedPath(null);
  }

  /**
   * Save the report to the project's uploads. The backend keys it on this
   * chat's session id, so saving again replaces the same file.
   */
  async function saveReport(payload: ReportSavePayload) {
    const projectName = activeProject?.name;
    if (!projectName || !activeChatSessionId) return;
    setReportError(null);
    try {
      const resp = await fetch(`/api/projects/${encodeURIComponent(projectName)}/reports`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ ...payload, chat_session_id: activeChatSessionId }),
      });
      if (!resp.ok) {
        setReportError(await extractError(resp));
        return;
      }
      const saved = (await resp.json()) as { path: string };
      setReportSavedPath(saved.path);
    } catch (err) {
      setReportError(err instanceof Error ? err.message : "Save failed.");
    }
  }

  /** Hand the report off to skill drafting: the report steers the skill draft. */
  function saveReportAsSkill(body: string) {
    closeReport();
    void openSaveAsSkill(body);
  }

  /**
   * Open the Save-as-Skill modal and kick off the LLM draft in parallel.
   * The modal opens immediately in a "Drafting…" state and switches to the
   * editable form once `/api/skills/generate` returns. `hint` (the report, when
   * coming from the report modal) steers the draft.
   */
  async function openSaveAsSkill(hint?: string) {
    setSkillDraft(null);
    setSkillSaveError(null);
    setShowSkillEditor(true);
    try {
      const resp = await fetch("/api/skills/generate", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ message_history: messageHistory, hint: hint || null }),
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
    setLiveStatus("Working on it");

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

    let targetChatSessionId = activeChatSessionId;
    if (!targetChatSessionId) {
      try {
        const created = await createPersistedChatSession(projectName, {
          title: text.slice(0, 60),
        });
        targetChatSessionId = created.id;
        // Claim it before the pointer moves, so the hydration effect knows not
        // to fetch this one back over the turn being sent.
        locallyCreatedSessionIdRef.current = created.id;
        setChatSessions((prev) => [created, ...prev.filter((session) => session.id !== created.id)]);
        setActiveChatSessionTitle(created.title);
        writeActiveChatSessionId(projectName, created.id);
        notifyActiveChatSessionChanged();
      } catch {
        setMessages((prev) => [
          ...prev,
          {
            id: crypto.randomUUID(),
            role: "assistant",
            content: "Could not create a new conversation."
          }
        ]);
        return;
      }
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
          // Still recorded as a step: `messages` is what gets persisted, so it
          // is also what the Activity tab can show after a reload.
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
          setLiveStatus(labelForTool(toolName));
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
          setLiveStatus("Working on it");
          if (result.part_kind === "tool-return" || result.part_kind === "builtin-tool-return") {
            const file = result.tool_name === "display_file" ? fileFromToolReturnContent(result.content) : null;
            const html = file ? null : htmlFromToolReturnContent(result.content);
            const ui = file
              ? ({ kind: "file", url: file.url, mimeType: file.mimeType, name: file.name } as const)
              : html
                ? ({ kind: "html", html } as const)
                : null;
            // The text matters even when there is nothing to render: it is
            // what the prediction summary and references panels read. Before
            // this it was dropped, and those panels only ever filled from the
            // salt quick-actions, which called MCP directly.
            const stdout = file || html ? "" : (textFromToolReturnContent(result.content) ?? "");
            if (ui || stdout) {
              setLatestResult((prev) => ({
                ok: true,
                // Keep text from an earlier tool in the same turn when this
                // one only produced a figure, so a run that plots *and*
                // reports does not lose half of itself.
                stdout: stdout || prev?.stdout || "",
                stderr: "",
                artifacts: [],
                meta: { tool: result.tool_name },
                ui: ui ?? prev?.ui
              }));
            }
          }
          break;
        }

        case "agent_run_result": {
          const ev = data as AgentRunResultEvent;
          sawAgentRunResult = true;
          setLiveStatus(null);
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
          chat_session_id: targetChatSessionId,
          user_prompt: text,
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
      setLiveStatus(null);
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
        `/api/mcp/health?project_name=${encodeURIComponent(projectName)}${
          activeChatSessionId ? `&chat_session_id=${encodeURIComponent(activeChatSessionId)}` : ""
        }`
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
        `/api/mcp/tools?project_name=${encodeURIComponent(projectName)}${
          activeChatSessionId ? `&chat_session_id=${encodeURIComponent(activeChatSessionId)}` : ""
        }`
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

  async function handleCreateConversation() {
    const projectName = activeProject?.name ?? null;
    if (!projectName) return;
    try {
      const created = await createPersistedChatSession(projectName);
      setChatSessions((prev) => [created, ...prev.filter((session) => session.id !== created.id)]);
      setActiveChatSessionTitle(created.title);
      writeActiveChatSessionId(projectName, created.id);
      notifyActiveChatSessionChanged();
    } catch (error) {
      setChatSessionsError(
        error instanceof Error ? error.message : "Failed to create conversation."
      );
    }
  }

  function handleOpenConversation(chatSession: PersistedChatSessionSummary) {
    const projectName = activeProject?.name ?? null;
    if (!projectName) return;
    setActiveChatSessionTitle(chatSession.title);
    writeActiveChatSessionId(projectName, chatSession.id);
    notifyActiveChatSessionChanged();
  }

  function handleBackToConversationList() {
    const projectName = activeProject?.name ?? null;
    if (!projectName) return;
    writeActiveChatSessionId(projectName, null);
    notifyActiveChatSessionChanged();
  }

  async function handleRenameConversation() {
    const projectName = activeProject?.name ?? null;
    if (!projectName || !editingChatSessionId) return;
    const sessionToRename = chatSessions.find((session) => session.id === editingChatSessionId);
    if (!sessionToRename) return;
    const nextTitle = draftChatSessionTitle.trim();
    if (!nextTitle || nextTitle === sessionToRename.title) {
      setEditingChatSessionId(null);
      return;
    }
    try {
      const updated = await renamePersistedChatSession(projectName, editingChatSessionId, nextTitle);
      setChatSessions((prev) =>
        prev.map((session) =>
          session.id === updated.id ? { ...session, title: updated.title, updated_at: updated.updated_at } : session
        )
      );
      if (activeChatSessionId === updated.id) {
        setActiveChatSessionTitle(updated.title);
      }
      setEditingChatSessionId(null);
    } catch (error) {
      setChatSessionsError(
        error instanceof Error ? error.message : "Failed to rename conversation."
      );
    }
  }

  async function handleDeleteConversation() {
    const projectName = activeProject?.name ?? null;
    if (!projectName || !deleteSessionTarget) return;
    try {
      await deletePersistedChatSession(projectName, deleteSessionTarget.id);
      setChatSessions((prev) => prev.filter((session) => session.id !== deleteSessionTarget.id));
      setDeleteSessionTarget(null);
    } catch (error) {
      setChatSessionsError(
        error instanceof Error ? error.message : "Failed to delete conversation."
      );
    }
  }

  function beginInlineRename(chatSession: PersistedChatSessionSummary) {
    setEditingChatSessionId(chatSession.id);
    setDraftChatSessionTitle(chatSession.title);
  }

  return (
    <main
      ref={mainRef}
      style={
        {
          "--viz-width": `${vizWidth}px`
        } as CSSProperties
      }
    >
      <AppTopBar
        title={isConversationListView ? "Conversations" : "Chat"}
        actions={
          <div className="chat-header-actions">
            {isConversationListView ? (
              <button
                className="conversation-action-button primary"
                onClick={() => void handleCreateConversation()}
                title="New conversation"
                aria-label="New conversation"
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d="M12 5v14" />
                  <path d="M5 12h14" />
                </svg>
                <span>New conversation</span>
              </button>
            ) : isConversationOpen ? (
              <div className="chat-header-secondary-actions">
                <button
                  className="conversation-back-button"
                  onClick={handleBackToConversationList}
                  title="Back to conversations"
                  aria-label="Back to conversations"
                >
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="m15 18-6-6 6-6" />
                  </svg>
                  <span>Back to conversations</span>
                </button>
                <button
                  className="conversation-back-button"
                  disabled={messageHistory.length === 0}
                  title={
                    messageHistory.length === 0
                      ? "Have a conversation first; the report is written from it."
                      : "Write up this conversation as a report you can save or turn into a skill"
                  }
                  onClick={openGenerateReport}
                >
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8Z" />
                    <path d="M14 3v5h5" />
                    <path d="M9 13h6" />
                    <path d="M9 17h6" />
                  </svg>
                  <span>Generate report</span>
                </button>
              </div>
            ) : null}
            <ModelPicker />
          </div>
        }
      />

      <div className="workspace">

      <section className="panel" style={{ minHeight: 0 }}>
        <div className="panel-body" style={{ flex: 1, position: "relative", overflow: "hidden" }}>
          {isConversationListView ? (
            <div className="chat-list conversation-list-view">
              {!activeProject && (
                <div className="chat-bubble">
                  Select a project first to see its conversations.
                </div>
              )}
              {activeProject && chatSessionsLoading && (
                <div className="chat-bubble">Loading conversations…</div>
              )}
              {activeProject && !chatSessionsLoading && chatSessionsError && (
                <div className="chat-bubble error">{chatSessionsError}</div>
              )}
              {activeProject && !chatSessionsLoading && !chatSessionsError && chatSessions.length === 0 && (
                <div className="chat-bubble">
                  No conversations yet. Create a new conversation to get started.
                </div>
              )}
              {chatSessions.map((chatSession) => (
                <div key={chatSession.id} className="conversation-list-item">
                  {editingChatSessionId === chatSession.id ? (
                    <div className="conversation-list-open conversation-list-open-static">
                      <input
                        className="input conversation-list-title-input"
                        value={draftChatSessionTitle}
                        onChange={(event) => setDraftChatSessionTitle(event.target.value)}
                        onKeyDown={(event) => {
                          if (event.key === "Enter") {
                            event.preventDefault();
                            void handleRenameConversation();
                          }
                          if (event.key === "Escape") {
                            setEditingChatSessionId(null);
                          }
                        }}
                        autoFocus
                      />
                      <span className="conversation-list-date">
                        {new Date(chatSession.updated_at).toLocaleString()}
                      </span>
                    </div>
                  ) : (
                    <button
                      type="button"
                      className="conversation-list-open"
                      onClick={() => handleOpenConversation(chatSession)}
                    >
                      <span className="conversation-list-title">{chatSession.title}</span>
                      <span className="conversation-list-date">
                        {new Date(chatSession.updated_at).toLocaleString()}
                      </span>
                    </button>
                  )}
                  {editingChatSessionId === chatSession.id ? (
                    <>
                      <button
                        type="button"
                        className="conversation-action-button primary"
                        onClick={() => void handleRenameConversation()}
                      >
                        Save
                      </button>
                      <button
                        type="button"
                        className="conversation-action-button"
                        onClick={() => setEditingChatSessionId(null)}
                      >
                        Cancel
                      </button>
                    </>
                  ) : (
                    <button
                      type="button"
                      className="conversation-list-edit"
                      title="Edit conversation name"
                      aria-label="Edit conversation name"
                      onClick={() => beginInlineRename(chatSession)}
                    >
                      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                        <path d="M12 20h9" />
                        <path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z" />
                      </svg>
                    </button>
                  )}
                  <button
                    type="button"
                    className="conversation-list-delete"
                    title="Delete conversation"
                    aria-label="Delete conversation"
                    onClick={() => setDeleteSessionTarget(chatSession)}
                  >
                    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                      <path d="M3 6h18" />
                      <path d="M8 6V4h8v2" />
                      <path d="M19 6l-1 14H6L5 6" />
                      <path d="M10 11v6" />
                      <path d="M14 11v6" />
                    </svg>
                  </button>
                </div>
              ))}
            </div>
          ) : isConversationOpen ? (
            <>
              <div ref={chatListRef} className="chat-list" onScroll={handleChatScroll}>
                {activeChatSessionTitle && (
                  <div className="chat-session-banner">{activeChatSessionTitle}</div>
                )}
                {messages.length === 0 && (
                  <div className="chat-opener">
                    <p className="chat-opener-lede">Ask a question to get started.</p>
                    <div className="chat-opener-chips">
                      {SUGGESTIONS.map((suggestion) => (
                        <button
                          key={suggestion}
                          type="button"
                          className="quick-chip"
                          onClick={() => setInput(suggestion)}
                        >
                          {suggestion}
                        </button>
                      ))}
                    </div>
                  </div>
                )}
                {/* User turns and final answers only. Every intermediate step
                    is in the Activity tab, where there is room to say what it
                    was. */}
                {conversationMessages.map((msg) => (
                  <div key={msg.id} className={`chat-bubble ${msg.role}`}>
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
                ))}
                {/* One line, updated in place, gone when the answer lands. */}
                {isChatLoading && (
                  <div className="chat-bubble assistant thinking" role="status" aria-live="polite">
                    <span className="thinking-loader" aria-hidden="true">
                      <span />
                      <span />
                      <span />
                    </span>
                    <span>{liveStatus ?? "Working on it"}…</span>
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
            </>
          ) : (
            <div className="chat-list conversation-list-view">
              <div className="chat-bubble">
                Select a project first to open its conversations.
              </div>
            </div>
          )}
        </div>
        {(isConversationOpen || isConversationListView) && (
          <div className="chat-input-row">
            <input
              className="input"
              placeholder="Ask a question… (e.g., 'What can you help me with?')"
              value={input}
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  void sendUserMessage();
                }
              }}
            />
            <div className="composer-actions">
              {isChatLoading && <span className="composer-status">Agent working…</span>}
              <div className="composer-spacer" />
              <button
                className={`composer-send${isConversationListView ? " labelled" : ""}`}
                onClick={() => void sendUserMessage()}
                disabled={isChatLoading}
                title={isConversationListView ? "Start chat" : "Send"}
                aria-label={isConversationListView ? "Start chat" : "Send"}
              >
                {isConversationListView ? (
                  <span>Start chat</span>
                ) : (
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="M5 12h13" />
                    <path d="M13 6l6 6-6 6" />
                  </svg>
                )}
              </button>
            </div>
          </div>
        )}
      </section>

      <div
        className="panel-resizer"
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize output panel"
        onPointerDown={() => setActiveColumnResizer("right")}
      />

      <section className="panel">
        {/* Tabs, not two stacked panes. The Jobs tab appears only when the
            conversation has a campaign — most never do, and a permanently
            empty tab reads worse than an absent one. */}
        <div className="panel-header workspace-tabs" role="tablist" aria-label="Workspace">
          {WORKSPACE_TABS.filter((tab) => tab.id !== "jobs" || hasCampaign).map((tab) => (
            <button
              key={tab.id}
              type="button"
              role="tab"
              id={`workspace-tab-${tab.id}`}
              aria-selected={workspaceTab === tab.id}
              aria-controls={`workspace-panel-${tab.id}`}
              className="workspace-tab"
              data-active={workspaceTab === tab.id ? "true" : "false"}
              onClick={() => setWorkspaceTab(tab.id)}
            >
              {tab.label}
              {tab.id === "activity" && activitySteps.length > 0 && (
                <span className="workspace-tab-count">{activitySteps.length}</span>
              )}
            </button>
          ))}
        </div>
        <div className="panel-body">
          {/* Mounted regardless of the visible tab: it owns the polling that
              decides whether the Jobs tab exists at all. */}
          <div hidden={workspaceTab !== "jobs"}>
            <CampaignPanel
              projectName={activeProject?.name ?? null}
              chatSessionId={activeChatSessionId}
              onPresenceChange={setHasCampaign}
            />
          </div>

          <div
            role="tabpanel"
            id="workspace-panel-activity"
            aria-labelledby="workspace-tab-activity"
            hidden={workspaceTab !== "activity"}
            className="activity-view"
          >
            {activitySteps.length === 0 && !isChatLoading && (
              <div className="activity-empty">
                Nothing yet. Steps appear here while the agent works.
              </div>
            )}
            {activitySteps.map((step) => {
              // A tool call is one line and says everything it has to say.
              // Only a longer step — a mid-campaign report, say — is worth
              // collapsing, and then the newest one opens on its own.
              const hasDetail = step.content.trim().includes("\n");
              const expanded =
                !hasDetail ||
                step.id === latestIntermediateId ||
                expandedIntermediates.has(step.id);
              return (
                <div key={step.id} className="activity-step" data-expanded={expanded}>
                  <span className="activity-step-dot" aria-hidden="true" />
                  <div className="activity-step-body">
                    {hasDetail ? (
                      <>
                        <button
                          type="button"
                          className="activity-step-summary"
                          aria-expanded={expanded}
                          onClick={() => toggleIntermediate(step.id)}
                        >
                          {intermediatePreview(step.content)}
                        </button>
                        {expanded && (
                          <div className="activity-step-detail">
                            <ReactMarkdown remarkPlugins={[remarkGfm]}>{step.content}</ReactMarkdown>
                          </div>
                        )}
                      </>
                    ) : (
                      <div className="activity-step-line">
                        <ReactMarkdown remarkPlugins={[remarkGfm]}>{step.content}</ReactMarkdown>
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
            {isChatLoading && liveStatus && (
              <div className="activity-step activity-step-live">
                <span className="activity-step-dot live" aria-hidden="true" />
                <div className="activity-step-body">{liveStatus}…</div>
              </div>
            )}

            <div className="activity-raw">
              <button
                type="button"
                className="activity-raw-toggle"
                aria-expanded={showRawLog}
                onClick={() => setShowRawLog((prev) => !prev)}
              >
                {showRawLog ? "Hide raw log" : "Show raw log"}
                {agentLogs.length > 0 && ` (${agentLogs.length})`}
              </button>
              {showRawLog && (
                <>
                  <div className="activity-raw-actions">
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
                      <div className="log-empty">
                        The raw log is live only. It is empty after a reload.
                      </div>
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
                </>
              )}
            </div>
          </div>

          <div
            role="tabpanel"
            id="workspace-panel-artifacts"
            aria-labelledby="workspace-tab-artifacts"
            hidden={workspaceTab !== "artifacts"}
            className="artifacts-view"
          >
            <div className="output-split" ref={outputSplitRef}>
            <div className="output-top">
              {!latestResult && <div className="chat-bubble">No figure yet.</div>}
              {latestResult && latestResult.ui?.kind === "html" && (
                <SandboxedHtmlCard html={latestResult.ui.html} />
              )}
              {latestResult && latestResult.ui?.kind === "file" && (
                latestResult.ui.mimeType?.startsWith("image/") ? (
                  <ArtifactImage
                    url={latestResult.ui.url}
                    name={latestResult.ui.name}
                    onZoom={setLightbox}
                  />
                ) : (
                  <a href={latestResult.ui.url} {...fileLinkProps(latestResult.ui.url)} className="chat-bubble">
                    Download {latestResult.ui.name ?? "file"}
                  </a>
                )
              )}
              {latestResult && latestResult.ui?.kind !== "html" && latestResult.ui?.kind !== "file" && (
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
            </div>
          </div>
        </div>
        </section>
      </div>

      {deleteSessionTarget && (
        <div className="modal-backdrop" onClick={() => setDeleteSessionTarget(null)}>
          <div className="modal" onClick={(event) => event.stopPropagation()}>
            <div className="panel-header">
              <div className="panel-title">Delete Conversation</div>
              <button className="button ghost" onClick={() => setDeleteSessionTarget(null)}>
                Close
              </button>
            </div>
            <div className="modal-body">
              <p>
                Delete <strong>{deleteSessionTarget.title}</strong>? This conversation history will be removed.
              </p>
              <div style={{ display: "flex", gap: 10 }}>
                <button className="button ghost" onClick={() => setDeleteSessionTarget(null)}>
                  Cancel
                </button>
                <button className="button secondary" onClick={() => void handleDeleteConversation()}>
                  Delete conversation
                </button>
              </div>
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

      <ReportModal
        open={showReport}
        projectName={activeProject?.name ?? ""}
        draft={reportDraft}
        errorMessage={reportError}
        savedPath={reportSavedPath}
        onRegenerate={(hint) => void draftReport(hint)}
        onSave={saveReport}
        onSaveAsSkill={saveReportAsSkill}
        onClose={closeReport}
      />

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

      {lightbox && (
        <ImageLightbox
          src={lightbox.src}
          alt={lightbox.alt}
          onClose={() => setLightbox(null)}
        />
      )}
    </main>
  );
}
