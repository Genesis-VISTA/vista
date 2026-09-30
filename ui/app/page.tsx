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
  attachChatRun,
  createPersistedChatSession,
  deletePersistedChatSession,
  fetchPersistedChatSession,
  listPersistedChatSessions,
  notifyActiveChatSessionChanged,
  PersistedChatSessionError,
  readActiveChatSessionId,
  renamePersistedChatSession,
  savePersistedChatSession,
  stopChatRun,
  type PersistedChatSession,
  type PersistedChatSessionSummary,
  useActiveChatSessionId,
  writeActiveChatSessionId,
} from "@/lib/chat-session";
import type { ModelMessage } from "@/lib/agent-events";
import { readSseStream } from "@/lib/run-stream";
import {
  createRunRenderer,
  type RunHost,
  type RunPrompt,
  type RunRenderer,
} from "@/lib/run-renderer";
import { refreshChatRunStatus } from "@/lib/chat-run-status";

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
   * Raw PydanticAI `ModelMessage` history, read from the backend when a
   * conversation opens and again when a run ends. The backend is the only writer
   * of it; the page keeps a copy for features like "Save as skill" and never
   * sends it back.
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
  /**
   * The stream the page is watching a run through, and which conversation it
   * belongs to. Aborting it only stops watching: the run belongs to the backend.
   */
  const streamRef = useRef<{ controller: AbortController; conversationId: string } | null>(
    null
  );
  /**
   * The researcher has now seen the last run, so the next save also
   * acknowledges it, which clears its dot and the events kept for drawing it.
   * It rides the save that persists the drawn thread, so the events are only
   * deleted once the transcript that replaces them is stored.
   */
  const [ackPending, setAckPending] = useState(false);
  /** The latest `resumeRun`, so the hydration effect can call it without re-running when it changes. */
  const resumeRunRef = useRef<typeof resumeRun | null>(null);
  useEffect(() => {
    resumeRunRef.current = resumeRun;
  });
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
    setAgentLogs([]);

    void (async () => {
      let restored: PersistedChatSession | null = null;
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
          latestResult: restoredLatestResult,
        });
        restored = persisted;
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
          latestResult: null,
        });
      } finally {
        if (!cancelled) {
          hydratedSessionKeyRef.current = sessionKey;
          setIsSessionHydrated(true);
        }
      }
      // What the conversation was doing while it was away: a run still going,
      // or one that finished with nobody watching.
      if (!cancelled && restored) {
        await resumeRunRef.current?.(projectName, activeChatSessionId, restored);
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
    const ack = ackPending;
    if (!ack && messages.length === 0 && latestResult == null) return;

    // Model history is not part of this: only the backend writes it.
    const snapshot = JSON.stringify({ messages, latestResult });
    if (!ack && snapshot === lastPersistedSnapshotRef.current) return;

    lastPersistedSnapshotRef.current = snapshot;
    if (ack) setAckPending(false);
    void savePersistedChatSession(projectName, {
      chatSessionId: activeChatSessionId,
      messages,
      latestResult,
      ackRun: ack,
    })
      .then(() => {
        if (ack) void refreshChatRunStatus(projectName);
      })
      .catch(() => {
        // Best-effort persistence. A failed save should not break the live chat.
        if (lastPersistedSnapshotRef.current === snapshot) {
          lastPersistedSnapshotRef.current = null;
        }
        if (ack) setAckPending(true);
      });
  }, [
    activeProject?.name,
    activeChatSessionId,
    isSessionHydrated,
    messages,
    latestResult,
    ackPending,
  ]);

  // Switching conversation, or leaving the chat page, stops watching a run. The
  // run carries on in the backend, and opening its conversation again re-attaches.
  useEffect(() => {
    const stream = streamRef.current;
    if (stream && stream.conversationId !== activeChatSessionId) {
      abortRunStream();
      setIsRunActive(false);
      setLiveStatus(null);
      setPendingElicitation(null);
      setPendingToolApproval(null);
    }
  }, [activeChatSessionId]);

  useEffect(() => () => abortRunStream(), []);

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
  /** A run is active in the open conversation, whoever started it. */
  const [isRunActive, setIsRunActive] = useState(false);
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

  /** Stop watching the run, if any. The run itself carries on in the backend. */
  function abortRunStream() {
    const stream = streamRef.current;
    if (!stream) return;
    stream.controller.abort();
    streamRef.current = null;
  }

  function pushLog(level: string, area: string, message: string) {
    setAgentLogs((prev) => [
      ...prev,
      { id: crypto.randomUUID(), ts: new Date().toISOString(), level, area, message }
    ]);
  }

  /** Put a prompt the run is waiting on in front of the researcher. */
  function showPrompt(prompt: RunPrompt) {
    if (prompt.kind === "form") {
      setPendingElicitation({ id: prompt.id, message: prompt.message, schema: prompt.schema });
    } else if (prompt.kind === "approval") {
      setPendingToolApproval({
        id: prompt.id,
        toolName: prompt.toolName,
        message: prompt.message,
        args: prompt.args,
        decisionMetadata: prompt.decisionMetadata
      });
    } else {
      // v1: synchronous confirm. The backend is waiting for a POST to
      // /api/chat/elicitation, so briefly blocking the UI is acceptable. Refine
      // to a proper modal when the URL flow gets first-class UX.
      const allow = window.confirm(
        `${prompt.message}\n\nAllow the agent to open:\n${prompt.url}`
      );
      void handleElicitationSubmit(prompt.id, allow ? "accept" : "cancel");
      if (allow) window.open(prompt.url, "_blank", "noopener,noreferrer");
    }
  }

  /**
   * Applies a run's events to the open conversation, and only while that
   * conversation is still the one open and the stream is still wanted. After the
   * researcher switches away, a late event is dropped rather than drawn into the
   * wrong thread.
   */
  function makeRunHost(conversationId: string, controller: AbortController): RunHost {
    const live = () =>
      !controller.signal.aborted &&
      readActiveChatSessionId(readActiveProjectName()) === conversationId;
    return {
      setMessages: (update) => {
        if (live()) setMessages(update);
      },
      setLatestIntermediateId: (update) => {
        if (live()) setLatestIntermediateId(update);
      },
      setLiveStatus: (status) => {
        if (live()) setLiveStatus(status);
      },
      setLatestResult: (update) => {
        if (live()) setLatestResult(update);
      },
      pushLog: (level, area, message) => {
        if (live()) pushLog(level, area, message);
      },
      prompt: (prompt) => {
        if (live()) showPrompt(prompt);
      },
      promptResolved: (id) => {
        if (!live()) return;
        setPendingElicitation((current) => (current?.id === id ? null : current));
        setPendingToolApproval((current) => (current?.id === id ? null : current));
      },
      scrollToLatest: () => requestAnimationFrame(() => scrollChatToLatest("smooth"))
    };
  }

  /** The watch is over, however it ended. */
  function endRun(controller: AbortController) {
    if (streamRef.current?.controller === controller) streamRef.current = null;
    // Aborted means the researcher moved on, and the switch already reset the page.
    if (controller.signal.aborted) return;
    setIsRunActive(false);
    setLiveStatus(null);
    setPendingElicitation(null);
    setPendingToolApproval(null);
  }

  /**
   * Draw `body`'s events through `renderer` until it ends, then settle the page.
   *
   * A run that ended is acknowledged: the researcher has watched it, so its dot
   * and stored events go once the drawn thread is saved. The model history is
   * read back rather than assembled here, so it is exactly what the backend kept,
   * including the closing note of a stopped turn.
   */
  async function finishWatching(
    projectName: string,
    conversationId: string,
    controller: AbortController,
    body: ReadableStream<Uint8Array>,
    renderer: RunRenderer
  ) {
    try {
      await readSseStream(body, (event) => renderer.dispatch(event.event, event.data));
    } catch {
      // Aborted on a switch, or the stream broke; told apart below.
    }
    if (controller.signal.aborted) {
      endRun(controller);
      return;
    }
    const finished = renderer.finishedState !== null || renderer.sawResult;
    if (!finished) {
      setMessages((prev) => [
        ...prev,
        {
          id: crypto.randomUUID(),
          role: "assistant",
          content: "Agent run did not complete."
        }
      ]);
    }
    endRun(controller);
    if (!finished) return;
    setAckPending(true);
    try {
      const persisted = await fetchPersistedChatSession(projectName, conversationId);
      if (readActiveChatSessionId(projectName) === conversationId) {
        setMessageHistory(Array.isArray(persisted.message_history) ? persisted.message_history : []);
      }
    } catch {
      // The history only feeds "Save as skill"; the next open reads it again.
    }
  }

  /**
   * Watch a conversation's active run from its start. False when it has none
   * (it may have ended a moment ago), so the caller can look for what it left.
   */
  async function attachToRun(projectName: string, conversationId: string): Promise<boolean> {
    abortRunStream();
    const controller = new AbortController();
    streamRef.current = { controller, conversationId };
    let response: Response;
    try {
      response = await attachChatRun(projectName, conversationId, controller.signal);
    } catch {
      const aborted = controller.signal.aborted;
      endRun(controller);
      return aborted;
    }
    if (response.status !== 200 || !response.body) {
      endRun(controller);
      return false;
    }
    setIsRunActive(true);
    setLiveStatus("Working on it");
    const renderer = createRunRenderer(makeRunHost(conversationId, controller), {
      drawUserPrompt: true
    });
    await finishWatching(projectName, conversationId, controller, response.body, renderer);
    return true;
  }

  /**
   * Draw a turn that ran with nobody watching, from the events the backend kept
   * for it: the same renderer, so it looks as it would have live. Then the
   * save that follows acknowledges it.
   */
  function replayStoredRun(conversationId: string, events: PersistedChatSession["run_events"]) {
    const renderer = createRunRenderer(makeRunHost(conversationId, new AbortController()), {
      drawUserPrompt: true
    });
    for (const event of events ?? []) renderer.dispatch(event.event, event.data);
    setAckPending(true);
  }

  /** What a conversation was doing while it was closed. */
  async function resumeRun(
    projectName: string,
    conversationId: string,
    persisted: PersistedChatSession
  ) {
    let session = persisted;
    if (session.run_status === "working" || session.run_status === "needs_you") {
      if (await attachToRun(projectName, conversationId)) return;
      // It ended between the fetch and the attach, so what it left is stored now.
      try {
        session = await fetchPersistedChatSession(projectName, conversationId);
      } catch {
        return;
      }
      if (readActiveChatSessionId(projectName) !== conversationId) return;
    }
    if (session.run_events && session.run_events.length > 0) {
      replayStoredRun(conversationId, session.run_events);
    } else if (session.run_unseen || session.run_state !== "idle") {
      setAckPending(true);
    }
  }

  async function stopRun() {
    const projectName = activeProject?.name ?? null;
    if (!projectName || !activeChatSessionId) return;
    try {
      await stopChatRun(projectName, activeChatSessionId);
    } catch {
      // The run may already be over; the stream says how it ended.
    }
  }

  async function sendUserMessage() {
    const text = input.trim();
    if (!text || isRunActive) return;
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

    const conversationId = targetChatSessionId;
    const controller = new AbortController();
    streamRef.current = { controller, conversationId };
    setIsRunActive(true);
    const renderer = createRunRenderer(makeRunHost(conversationId, controller), {
      userBubbleId: userMessage.id
    });

    try {
      const response = await fetch("/api/chat", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          project_name: projectName,
          chat_session_id: conversationId,
          user_prompt: text,
        }),
        signal: controller.signal
      });

      if (response.status === 409) {
        // Another view is already running a turn here. Show that run instead,
        // and give the researcher their unsent text back.
        setMessages((prev) => prev.filter((m) => m.id !== userMessage.id));
        setInput(text);
        if (streamRef.current?.controller === controller) streamRef.current = null;
        if (!(await attachToRun(projectName, conversationId))) {
          setIsRunActive(false);
          setLiveStatus(null);
        }
        return;
      }

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
        endRun(controller);
        return;
      }

      await finishWatching(projectName, conversationId, controller, response.body, renderer);
    } catch {
      if (controller.signal.aborted) return;
      setMessages((prev) => [
        ...prev,
        {
          id: crypto.randomUUID(),
          role: "assistant",
          content: "Agent unavailable: failed to call /api/chat."
        }
      ]);
      endRun(controller);
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
  }, [messages, isRunActive, showJumpToLatest, agentLogs]);

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
                {isRunActive && (
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
              disabled={isRunActive}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  void sendUserMessage();
                }
              }}
            />
            <div className="composer-actions">
              {isRunActive && <span className="composer-status">Agent working…</span>}
              <div className="composer-spacer" />
              {isRunActive ? (
                <button
                  className="composer-send composer-stop"
                  onClick={() => void stopRun()}
                  title="Stop the agent"
                  aria-label="Stop"
                >
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                    <rect x="5" y="5" width="14" height="14" rx="2" />
                  </svg>
                </button>
              ) : (
                <button
                  className={`composer-send${isConversationListView ? " labelled" : ""}`}
                  onClick={() => void sendUserMessage()}
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
              )}
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
            {activitySteps.length === 0 && !isRunActive && (
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
            {isRunActive && liveStatus && (
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
