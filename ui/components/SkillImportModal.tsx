"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { SkillSummary } from "@/lib/types";

type Props = {
  open: boolean;
  /**
   * Project the imported skill is loaded into; its output folder is also the
   * "From project output" source.
   */
  project: string | null;
  onCancel: () => void;
  /**
   * Skills just imported. `done` is false when some of a repository's chosen
   * skills were skipped or failed, so the modal stays open to say which.
   */
  onImported: (skills: SkillSummary[], done: boolean) => void;
};

type Source = "repo" | "local" | "output";

/** One skill found in a repository (backend `ScannedSkill`). */
type ScannedSkill = {
  path: string;
  url: string;
  name: string | null;
  description: string | null;
  error: string | null;
  warnings: string[];
  included: string[];
  hpc_jobs: string[];
  files: number;
  exists: boolean;
  replaceable: boolean;
};

/** What `/api/skills/import/scan` found (backend `RepoScan`). */
type RepoScan = {
  url: string;
  repository: string;
  ref: string;
  commit: string;
  path: string;
  skills: ScannedSkill[];
};

type OnConflict = "skip" | "overwrite" | "rename";
type Choice = { on_conflict: OnConflict; rename_to: string };

/** One chosen skill's outcome (backend `SkillImportResult`). */
type ImportResult = {
  path: string;
  name: string | null;
  status: "imported" | "overwritten" | "renamed" | "skipped" | "failed";
  detail: string | null;
  skill: SkillSummary | null;
};

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
  const [source, setSource] = useState<Source>("repo");
  const [url, setUrl] = useState("");
  const [token, setToken] = useState("");
  const [scan, setScan] = useState<RepoScan | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [choices, setChoices] = useState<Record<string, Choice>>({});
  const [results, setResults] = useState<ImportResult[] | null>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [outputSkills, setOutputSkills] = useState<OutputSkill[] | null>(null);
  const [outputFolder, setOutputFolder] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const folderInput = useRef<HTMLInputElement | null>(null);

  // Fetched whenever the modal opens on this tab, not only on a tab click: it
  // stays mounted while closed, and the agent may have written more since.
  const loadOutputSkills = useCallback(async () => {
    if (!project) return;
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
  }, [project]);

  useEffect(() => {
    if (open && source === "output") void loadOutputSkills();
  }, [open, source, loadOutputSkills]);

  if (!open) return null;

  const folderName = files.length > 0 ? relativePath(files[0]).split("/")[0] : null;
  const picked = outputSkills?.find((s) => s.folder === outputFolder) ?? null;
  const importable = scan?.skills.filter((s) => !s.error) ?? [];
  const canSubmit =
    source === "repo"
      ? scan
        ? selected.size > 0
        : url.trim().length > 0
      : source === "local"
        ? files.length > 0
        : picked !== null && !picked.error && (!picked.exists || picked.replaceable);

  function pickFolder(list: FileList | null) {
    setError(null);
    setFiles(Array.from(list ?? []).filter((file) => !isIgnored(relativePath(file))));
  }

  function resetRepo() {
    setScan(null);
    setSelected(new Set());
    setChoices({});
    setResults(null);
  }

  function choiceFor(s: ScannedSkill): Choice {
    return choices[s.path] ?? { on_conflict: s.replaceable ? "overwrite" : "skip", rename_to: "" };
  }

  async function postJson(path: string, body: unknown): Promise<unknown> {
    const resp = await fetch(path, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    const payload = await readPayload(resp);
    if (!resp.ok) throw new Error(errorDetail(resp, payload));
    return payload;
  }

  async function importChosen(found: RepoScan, paths: string[]) {
    const skills = paths.map((path) => {
      const s = found.skills.find((x) => x.path === path)!;
      if (!s.exists) return { path };
      const c = choiceFor(s);
      return c.on_conflict === "rename"
        ? { path, on_conflict: c.on_conflict, rename_to: c.rename_to.trim() }
        : { path, on_conflict: c.on_conflict };
    });
    const out = (await postJson("/api/skills/import/batch", {
      url: found.url,
      commit: found.commit,
      project,
      token: token.trim() || undefined,
      skills,
    })) as ImportResult[];
    const imported = out.flatMap((r) => (r.skill ? [r.skill] : []));
    const done = out.every((r) => r.skill !== null);
    if (done) {
      setUrl("");
      setToken("");
      resetRepo();
    } else {
      setResults(out);
      // What is left to retry: the chosen skills that did not go in.
      setSelected(new Set(out.filter((r) => r.status === "failed").map((r) => r.path)));
    }
    if (imported.length > 0 || done) onImported(imported, done);
  }

  /** Scan the URL; one skill that can go straight in does, otherwise list them. */
  async function submitRepo() {
    if (scan) {
      await importChosen(scan, [...selected]);
      return;
    }
    const found = (await postJson("/api/skills/import/scan", {
      url: url.trim(),
      token: token.trim() || undefined,
    })) as RepoScan;
    const only = found.skills.length === 1 ? found.skills[0] : null;
    if (only && !only.error && !only.exists && only.warnings.length === 0) {
      await importChosen(found, [only.path]);
      return;
    }
    setScan(found);
    setSelected(new Set(found.skills.filter((s) => !s.error && !s.exists).map((s) => s.path)));
  }

  function request(): Promise<Response> {
    if (source === "output") {
      return fetch(`/api/projects/${encodeURIComponent(project ?? "")}/skills/from-output`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ folder: outputFolder, replace: picked?.exists ?? false }),
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
      if (source === "repo") {
        await submitRepo();
        return;
      }
      const resp = await request();
      const payload = await readPayload(resp);
      if (!resp.ok) {
        setError(errorDetail(resp, payload));
        return;
      }
      if (source === "output") {
        setOutputSkills(null);
        setOutputFolder(null);
      }
      onImported([payload as SkillSummary], true);
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
          if (value === "output" && source !== "output") {
            setOutputSkills(null);
            setOutputFolder(null);
          }
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
            {tab("repo", "Repository")}
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
          ) : source === "repo" ? (
            <>
              <label className="skill-editor-label">
                <span>Repository URL</span>
                <input
                  key="url"
                  className="input"
                  autoFocus
                  placeholder="https://github.com/owner/repo  or  .../tree/main/path/to/skills"
                  value={url}
                  onChange={(e) => {
                    setUrl(e.target.value);
                    if (scan) resetRepo();
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !scan) void submit();
                  }}
                  disabled={busy}
                />
              </label>
              <label className="skill-editor-label">
                <span>Access token (optional)</span>
                <input
                  key="token"
                  className="input"
                  type="password"
                  autoComplete="off"
                  placeholder="For a private repository; used for this import only, not saved"
                  value={token}
                  onChange={(e) => {
                    setToken(e.target.value);
                    if (scan) resetRepo();
                  }}
                  disabled={busy}
                />
              </label>
              {scan ? (
                <RepoSkillList
                  scan={scan}
                  importable={importable}
                  selected={selected}
                  setSelected={setSelected}
                  choiceFor={choiceFor}
                  setChoice={(path, c) => setChoices((prev) => ({ ...prev, [path]: c }))}
                  results={results}
                  busy={busy}
                />
              ) : (
                <p style={{ fontSize: 12, color: "var(--muted)", margin: 0 }}>
                  A GitHub or GitLab repository, or a folder in one (
                  <code>/tree/&lt;branch&gt;/&lt;path&gt;</code>, GitLab&apos;s{" "}
                  <code>/-/tree/…</code>). Every <code>SKILL.md</code> in it is found, and
                  you choose which to import. Without a token here, your GitHub or
                  gitlab.com token from Settings is used. Imported skills land private;
                  publish them from the /skills tab.
                </p>
              )}
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
              {busy
                ? source === "repo" && !scan
                  ? "Looking…"
                  : "Importing…"
                : source === "output" && picked?.exists
                  ? "Replace"
                  : source === "repo" && scan
                    ? `Import ${selected.size} skill${selected.size === 1 ? "" : "s"}`
                    : "Import"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

async function readPayload(resp: Response): Promise<unknown> {
  const text = await resp.text();
  try {
    return text ? JSON.parse(text) : null;
  } catch {
    return { error: text };
  }
}

function errorDetail(resp: Response, payload: unknown): string {
  if (payload && typeof payload === "object") {
    if ("detail" in payload) return String((payload as { detail: unknown }).detail);
    if ("error" in payload) return String((payload as { error: unknown }).error);
  }
  return `Import failed (${resp.status}).`;
}

const STATUS_LABEL: Record<ImportResult["status"], string> = {
  imported: "Imported",
  overwritten: "Replaced",
  renamed: "Imported as",
  skipped: "Skipped",
  failed: "Failed",
};

/** The skills a scan found, to choose from, with a conflict choice for each taken name. */
function RepoSkillList({
  scan,
  importable,
  selected,
  setSelected,
  choiceFor,
  setChoice,
  results,
  busy,
}: {
  scan: RepoScan;
  importable: ScannedSkill[];
  selected: Set<string>;
  setSelected: (s: Set<string>) => void;
  choiceFor: (s: ScannedSkill) => Choice;
  setChoice: (path: string, c: Choice) => void;
  results: ImportResult[] | null;
  busy: boolean;
}) {
  const allChosen = importable.length > 0 && importable.every((s) => selected.has(s.path));
  const muted = { fontSize: 12, color: "var(--muted)" } as const;
  function toggle(path: string, on: boolean) {
    const next = new Set(selected);
    if (on) next.add(path);
    else next.delete(path);
    setSelected(next);
  }
  return (
    <div className="skill-editor-label">
      <span>
        {scan.skills.length} skill{scan.skills.length === 1 ? "" : "s"} in {scan.repository}
        {scan.path ? `/${scan.path}` : ""} at {scan.ref}
      </span>
      {importable.length > 1 && (
        <label className="skill-output-row">
          <input
            type="checkbox"
            checked={allChosen}
            disabled={busy}
            onChange={(e) =>
              setSelected(new Set(e.target.checked ? importable.map((s) => s.path) : []))
            }
          />
          <span>Select all</span>
        </label>
      )}
      <div className="skill-output-list" style={{ maxHeight: 320 }}>
        {scan.skills.map((s) => {
          const result = results?.find((r) => r.path === s.path);
          const c = choiceFor(s);
          return (
            <div key={s.path} className="skill-output-row">
              <input
                type="checkbox"
                aria-label={`Import ${s.name ?? s.path}`}
                checked={selected.has(s.path)}
                disabled={busy || Boolean(s.error)}
                onChange={(e) => toggle(s.path, e.target.checked)}
              />
              <span style={{ minWidth: 0 }}>
                <strong>{s.name ?? s.path}</strong>
                {s.hpc_jobs.length > 0 && (
                  <span className="skill-hpc" style={{ marginLeft: 8 }}>
                    Runs HPC jobs: {s.hpc_jobs.join(", ")}
                  </span>
                )}
                {s.description && <span className="skill-desc" style={{ display: "block" }}>{s.description}</span>}
                <span style={{ display: "block", ...muted }}>
                  <a href={s.url} target="_blank" rel="noreferrer">
                    {s.path || "(repository root)"}
                  </a>
                  {" · "}
                  {s.files} file{s.files === 1 ? "" : "s"}
                </span>
                {s.error && <span className="error" style={{ display: "block" }}>{s.error}</span>}
                {s.included.length > 0 && (
                  <span style={{ display: "block", ...muted }}>
                    Also copied in: {s.included.join("; ")}
                  </span>
                )}
                {s.warnings.map((w) => (
                  <span key={w} style={{ display: "block", fontSize: 12, color: "var(--warning-ink)" }}>
                    ⚠ {w}
                  </span>
                ))}
                {s.exists && !s.error && (
                  <span style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap", ...muted }}>
                    {s.name} is taken:
                    <select
                      className="input"
                      style={{ width: "auto", padding: "2px 6px" }}
                      value={c.on_conflict}
                      disabled={busy}
                      onChange={(e) => setChoice(s.path, { ...c, on_conflict: e.target.value as OnConflict })}
                    >
                      <option value="skip">Skip</option>
                      {s.replaceable && <option value="overwrite">Replace my private skill</option>}
                      <option value="rename">Import under another name</option>
                    </select>
                    {c.on_conflict === "rename" && (
                      <input
                        className="input"
                        style={{ width: 180, padding: "2px 6px" }}
                        placeholder={`${s.name}-2`}
                        value={c.rename_to}
                        disabled={busy}
                        onChange={(e) => setChoice(s.path, { ...c, rename_to: e.target.value })}
                      />
                    )}
                    {!s.replaceable && <span>(published, so it cannot be replaced)</span>}
                  </span>
                )}
                {result && (
                  <span
                    style={{
                      display: "block",
                      fontSize: 12,
                      color: result.status === "failed" ? "var(--danger-ink)" : "var(--muted)",
                    }}
                  >
                    {STATUS_LABEL[result.status]}
                    {result.status === "renamed" ? ` ${result.name}` : ""}
                    {result.detail ? `: ${result.detail}` : ""}
                  </span>
                )}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
