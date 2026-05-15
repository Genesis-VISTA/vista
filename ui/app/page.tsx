"use client";

// Main chat page — three-panel layout: NavRail (in layout.tsx) | Chat | LatestOutput+AgentLogs.
// Powered by Vercel AI SDK v5 useChat.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useChat } from "@ai-sdk/react";
import { DefaultChatTransport, type PrepareSendMessagesRequest } from "ai";
import ChatInterface from "@/components/ChatInterface";
import LatestOutput from "@/components/LatestOutput";
import AgentLogs from "@/components/AgentLogs";
import { useActiveProject } from "@/lib/projects";
import type { LogData, VistaUIMessage } from "@/lib/types";

const SIDEBAR_MIN = 200;
const SIDEBAR_MAX = 900;
const SIDEBAR_DEFAULT = 480;

export default function HomePage() {
  const { activeProject } = useActiveProject();

  const [sidebarWidth, setSidebarWidth] = useState(SIDEBAR_DEFAULT);
  const isResizing = useRef(false);
  const dragStartX = useRef(0);
  const dragStartWidth = useRef(0);

  const onResizeMouseDown = useCallback((e: React.MouseEvent) => {
    isResizing.current = true;
    dragStartX.current = e.clientX;
    dragStartWidth.current = sidebarWidth;
    e.preventDefault();
  }, [sidebarWidth]);

  useEffect(() => {
    const onMouseMove = (e: MouseEvent) => {
      if (!isResizing.current) return;
      const delta = dragStartX.current - e.clientX;
      setSidebarWidth(Math.max(SIDEBAR_MIN, Math.min(SIDEBAR_MAX, dragStartWidth.current + delta)));
    };
    const onMouseUp = () => {
      isResizing.current = false;
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };
    const onMouseMoveStart = (e: MouseEvent) => {
      if (isResizing.current) {
        document.body.style.cursor = "col-resize";
        document.body.style.userSelect = "none";
      }
      onMouseMove(e);
    };
    window.addEventListener("mousemove", onMouseMoveStart);
    window.addEventListener("mouseup", onMouseUp);
    return () => {
      window.removeEventListener("mousemove", onMouseMoveStart);
      window.removeEventListener("mouseup", onMouseUp);
    };
  }, []);

  // useChat caches the transport from the first render, so reading activeProject
  // directly in `body` would always see the initial `null`. A ref lets the
  // request preparer see the *current* active project at send time.
  const projectNameRef = useRef<string | null>(null);
  useEffect(() => {
    projectNameRef.current = activeProject?.name ?? null;
  }, [activeProject]);

  const prepareSendMessagesRequest = useCallback<PrepareSendMessagesRequest<VistaUIMessage>>(
    ({ id, messages, trigger, body }) => ({
      body: {
        ...body,
        id,
        messages,
        trigger,
        project_name: projectNameRef.current,
      },
    }),
    [],
  );

  // Transport is created once; useChat caches it from the first render.
  // prepareSendMessagesRequest reads projectNameRef.current at send time (not during render).
  /* eslint-disable react-hooks/refs */
  const transport = useMemo(
    () =>
      new DefaultChatTransport<VistaUIMessage>({
        api: "/api/chat",
        prepareSendMessagesRequest,
      }),
    [prepareSendMessagesRequest],
  );
  /* eslint-enable react-hooks/refs */

  // Transient data parts (logs) aren't persisted to messages, so we collect
  // them via onData and keep our own buffer.
  const [logs, setLogs] = useState<LogData[]>([]);
  const onData = useCallback(
    (part: { type: string; data?: unknown }) => {
      if (part.type === "data-log" && part.data) {
        setLogs((prev) => [...prev, part.data as LogData]);
      }
    },
    [],
  );

  const chat = useChat<VistaUIMessage>({
    transport,
    onData,
    onError: (err) => {
      console.error("chat error:", err);
    },
  });

  const clearLogs = useCallback(() => setLogs([]), []);

  return (
    <div className="flex h-full overflow-hidden">
      <div className="flex-1 min-w-0">
        <ChatInterface chat={chat} project={activeProject ?? null} />
      </div>

      <div
        className="w-1 flex-shrink-0 cursor-col-resize bg-gray-200 hover:bg-blue-400 active:bg-blue-500 transition-colors"
        onMouseDown={onResizeMouseDown}
      />

      <div
        style={{ width: sidebarWidth }}
        className="flex-shrink-0 flex flex-col border-gray-200 bg-white overflow-hidden"
      >
        <LatestOutput messages={chat.messages} />
        <AgentLogs logs={logs} onClear={clearLogs} />
      </div>
    </div>
  );
}
