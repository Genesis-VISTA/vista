"use client";

import { useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import SandboxedHtmlCard from "@/components/SandboxedHtmlCard";
import type { ChatMessage, ExecutionResult, SkillDetail, SkillSummary } from "@/lib/types";

function formatResultSummary(result: ExecutionResult): string {
  const status = result.ok ? "OK" : "ERROR";
  const output = result.stdout ? result.stdout.slice(0, 240) : "";
  return `${status}${output ? `: ${output}` : ""}`;
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
  const [richUi, setRichUi] = useState(false);

  const [latestResult, setLatestResult] = useState<ExecutionResult | null>(null);
  const [isCalling, setIsCalling] = useState(false);

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

  function sendUserMessage() {
    const text = input.trim();
    if (!text) return;
    setMessages((prev) => [
      ...prev,
      { id: crypto.randomUUID(), role: "user", content: text }
    ]);
    setInput("");
  }

  async function runSaltAnalysis() {
    const tool = richUi ? "analyze_salt_ui" : "run_salt_analysis";
    setIsCalling(true);
    const salt = saltInput.trim() || "AlCl3-KCl";

    try {
      const response = await fetch("/api/mcp/call", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ tool, args: { salt } })
      });

      const result = (await response.json()) as ExecutionResult;
      setLatestResult(result);
      setMessages((prev) => [
        ...prev,
        {
          id: crypto.randomUUID(),
          role: "tool",
          content: `Tool ${tool} finished. ${formatResultSummary(result)}`,
          result
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
              if (event.key === "Enter") sendUserMessage();
            }}
          />
          <button className="button" onClick={sendUserMessage}>
            Add
          </button>
        </div>
      </section>

      <section className="panel">
        <div className="panel-header">
          <div className="panel-title">Latest Output</div>
        </div>
        <div className="panel-body">
          {!latestResult && <div className="chat-bubble">No execution yet.</div>}
          {latestResult && (
            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
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
              {latestResult.ui?.kind === "html" && (
                <SandboxedHtmlCard html={latestResult.ui.html} />
              )}
            </div>
          )}
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
              <label style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <input
                  type="checkbox"
                  checked={richUi}
                  onChange={(event) => setRichUi(event.target.checked)}
                />
                Use rich UI tool if available
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
