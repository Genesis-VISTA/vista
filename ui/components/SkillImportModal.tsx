"use client";

import { useRef, useState } from "react";
import type { SkillSummary } from "@/lib/types";

type Props = {
  open: boolean;
  /**
   * Project the imported skill is loaded into; its output folder is also the
   * "From project output" source.
   */
  project: string | null;
  onCancel: () => void;
  onImported: (skill: SkillSummary) => void;
};

type Source = "github" | "local" | "output";

/** A folder in the researcher's project output that holds a SKILL.md (backend `OutputSkill`). */
type OutputSkill = {
  folder: string;
  name: string | null;
  error: string | null;
  hpc_jobs: string[];
  exists: boolean;
  replaceable: boolean;
};

// Clutter a folder picker drags along. Skipped here so it is never uploaded;
// the backend drops the same names if they arrive anyway.
const IGNORED_DIRS = new Set([".git", "node_modules", "__pycache__", ".venv"]);
const IGNORED_FILES = new Set([".DS_Store", "Thumbs.db"]);

/** A picked file's path inside the chosen folder, e.g. "my-skill/scripts/run.py". */
function relativePath(file: File): string {
  return file.webkitRelativePath || file.name;
}

function isIgnored(path: string): boolean {
  const parts = path.split("/");
  const name = parts[parts.length - 1];
  return IGNORED_FILES.has(name) || parts.slice(0, -1).some((part) => IGNORED_DIRS.has(part));
}

export function SkillImportModal({ open, project, onCancel, onImported }: Props) {
  const [source, setSource] = useState<Source>("github");
  const [url, setUrl] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [outputSkills, setOutputSkills] = useState<OutputSkill[] | null>(null);
  const [outputFolder, setOutputFolder] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const folderInput = useRef<HTMLInputElement | null>(null);

  if (!open) return null;

  const folderName = files.length > 0 ? relativePath(files[0]).split("/")[0] : null;
  const picked = outputSkills?.find((s) => s.folder === outputFolder) ?? null;
  const canSubmit =
    source === "github"
      ? url.trim().length > 0
      : source === "local"
        ? files.length > 0
        : picked !== null && !picked.error && (!picked.exists || picked.replaceable);

  async function loadOutputSkills() {
    if (!project) return;
    setOutputSkills(null);
    setOutputFolder(null);
    try {
      const resp = await fetch(
        `/api/projects/${encodeURIComponent(project)}/skills/from-output`
      );
      const data = resp.ok ? await resp.json() : [];
      const list = Array.isArray(data) ? (data as OutputSkill[]) : [];
      setOutputSkills(list);
      const first = list.find((s) => !s.error && (!s.exists || s.replaceable));
      setOutputFolder(first?.folder ?? null);
      if (!resp.ok) setError(`Could not list the project's output (${resp.status}).`);
    } catch {
      setOutputSkills([]);
      setError("Could not list the project's output.");
    }
  }

  function pickFolder(list: FileList | null) {
    setError(null);
    setFiles(Array.from(list ?? []).filter((file) => !isIgnored(relativePath(file))));
  }

  function request(): Promise<Response> {
    if (source === "output") {
      return fetch(`/api/projects/${encodeURIComponent(project ?? "")}/skills/from-output`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ folder: outputFolder, replace: picked?.exists ?? false }),
      });
    }
    if (source === "github") {
      return fetch("/api/skills/import", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ url: url.trim(), project }),
      });
    }
    const form = new FormData();
    for (const file of files) {
      form.append("files", file, file.name);
      form.append("paths", relativePath(file));
    }
    if (project) form.append("project", project);
    return fetch("/api/skills/import/upload", { method: "POST", body: form });
  }

  async function submit() {
    if (!canSubmit) return;
    setBusy(true);
    setError(null);
    try {
      const resp = await request();
      const text = await resp.text();
      let payload: unknown = null;
      try {
        payload = text ? JSON.parse(text) : null;
      } catch {
        payload = { error: text };
      }
      if (!resp.ok) {
        let detail = `Import failed (${resp.status}).`;
        if (payload && typeof payload === "object") {
          if ("detail" in payload) detail = String((payload as { detail: unknown }).detail);
          else if ("error" in payload) detail = String((payload as { error: unknown }).error);
        }
        setError(detail);
        return;
      }
      if (source === "output") {
        setOutputSkills(null);
        setOutputFolder(null);
      }
      onImported(payload as SkillSummary);
      setUrl("");
      setFiles([]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Import failed.");
    } finally {
      setBusy(false);
    }
  }

  function tab(value: Source, label: string) {
    const active = source === value;
    return (
      <button
        type="button"
        role="tab"
        aria-selected={active}
        className="kb-tab"
        data-active={active ? "true" : "false"}
        onClick={() => {
          setSource(value);
          setError(null);
          if (value === "output") void loadOutputSkills();
        }}
        disabled={busy}
      >
        {label}
      </button>
    );
  }

  return (
    <div className="modal-backdrop" onClick={busy ? undefined : onCancel}>
      <div
        className="modal"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label="Import skill"
      >
        <div className="panel-header">
          <div className="panel-title">Import skill</div>
          <button type="button" className="button ghost" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
        </div>
        <div className="kb-tabs-row">
          <div className="kb-tabs" role="tablist" aria-label="Import source">
            {tab("github", "GitHub URL")}
            {tab("local", "Local folder")}
            {project && tab("output", "From project output")}
          </div>
        </div>
        <div className="modal-body">
          {error && <div className="error">{error}</div>}
          {source === "output" ? (
            <>
              <div className="skill-editor-label">
                <span>Skill folder in this project&apos;s output</span>
                {outputSkills === null ? (
                  <div style={{ fontSize: 12, color: "var(--muted)" }}>Looking…</div>
                ) : outputSkills.length === 0 ? (
                  <div style={{ fontSize: 12, color: "var(--muted)" }}>
                    No skills in this project&apos;s output yet. Ask the agent to make
                    one (the <code>hpc-skill-author</code> skill writes it to{" "}
                    <code>/mnt/data/output/&lt;skill&gt;/</code>).
                  </div>
                ) : (
                  <div role="radiogroup" className="skill-output-list">
                    {outputSkills.map((s) => {
                      const blocked = Boolean(s.error) || (s.exists && !s.replaceable);
                      return (
                        <label key={s.folder} className="skill-output-row">
                          <input
                            type="radio"
                            name="output-skill"
                            checked={outputFolder === s.folder}
                            disabled={busy || blocked}
                            onChange={() => setOutputFolder(s.folder)}
                          />
                          <span>
                            <strong>{s.folder}</strong>
                            {s.hpc_jobs.length > 0 && (
                              <span className="skill-hpc" style={{ marginLeft: 8 }}>
                                Runs HPC jobs: {s.hpc_jobs.join(", ")}
                              </span>
                            )}
                            <span style={{ display: "block", fontSize: 12, color: "var(--muted)" }}>
                              {s.error
                                ? s.error
                                : s.exists && !s.replaceable
                                  ? `A published skill is named ${s.name}; rename this one.`
                                  : s.exists
                                    ? `Replaces your private skill ${s.name}.`
                                    : `New skill ${s.name}.`}
                            </span>
                          </span>
                        </label>
                      );
                    })}
                  </div>
                )}
              </div>
              <p style={{ fontSize: 12, color: "var(--muted)", margin: 0 }}>
                Imports the folder the agent wrote, checked like a local folder, and
                loads it into this project, so the agent can use it (and submit its
                HPC jobs) from your next message. Imported skills land private.
              </p>
            </>
          ) : source === "github" ? (
            <>
              <label className="skill-editor-label">
                <span>Repository URL</span>
                <input
                  key="url"
                  className="input"
                  autoFocus
                  placeholder="https://github.com/owner/repo  or  .../tree/main/path/to/skill"
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") void submit();
                  }}
                  disabled={busy}
                />
              </label>
              <p style={{ fontSize: 12, color: "var(--muted)", margin: 0 }}>
                Accepts a repo URL (SKILL.md at the root) or a tree URL into a
                sub-directory (e.g. <code>github.com/anthropics/skills/tree/main/skills/docx</code>).
                Imported skills land private; publish them from the /skills tab.
              </p>
            </>
          ) : (
            <>
              <div className="skill-editor-label">
                <span>Skill folder</span>
                {/* The native input stays hidden: its own "N files" count
                    includes the clutter skipped above, and would disagree
                    with the count shown below. */}
                <input
                  key="folder"
                  ref={(el) => {
                    folderInput.current = el;
                    // Not in React's typings; makes the picker choose a folder
                    // and fill in each file's `webkitRelativePath`.
                    el?.setAttribute("webkitdirectory", "");
                  }}
                  type="file"
                  hidden
                  onChange={(e) => {
                    pickFolder(e.target.files);
                    // So picking the same folder again still fires `change`.
                    e.target.value = "";
                  }}
                />
                <div>
                  <button
                    type="button"
                    className="button ghost"
                    onClick={() => folderInput.current?.click()}
                    disabled={busy}
                  >
                    {folderName ? "Choose another folder…" : "Choose folder…"}
                  </button>
                </div>
              </div>
              <p style={{ fontSize: 12, color: "var(--muted)", margin: 0 }}>
                {folderName
                  ? `${folderName}: ${files.length} file${files.length === 1 ? "" : "s"} selected.`
                  : "Pick the folder that holds SKILL.md at its top level."}{" "}
                Scripts, references and assets beside it are imported too.
                Imported skills land private; publish them from the /skills tab.
              </p>
            </>
          )}
          <div className="skill-editor-actions">
            <button
              type="button"
              className="button"
              disabled={busy || !canSubmit}
              onClick={() => void submit()}
            >
              {busy ? "Importing…" : source === "output" && picked?.exists ? "Replace" : "Import"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
