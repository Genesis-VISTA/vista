"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { refreshProjects, useActiveProject } from "@/lib/projects";
import { setSkillLoaded } from "@/lib/loaded-skills";
import { useCollaboration } from "@/lib/collaboration";
import {
  importAction,
  importRemoteSkill,
  listRemoteSkills,
  type RemoteSkill,
} from "@/lib/skill-sharing";
import { AppTopBar } from "@/components/AppTopBar";
import { HpcJobsBadge } from "@/components/HpcJobsBadge";
import { PublishConfirmModal } from "@/components/PublishConfirmModal";

const TAG_FILTERS = [
  "All",
  "OLCF",
  "Frontier",
  "Materials Design",
  "Molten Salt Tritium Breeding",
  "High Entropy Alloy Design",
  "Data",
] as const;

type TagFilter = typeof TAG_FILTERS[number];
type SortKey = "name" | "recent";

function tagsOf(skill: RemoteSkill): string[] {
  const tags = skill.manifest?.metadata?.tags;
  return Array.isArray(tags) ? tags : [];
}

/**
 * The Skill Hub: the skills published to the collaboration server, to import
 * into this computer (and load into the active project). Without a server that
 * answers, the page says why and lists nothing.
 */
export default function SkillHubPage() {
  const activeProject = useActiveProject();
  const collab = useCollaboration();
  const [skills, setSkills] = useState<RemoteSkill[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState("");
  const [activeTag, setActiveTag] = useState<TagFilter>("All");
  const [sort, setSort] = useState<SortKey>("name");
  const [selected, setSelected] = useState<RemoteSkill | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [renaming, setRenaming] = useState<{ skill: RemoteSkill; name: string } | null>(null);
  const [replacing, setReplacing] = useState<RemoteSkill | null>(null);

  const loadedSlugs = useMemo(
    () => new Set(activeProject?.skills ?? []),
    [activeProject]
  );

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      setSkills(await listRemoteSkills());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "The skills could not be listed.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (collab.available) void refresh();
  }, [collab.available, refresh]);

  async function runImport(
    skill: RemoteSkill,
    opts: { renameTo?: string; replaceModified?: boolean } = {},
  ) {
    setBusyId(skill.id);
    setNotice(null);
    try {
      const result = await importRemoteSkill(skill.id, {
        ...opts,
        project: activeProject?.name ?? null,
      });
      if (result.ok) {
        const where = activeProject ? ` and loaded it into ${activeProject.name}` : "";
        setNotice(
          result.status === "unchanged"
            ? `${result.skill.slug} is already here${where}.`
            : `Imported ${result.skill.slug} version ${result.skill.remoteVersion}${where}.`,
        );
        if (activeProject) void refreshProjects();
        await refresh();
        return;
      }
      if (result.code === "name_taken") {
        setRenaming({ skill, name: `${skill.name}-2` });
        return;
      }
      if (result.code === "modified") {
        setReplacing(skill);
        return;
      }
      setNotice(result.detail);
    } finally {
      setBusyId(null);
    }
  }

  function onAction(skill: RemoteSkill) {
    const action = importAction(skill);
    if (action.kind === "rename") setRenaming({ skill, name: `${skill.name}-2` });
    else if (skill.local?.modified) setReplacing(skill);
    else void runImport(skill);
  }

  async function toggleLoaded(name: string) {
    if (!activeProject) return;
    try {
      await setSkillLoaded(activeProject, name, !loadedSlugs.has(name));
    } catch (err) {
      window.alert(`Failed to update the project: ${err instanceof Error ? err.message : err}`);
    }
  }

  const visible = useMemo(() => {
    const term = search.trim().toLowerCase();
    let list = skills.filter((skill) => {
      if (activeTag !== "All" && !tagsOf(skill).includes(activeTag)) return false;
      if (!term) return true;
      return (
        skill.name.toLowerCase().includes(term) ||
        (skill.manifest?.description ?? "").toLowerCase().includes(term) ||
        tagsOf(skill).some((t) => t.toLowerCase().includes(term))
      );
    });
    if (sort === "name") {
      list = [...list].sort((a, b) => a.name.localeCompare(b.name));
    } else {
      // Most recently published first.
      list = [...list].sort((a, b) =>
        (b.published_at ?? b.created_at).localeCompare(a.published_at ?? a.created_at),
      );
    }
    return list;
  }, [skills, search, activeTag, sort]);

  return (
    <div className="app-page">
      <AppTopBar title="Skill Hub" />
      <div className="app-page-body hub-page">
      <p className="page-lede">
        Skills published to the collaboration server
        {collab.status?.url ? ` at ${collab.status.url}` : ""}. Importing one is
        your decision to trust it: review what it does, and the HPC jobs it
        ships, before loading it.
      </p>

      {!collab.available ? (
        <div className="hub-unavailable" role="status">
          {collab.reason}
        </div>
      ) : (
        <>
          <div className="hub-controls">
            <input
              className="hub-search"
              placeholder='Search skills... (press "/" to focus)'
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "/" && document.activeElement !== e.currentTarget) {
                  e.preventDefault();
                  e.currentTarget.focus();
                }
              }}
            />
            <select
              className="hub-sort"
              value={sort}
              onChange={(e) => setSort(e.target.value as SortKey)}
            >
              <option value="name">Name (A–Z)</option>
              <option value="recent">Recently Published</option>
            </select>
          </div>

          <div className="hub-tags" role="tablist" aria-label="Tag filters">
            {TAG_FILTERS.map((tag) => (
              <button
                key={tag}
                type="button"
                role="tab"
                aria-selected={activeTag === tag}
                className={`hub-tag${activeTag === tag ? " active" : ""}`}
                onClick={() => setActiveTag(tag)}
              >
                {tag}
              </button>
            ))}
          </div>

          {notice && <div className="hub-count" role="status">{notice}</div>}
          {error ? (
            <div className="hub-unavailable" role="alert">{error}</div>
          ) : (
            <>
              <div className="hub-count">
                {loading ? "Loading…" : `${visible.length} skills on the server`}
              </div>
              <div className="hub-grid">
                {visible.length === 0 ? (
                  <div className="hub-empty">
                    {skills.length === 0 && !loading
                      ? "No skills have been published yet. Publish one from the Skills page."
                      : "No skills match your filters."}
                  </div>
                ) : (
                  visible.map((skill) => {
                    const action = importAction(skill);
                    const busy = busyId === skill.id;
                    const local = skill.local;
                    const isLoaded = local ? loadedSlugs.has(local.name) : false;
                    return (
                      <div
                        key={skill.id}
                        className="hub-card"
                        onClick={() => setSelected(skill)}
                        role="button"
                        tabIndex={0}
                        onKeyDown={(e) => {
                          if (e.key === "Enter" || e.key === " ") {
                            e.preventDefault();
                            setSelected(skill);
                          }
                        }}
                      >
                        <div className="hub-card-head">
                          <div className="hub-card-name">
                            {skill.name}
                            <HpcJobsBadge jobs={skill.manifest?.hpc_jobs} />
                          </div>
                          <div style={{ display: "flex", gap: 6 }}>
                            {local && (
                              <button
                                type="button"
                                className={`hub-card-action${isLoaded ? " loaded" : ""}`}
                                disabled={!activeProject}
                                title={activeProject ? undefined : "Open a project to load skills into it"}
                                onClick={(e) => {
                                  e.stopPropagation();
                                  void toggleLoaded(local.name);
                                }}
                                aria-pressed={isLoaded}
                              >
                                {isLoaded ? "Loaded ✓" : "Load"}
                              </button>
                            )}
                            <button
                              type="button"
                              className={`hub-card-action${action.kind === "current" ? " loaded" : ""}`}
                              disabled={busy || action.kind === "current"}
                              onClick={(e) => {
                                e.stopPropagation();
                                onAction(skill);
                              }}
                            >
                              {busy ? "Importing…" : action.label}
                            </button>
                          </div>
                        </div>
                        <div className="hub-card-desc">
                          {skill.manifest?.description || "No description"}
                        </div>
                        <div className="hub-card-meta">
                          <span className="hub-card-author">
                            Version {skill.head_version}
                            {skill.published_by_name ? ` by ${skill.published_by_name}` : ""}
                          </span>
                          {local && local.name !== skill.name && (
                            <>
                              <span className="hub-card-meta-sep">·</span>
                              <span>here as {local.name}</span>
                            </>
                          )}
                          {local?.modified && (
                            <>
                              <span className="hub-card-meta-sep">·</span>
                              <span>changed here</span>
                            </>
                          )}
                          {skill.manifest?.repo_url && (
                            <>
                              <span className="hub-card-meta-sep">·</span>
                              <a
                                href={skill.manifest.repo_url}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="hub-card-repo"
                                onClick={(e) => e.stopPropagation()}
                              >
                                repo ↗
                              </a>
                            </>
                          )}
                        </div>
                        {tagsOf(skill).length > 0 && (
                          <div className="hub-card-tags">
                            {tagsOf(skill).map((tag) => (
                              <span key={tag} className="hub-card-tag">
                                {tag}
                              </span>
                            ))}
                          </div>
                        )}
                      </div>
                    );
                  })
                )}
              </div>
            </>
          )}
        </>
      )}
      </div>

      {selected && (
        <div className="modal-backdrop" onClick={() => setSelected(null)}>
          <div
            className="modal"
            onClick={(e) => e.stopPropagation()}
            role="dialog"
            aria-label={`Skill details: ${selected.name}`}
          >
            <div className="panel-header">
              <div className="panel-title">
                {selected.name}
                <HpcJobsBadge jobs={selected.manifest?.hpc_jobs} />
              </div>
              <button
                type="button"
                className="button ghost"
                onClick={() => setSelected(null)}
              >
                Close
              </button>
            </div>
            <div className="modal-body">
              <p>{selected.manifest?.description || "No description"}</p>
              <dl className="hub-detail">
                <dt>Newest version</dt>
                <dd>
                  {selected.head_version}
                  {selected.published_at ? `, published ${selected.published_at.slice(0, 10)}` : ""}
                  {selected.published_by_name ? ` by ${selected.published_by_name}` : ""}
                </dd>
                <dt>First published by</dt>
                <dd>{selected.created_by_name ?? "unknown"}</dd>
                {selected.manifest?.license && (
                  <>
                    <dt>License</dt>
                    <dd>{selected.manifest.license}</dd>
                  </>
                )}
                {selected.manifest?.compatibility && (
                  <>
                    <dt>Compatibility</dt>
                    <dd>{selected.manifest.compatibility}</dd>
                  </>
                )}
                {selected.manifest?.allowed_tools && (
                  <>
                    <dt>Allowed tools</dt>
                    <dd>{selected.manifest.allowed_tools}</dd>
                  </>
                )}
                {selected.manifest && (
                  <>
                    <dt>Files</dt>
                    <dd>
                      {selected.manifest.files} ({Math.max(1, Math.round(selected.manifest.size / 1024))} KB)
                    </dd>
                  </>
                )}
                {selected.local && (
                  <>
                    <dt>Here</dt>
                    <dd>
                      {selected.local.name}, version {selected.local.remote_version}
                      {selected.local.modified ? ", changed here" : ""}
                    </dd>
                  </>
                )}
              </dl>
            </div>
          </div>
        </div>
      )}

      {renaming && (
        <div className="modal-backdrop" onClick={() => setRenaming(null)}>
          <div
            className="modal"
            onClick={(e) => e.stopPropagation()}
            role="dialog"
            aria-label="Import under a new name"
          >
            <div className="panel-header">
              <div className="panel-title">Import under a new name</div>
              <button type="button" className="button ghost" onClick={() => setRenaming(null)}>
                Cancel
              </button>
            </div>
            <form
              className="modal-body"
              style={{ display: "flex", flexDirection: "column", gap: 12 }}
              onSubmit={(e) => {
                e.preventDefault();
                const { skill, name } = renaming;
                setRenaming(null);
                void runImport(skill, { renameTo: name.trim() });
              }}
            >
              <p style={{ margin: 0 }}>
                A different skill named {renaming.skill.name} is already on this
                computer. Choose a name for this one.
              </p>
              <input
                className="input"
                aria-label="New name"
                value={renaming.name}
                onChange={(e) => setRenaming({ ...renaming, name: e.target.value })}
              />
              <div>
                <button type="submit" className="button" disabled={!renaming.name.trim()}>
                  Import
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      <PublishConfirmModal
        open={replacing !== null}
        title="Replace your changes?"
        message={replacing
          ? `${replacing.local?.name ?? replacing.name} has changes made here since version ${replacing.local?.remote_version}. Importing version ${replacing.head_version} replaces them.`
          : ""}
        confirmLabel="Replace my changes"
        cancelActionLabel="Keep my changes"
        onCancel={() => setReplacing(null)}
        onConfirm={() => {
          if (!replacing) return;
          const skill = replacing;
          setReplacing(null);
          void runImport(skill, { replaceModified: true });
        }}
      />
    </div>
  );
}
