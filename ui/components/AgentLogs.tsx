"use client";

// Bottom-right panel: streaming agent log viewer with MCP status & tool list controls.
// Logs come from `data-log` transient data parts surfaced via useChat's onData.

import { useEffect, useRef, useState } from "react";
import type { LogData } from "@/lib/types";

type McpHealth = { ok: boolean; mcpBaseUrl: string; detail?: string };
type McpTool = { name: string; description?: string };

interface AgentLogsProps {
  logs: LogData[];
  onClear: () => void;
}

const levelClass: Record<string, string> = {
  ERROR: "text-rose-300",
  WARNING: "text-amber-300",
  INFO: "text-slate-200",
  DEBUG: "text-slate-400",
};

export default function AgentLogs({ logs, onClear }: AgentLogsProps) {
  const [mcpHealth, setMcpHealth] = useState<McpHealth | null>(null);
  const [mcpTools, setMcpTools] = useState<McpTool[] | null>(null);
  const [busy, setBusy] = useState<"health" | "tools" | null>(null);
  const endRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [logs.length]);

  async function checkHealth() {
    setBusy("health");
    try {
      const res = await fetch("/api/mcp/health", { cache: "no-store" });
      setMcpHealth(await res.json());
    } catch (e) {
      setMcpHealth({
        ok: false,
        mcpBaseUrl: "unknown",
        detail: e instanceof Error ? e.message : "fetch failed",
      });
    } finally {
      setBusy(null);
    }
  }

  async function listTools() {
    setBusy("tools");
    try {
      const res = await fetch("/api/mcp/tools", { cache: "no-store" });
      const data: { ok: boolean; tools?: McpTool[] } = await res.json();
      setMcpTools(data.ok ? data.tools ?? [] : []);
    } catch {
      setMcpTools([]);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="flex flex-col flex-1 min-h-0 border-t border-gray-200 bg-white">
      <div className="flex items-center justify-between px-3 py-2 border-b border-gray-200">
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold text-gray-800">Agent Logs</span>
          {logs.length > 0 && (
            <span className="text-[11px] font-mono text-gray-500">{logs.length} entries</span>
          )}
        </div>
        <div className="flex gap-1.5">
          <button
            type="button"
            className="text-xs rounded border border-gray-300 px-2 py-0.5 hover:bg-gray-50 disabled:opacity-50"
            onClick={checkHealth}
            disabled={busy !== null}
          >
            {busy === "health" ? "…" : "MCP Status"}
          </button>
          <button
            type="button"
            className="text-xs rounded border border-gray-300 px-2 py-0.5 hover:bg-gray-50 disabled:opacity-50"
            onClick={listTools}
            disabled={busy !== null}
          >
            {busy === "tools" ? "…" : "Tools"}
          </button>
          <button
            type="button"
            className="text-xs rounded border border-gray-300 px-2 py-0.5 hover:bg-gray-50"
            onClick={onClear}
          >
            Clear
          </button>
        </div>
      </div>

      {mcpHealth && (
        <div className="px-3 py-1.5 text-[11px] border-b border-gray-200 bg-gray-50 text-gray-700">
          MCP: {mcpHealth.ok ? "✓ Connected" : "✗ Disconnected"} ({mcpHealth.mcpBaseUrl})
          {mcpHealth.detail ? ` — ${mcpHealth.detail}` : ""}
        </div>
      )}
      {mcpTools && (
        <div className="px-3 py-1.5 text-[11px] border-b border-gray-200 bg-gray-50 text-gray-700">
          Tools ({mcpTools.length}):{" "}
          {mcpTools.length === 0 ? "(none)" : mcpTools.map((t) => t.name).join(", ")}
        </div>
      )}

      <div className="flex-1 min-h-0 overflow-auto bg-[#0b1320] font-mono text-[11px] leading-relaxed p-2">
        {logs.length === 0 ? (
          <div className="italic text-slate-500">Waiting for agent activity…</div>
        ) : (
          logs.map((entry, i) => (
            <div key={i} className="whitespace-pre-wrap break-words">
              <span className={`mr-2 ${levelClass[entry.level] ?? "text-slate-300"}`}>
                {entry.level}
              </span>
              <span className="mr-2 text-slate-400">[{entry.area}]</span>
              <span className="text-slate-200">{entry.message}</span>
            </div>
          ))
        )}
        <div ref={endRef} />
      </div>
    </div>
  );
}
