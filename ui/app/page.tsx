"use client";

import { useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import SandboxedHtmlCard from "@/components/SandboxedHtmlCard";
import type { ChatMessage, ExecutionResult, SkillDetail, SkillSummary } from "@/lib/types";

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

type ChatApiResponse = {
  ok: boolean;
  response: string;
  tools?: Array<{ name: string; description?: string; inputSchema?: any }>;
  error?: string;
};

function formatResultSummary(result: ExecutionResult): string {
  const status = result.ok ? "OK" : "ERROR";
  const output = result.stdout ? result.stdout.slice(0, 240) : "";
  return `${status}${output ? `: ${output}` : ""}`;
}

function getPlotPath(result: ExecutionResult): string | null {
  const artifact = result.artifacts.find((item) => item.type === "plot" && typeof item.url === "string");
  return artifact?.url || null;
}

export default function HomePage() {
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  const [filter, setFilter] = useState("");
  const [selectedSkill, setSelectedSkill] = useState<SkillDetail | null>(null);
  const [showSkillModal, setShowSkillModal] = useState(false);

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");

  const [showAnalyzeModal, setShowAnalyzeModal] = useState(false);
  const [saltInput, setSaltInput] = useState("AlCl3-KCl");

  const [latestResult, setLatestResult] = useState<ExecutionResult | null>(null);
  const [isCalling, setIsCalling] = useState(false);
  const [mcpHealth, setMcpHealth] = useState<McpHealth | null>(null);
  const [isCheckingHealth, setIsCheckingHealth] = useState(false);
  const [mcpTools, setMcpTools] = useState<McpToolsResponse | null>(null);
  const [isLoadingTools, setIsLoadingTools] = useState(false);
  const [useLlm, setUseLlm] = useState(false);
  const [isChatLoading, setIsChatLoading] = useState(false);

  useEffect(() => {
    fetch("/api/skills")
      .then((res) => res.json())
      .then((data) => setSkills(Array.isArray(data) ? data : []))
      .catch(() => setSkills([]));
  }, []);

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

    if (!useLlm) return;

    setIsChatLoading(true);
    try {
      const response = await fetch("/api/chat", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ message: text })
      });
      const data = (await response.json()) as ChatApiResponse;
      const content = data.ok
        ? data.response
        : `LLM unavailable: ${data.error || "Unknown error"}`;

      setMessages((prev) => [
        ...prev,
        {
          id: crypto.randomUUID(),
          role: "assistant",
          content
        }
      ]);

      if (Array.isArray(data.tools)) {
        setMcpTools({
          ok: true,
          tools: data.tools
        });
      }
    } catch {
      setMessages((prev) => [
        ...prev,
        {
          id: crypto.randomUUID(),
          role: "assistant",
          content: "LLM unavailable: failed to call /api/chat."
        }
      ]);
    } finally {
      setIsChatLoading(false);
    }
  }

  async function runSaltAnalysis() {
    const tool = "execute_skill_script";
    setIsCalling(true);
    const salt = saltInput.trim() || "AlCl3-KCl";
    const command = `skills/salt-analysis/scripts/analyze_salt.py --salt ${salt}`;

    try {
      const response = await fetch("/api/mcp/call", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ tool, args: { command } })
      });

      const result = (await response.json()) as ExecutionResult;
      let finalResult = result;
      const plotPath = getPlotPath(result);
      if (plotPath) {
        try {
          const previewResponse = await fetch("/api/mcp/call", {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({ tool: "display_file", args: { uri: plotPath } })
          });
          const previewResult = (await previewResponse.json()) as ExecutionResult;
          if (previewResult.ui?.kind === "html") {
            finalResult = {
              ...result,
              ui: previewResult.ui
            };
          }
        } catch {
          // Keep original result if preview lookup fails.
        }
      }

      setLatestResult(finalResult);
      setMessages((prev) => [
        ...prev,
        {
          id: crypto.randomUUID(),
          role: "tool",
          content: `Tool ${tool} finished. ${formatResultSummary(finalResult)}`,
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

  return (
    <main>
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

      <section className="panel" style={{ minHeight: 0 }}>
        <div className="panel-header">
          <div className="panel-title">Console</div>
          <button className="quick-chip" onClick={() => setShowAnalyzeModal(true)}>
            Analyze salt…
          </button>
        </div>
        <div className="panel-body" style={{ flex: 1 }}>
          <div className="chat-list">
            {messages.length === 0 && (
              <div className="chat-bubble">
                No messages yet. Use the quick action to run the salt analysis tool.
              </div>
            )}
            {messages.map((msg) => (
              <div key={msg.id} className={`chat-bubble ${msg.role}`}>
                {msg.content}
                {msg.result && !msg.result.ok && (
                  <div className="error" style={{ marginTop: 6 }}>
                    {msg.result.stderr}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
        <div className="chat-input-row">
          <input
            className="input"
            placeholder="Type a note for your run..."
            value={input}
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                void sendUserMessage();
              }
            }}
          />
          <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: "#6a6258" }}>
            <input
              type="checkbox"
              checked={useLlm}
              onChange={(event) => setUseLlm(event.target.checked)}
            />
            Use LLM
          </label>
          <button className="button" onClick={() => void sendUserMessage()} disabled={isChatLoading}>
            {isChatLoading ? "Thinking..." : "Add"}
          </button>
        </div>
      </section>

      <section className="panel">
        <div className="panel-header">
          <div className="panel-title">Latest Output</div>
        </div>
        <div className="panel-body">
          <div className="output-split">
            <div className="output-top">
              {!latestResult && <div className="chat-bubble">No figure yet.</div>}
              {latestResult && latestResult.ui?.kind === "html" && (
                <SandboxedHtmlCard html={latestResult.ui.html} />
              )}
              {latestResult && latestResult.ui?.kind !== "html" && (
                <div className="chat-bubble">No image or plot rendered for this result.</div>
              )}
            </div>

            <div className="output-bottom">
              <div style={{ display: "flex", gap: 8, marginBottom: 12, flexWrap: "wrap" }}>
                <button className="button ghost" onClick={checkMcpHealth} disabled={isCheckingHealth}>
                  {isCheckingHealth ? "Checking MCP..." : "MCP Status"}
                </button>
                <button className="button ghost" onClick={listMcpTools} disabled={isLoadingTools}>
                  {isLoadingTools ? "Loading tools..." : "List MCP Tools"}
                </button>
              </div>

              {mcpHealth && (
                <div className="chat-bubble" style={{ marginBottom: 12 }}>
                  MCP: {mcpHealth.ok ? "Connected" : "Disconnected"} ({mcpHealth.mcpBaseUrl})
                  {mcpHealth.detail ? ` - ${mcpHealth.detail}` : ""}
                </div>
              )}

              {mcpTools && (
                <div className="chat-bubble" style={{ marginBottom: 12 }}>
                  {mcpTools.ok ? "Discovered tools:" : "Tool discovery failed:"}
                  {mcpTools.ok && mcpTools.tools.length > 0 && (
                    <div style={{ marginTop: 6 }}>
                      {mcpTools.tools.map((tool) => (
                        <div key={tool.name}>{tool.name}</div>
                      ))}
                    </div>
                  )}
                  {mcpTools.ok && mcpTools.tools.length === 0 && (
                    <div style={{ marginTop: 6 }}>No tools returned.</div>
                  )}
                  {!mcpTools.ok && mcpTools.error && (
                    <div className="error" style={{ marginTop: 6 }}>{mcpTools.error}</div>
                  )}
                </div>
              )}

              {!latestResult && <div className="chat-bubble">No execution yet.</div>}
              {latestResult && (
                <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                  {!!latestResult.meta?.analysisSummary && (
                    <div className="chat-bubble">
                      {(() => {
                        const summary = latestResult.meta.analysisSummary as Record<string, unknown>;
                        const measurements = typeof summary.measurements === "number" ? summary.measurements : null;
                        const compositions = typeof summary.compositions === "number" ? summary.compositions : null;
                        const references = typeof summary.references === "number" ? summary.references : null;
                        const plotPath = typeof summary.plotPath === "string" ? summary.plotPath : null;
                        return (
                          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                            <strong>Salt Analysis Summary</strong>
                            <div>Measurements: {measurements ?? "n/a"}</div>
                            <div>Compositions: {compositions ?? "n/a"}</div>
                            <div>References: {references ?? "n/a"}</div>
                            {plotPath && <div>Plot: {plotPath}</div>}
                          </div>
                        );
                      })()}
                    </div>
                  )}
                  <div className="output-card">
                    {JSON.stringify(
                      {
                        ok: latestResult.ok,
                        stdout: latestResult.stdout,
                        stderr: latestResult.stderr,
                        data: latestResult.data,
                        artifacts: latestResult.artifacts,
                        meta: latestResult.meta
                      },
                      null,
                      2
                    )}
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      </section>

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
    </main>
  );
}
