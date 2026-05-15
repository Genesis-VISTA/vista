"use client";

// Chat panel: header (model picker + Agent toggle + quick chips) → scrollable
// message list → composer.
//
// Messages come from useChat (Vercel AI SDK v5). Each UIMessage has `parts`;
// we render text parts as markdown bubbles, tool parts as compact status chips,
// and watch for `data-mcp-elicitation` parts to surface ElicitationModal.

import { useEffect, useMemo, useRef, useState } from "react";
import type { UseChatHelpers } from "@ai-sdk/react";
import { isToolUIPart, getToolOrDynamicToolName } from "ai";
import type { ToolUIPart, DynamicToolUIPart, UITools } from "ai";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import ElicitationModal from "@/components/ElicitationModal";
import type { ElicitationData, Project, VistaUIMessage } from "@/lib/types";

interface ChatInterfaceProps {
  chat: UseChatHelpers<VistaUIMessage>;
  project: Project | null;
}

const MODEL_LABEL = "AmSC model services / gpt-5";

const SUGGESTIONS = [
  "Analyze salt…",
  "Predict salt…",
];

export default function ChatInterface({ chat, project }: ChatInterfaceProps) {
  const { messages, sendMessage, status, stop, error, clearError } = chat;
  const [input, setInput] = useState("");
  const [agentToggle, setAgentToggle] = useState(true);
  const scrollerRef = useRef<HTMLDivElement | null>(null);

  // Auto-scroll to newest message unless the user has scrolled away.
  const [stickToBottom, setStickToBottom] = useState(true);
  const [resolvedElicitations, setResolvedElicitations] = useState<Set<string>>(new Set());
  useEffect(() => {
    if (!stickToBottom) return;
    const el = scrollerRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, status, stickToBottom]);

  function onScroll() {
    const el = scrollerRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
    setStickToBottom(atBottom);
  }

  const isStreaming = status === "submitted" || status === "streaming";

  function submit() {
    const text = input.trim();
    if (!text || isStreaming) return;
    if (!project) return;
    setInput("");
    setStickToBottom(true);
    void sendMessage({ text });
  }

  // Find the open elicitation (if any) — backend emits these as a persisted
  // `data-mcp-form-elicitation` or `data-mcp-url-elicitation` part on the assistant message.
  const pendingElicitation = useMemo<ElicitationData | null>(() => {
    for (let m = messages.length - 1; m >= 0; m--) {
      const parts = messages[m].parts;
      for (let p = parts.length - 1; p >= 0; p--) {
        const part = parts[p];
        if (part.type === "data-mcp-form-elicitation" || part.type === "data-mcp-url-elicitation") {
          const data = part.data as ElicitationData;
          if (!resolvedElicitations.has(data.elicitation_id)) return data;
        }
      }
    }
    return null;
  }, [messages, resolvedElicitations]);

  async function resolveElicitation(
    id: string,
    action: "accept" | "decline" | "cancel",
    content?: Record<string, unknown>,
  ) {
    setResolvedElicitations((prev) => new Set([...prev, id]));
    try {
      await fetch("/api/mcp/elicitation", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ id, action, content }),
      });
    } catch {
      // backend has a 5min timeout that will auto-cancel
    }
  }

  return (
    <div className="flex flex-col h-full bg-white">
      <div className="flex items-center justify-between gap-3 px-4 py-3 border-b border-gray-200">
        <div className="flex flex-col gap-1">
          <div className="text-[11px] uppercase tracking-widest text-gray-500">Chat with</div>
          <button
            type="button"
            className="self-start rounded-full border border-gray-300 px-3 py-1 text-sm text-gray-800 hover:bg-gray-50"
            disabled
            title="Model picker is fixed in this build"
          >
            {MODEL_LABEL}
          </button>
        </div>
        <div className="flex items-center gap-2">
          {SUGGESTIONS.map((label) => (
            <button
              key={label}
              type="button"
              onClick={() => setInput(label.replace("…", " "))}
              className="rounded-full border border-dashed border-gray-300 px-3 py-1 text-xs text-gray-700 hover:bg-gray-50"
            >
              {label}
            </button>
          ))}
          <label className="flex items-center gap-2 text-xs text-gray-700">
            <span>Agent</span>
            <span className="relative inline-block w-9 h-5">
              <input
                type="checkbox"
                className="peer sr-only"
                checked={agentToggle}
                onChange={(e) => setAgentToggle(e.target.checked)}
              />
              <span className="absolute inset-0 rounded-full bg-gray-300 peer-checked:bg-amber-400 transition-colors" />
              <span className="absolute left-0.5 top-0.5 h-4 w-4 rounded-full bg-white shadow transition-transform peer-checked:translate-x-4" />
            </span>
          </label>
        </div>
      </div>

      <div
        ref={scrollerRef}
        onScroll={onScroll}
        className="flex-1 overflow-y-auto px-4 py-4 space-y-3"
      >
        {messages.length === 0 && (
          <div className="rounded-md border border-gray-200 bg-gray-50 px-3 py-2 text-sm text-gray-700">
            Ask me about molten salts! Try: &quot;Show me the phase diagram for AlCl3-KCl&quot; or
            &quot;How many fluoride salts are in the database?&quot;
          </div>
        )}

        {messages.map((message) => (
          <MessageBubble key={message.id} message={message} />
        ))}

        {isStreaming && (
          <div className="flex items-center gap-2 text-sm text-gray-600">
            <ThinkingDots />
            <span>Working on it…</span>
            <button
              type="button"
              onClick={() => stop()}
              className="ml-2 text-xs text-rose-700 hover:underline"
            >
              Stop
            </button>
          </div>
        )}

        {error && (
          <div className="rounded-md bg-rose-50 border border-rose-200 text-rose-800 px-3 py-2 text-sm flex items-start gap-2">
            <span className="flex-1">{error.message}</span>
            <button type="button" onClick={() => clearError()} className="text-xs underline">
              Dismiss
            </button>
          </div>
        )}
      </div>

      <div className="flex items-center gap-2 px-4 py-3 border-t border-gray-200">
        <input
          className="flex-1 rounded-md border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-300"
          placeholder={
            project
              ? "Ask about molten salts… (e.g., 'show phase diagram for LiF-NaF')"
              : "Select a project from the sidebar first…"
          }
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              submit();
            }
          }}
          disabled={!project || isStreaming}
        />
        <button
          type="button"
          onClick={submit}
          disabled={!project || isStreaming || !input.trim()}
          className="rounded-md bg-[#1a2740] text-white px-3 py-2 text-sm hover:bg-[#243657] disabled:bg-gray-300"
          aria-label="Send"
        >
          ⏎
        </button>
      </div>

      {pendingElicitation && (
        <ElicitationModal request={pendingElicitation} onSubmit={resolveElicitation} />
      )}
    </div>
  );
}

function ThinkingDots() {
  return (
    <span className="inline-flex gap-0.5">
      <span className="w-1.5 h-1.5 rounded-full bg-gray-400 animate-bounce [animation-delay:-0.3s]" />
      <span className="w-1.5 h-1.5 rounded-full bg-gray-400 animate-bounce [animation-delay:-0.15s]" />
      <span className="w-1.5 h-1.5 rounded-full bg-gray-400 animate-bounce" />
    </span>
  );
}

function MessageBubble({ message }: { message: VistaUIMessage }) {
  const isUser = message.role === "user";
  return (
    <div className="flex justify-start">
      <div
        className={`max-w-[85%] rounded-md px-3 py-2 text-sm ${
          isUser
            ? "bg-blue-600 text-white"
            : "bg-gray-100 text-gray-900 border border-gray-200"
        }`}
      >
        {message.parts.map((part, i) => {
          if (part.type === "text") {
            return isUser ? (
              <span key={i} className="whitespace-pre-wrap">{part.text}</span>
            ) : (
              <div key={i} className="prose prose-sm max-w-none prose-pre:bg-gray-900 prose-pre:text-gray-100">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{part.text}</ReactMarkdown>
              </div>
            );
          }
          if (part.type === "reasoning") {
            return (
              <details key={i} className="text-xs text-gray-600 mt-1">
                <summary className="cursor-pointer">thinking</summary>
                <div className="whitespace-pre-wrap mt-1">{part.text}</div>
              </details>
            );
          }
          if (part.type === "dynamic-tool" || isToolUIPart(part)) {
            return <ToolCallEntry key={i} part={part} />;
          }
          if (part.type === "data-mcp-form-elicitation" || part.type === "data-mcp-url-elicitation") {
            return (
              <div key={i} className="mt-1 text-[11px] italic text-gray-500">
                waiting for input…
              </div>
            );
          }
          return null;
        })}
      </div>
    </div>
  );
}

type ToolPart = ToolUIPart<UITools> | DynamicToolUIPart;

function formatPayload(value: unknown): string {
  if (value === undefined || value === null) return "";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function ToolCallEntry({ part }: { part: ToolPart }) {
  const name = getToolOrDynamicToolName(part);
  const isError = part.state === "output-error";
  const statusLabel =
    part.state === "input-streaming"
      ? "calling…"
      : part.state === "input-available"
        ? "running…"
        : isError
          ? "✗"
          : "✓";

  const input = "input" in part ? part.input : undefined;
  const output = "output" in part ? part.output : undefined;
  const errorText = isError && "errorText" in part ? part.errorText : undefined;
  const inputStr = formatPayload(input);
  const outputStr = isError ? errorText ?? formatPayload(output) : formatPayload(output);
  const expandable =
    inputStr.length > 0 || outputStr.length > 0;

  return (
    <details
      className={`mt-1 group text-xs ${isError ? "text-rose-700" : "text-gray-600"}`}
    >
      <summary
        className={`flex items-center gap-1 ${expandable ? "cursor-pointer hover:text-gray-900" : "cursor-default list-none"}`}
      >
        {expandable && (
          <span
            aria-hidden="true"
            className="inline-block transition-transform group-open:rotate-90 text-gray-400"
          >
            ›
          </span>
        )}
        <span className="font-mono">{name}</span>
        <span className={`text-[11px] ${isError ? "text-rose-600" : "text-gray-400"}`}>
          {statusLabel}
        </span>
      </summary>
      {expandable && (
        <div className="mt-1 ml-3 space-y-1 border-l border-gray-200 pl-2">
          {inputStr && (
            <div>
              <div className="text-[10px] uppercase tracking-widest text-gray-400">
                Input
              </div>
              <pre className="whitespace-pre-wrap break-words font-mono text-[11px] text-gray-700 bg-gray-50 rounded px-2 py-1 max-h-60 overflow-auto">
                {inputStr}
              </pre>
            </div>
          )}
          {outputStr && (
            <div>
              <div className="text-[10px] uppercase tracking-widest text-gray-400">
                {isError ? "Error" : "Output"}
              </div>
              <pre
                className={`whitespace-pre-wrap break-words font-mono text-[11px] rounded px-2 py-1 max-h-60 overflow-auto ${
                  isError
                    ? "text-rose-700 bg-rose-50"
                    : "text-gray-700 bg-gray-50"
                }`}
              >
                {outputStr}
              </pre>
            </div>
          )}
        </div>
      )}
    </details>
  );
}
