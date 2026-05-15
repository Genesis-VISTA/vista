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
const OUTPUT_MIN = 120;
const OUTPUT_MAX = 4000;
const OUTPUT_DEFAULT = 420;

export default function HomePage() {
  const { activeProject } = useActiveProject();

  const [sidebarWidth, setSidebarWidth] = useState(SIDEBAR_DEFAULT);
  const isResizing = useRef(false);
  const dragStartX = useRef(0);
  const dragStartWidth = useRef(0);

  const [outputHeight, setOutputHeight] = useState(OUTPUT_DEFAULT);
  const isResizingV = useRef(false);
  const dragStartY = useRef(0);
  const dragStartHeight = useRef(0);
  const sidebarRef = useRef<HTMLDivElement | null>(null);

  const onResizeMouseDown = useCallback((e: React.MouseEvent) => {
    isResizing.current = true;
    dragStartX.current = e.clientX;
    dragStartWidth.current = sidebarWidth;
    e.preventDefault();
  }, [sidebarWidth]);

  const onVResizeMouseDown = useCallback((e: React.MouseEvent) => {
    isResizingV.current = true;
    dragStartY.current = e.clientY;
    dragStartHeight.current = outputHeight;
    e.preventDefault();
  }, [outputHeight]);

  useEffect(() => {
    const onMouseMove = (e: MouseEvent) => {
      if (isResizing.current) {
        const delta = dragStartX.current - e.clientX;
        setSidebarWidth(
          Math.max(SIDEBAR_MIN, Math.min(SIDEBAR_MAX, dragStartWidth.current + delta)),
        );
      } else if (isResizingV.current) {
        const delta = e.clientY - dragStartY.current;
        const containerHeight = sidebarRef.current?.clientHeight ?? OUTPUT_MAX;
        // Leave at least OUTPUT_MIN px for the logs panel below.
        const maxH = Math.max(OUTPUT_MIN, containerHeight - OUTPUT_MIN);
        setOutputHeight(
          Math.max(OUTPUT_MIN, Math.min(maxH, dragStartHeight.current + delta)),
        );
      }
    };
    const onMouseUp = () => {
      isResizing.current = false;
      isResizingV.current = false;
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };
    const onMouseMoveStart = (e: MouseEvent) => {
      if (isResizing.current) {
        document.body.style.cursor = "col-resize";
        document.body.style.userSelect = "none";
      } else if (isResizingV.current) {
        document.body.style.cursor = "row-resize";
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
        ref={sidebarRef}
        style={{ width: sidebarWidth }}
        className="flex-shrink-0 flex flex-col border-gray-200 bg-white overflow-hidden"
      >
        <div
          style={{ height: outputHeight }}
          className="flex-shrink-0 flex flex-col min-h-0"
        >
          <LatestOutput messages={chat.messages} />
        </div>
        <div
          className="h-1 flex-shrink-0 cursor-row-resize bg-gray-200 hover:bg-blue-400 active:bg-blue-500 transition-colors"
          onMouseDown={onVResizeMouseDown}
          aria-label="Resize output panel"
          role="separator"
        />
        <div className="flex-1 min-h-0 flex flex-col">
          <AgentLogs logs={logs} onClear={clearLogs} />
        </div>
      </div>
    </div>
  );
}
