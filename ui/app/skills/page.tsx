"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { refreshProjects, useActiveProject } from "@/lib/projects";
import { setSkillLoaded } from "@/lib/loaded-skills";
import { useCollaboration } from "@/lib/collaboration";
import {
  importRemoteSkill,
  isOnServer,
  listRemoteSkills,
  publishSkill,
  sharingLabel,
} from "@/lib/skill-sharing";
import { AppTopBar } from "@/components/AppTopBar";
import { ProjectRequired } from "@/components/ProjectRequired";
import { SkillImportModal } from "@/components/SkillImportModal";
import { PublishConfirmModal } from "@/components/PublishConfirmModal";
import { PublishConflictModal, type Conflict } from "@/components/PublishConflictModal";
import { HpcJobsBadge } from "@/components/HpcJobsBadge";
import { useTour } from "@/components/TourProvider";
import type { SkillDetail, SkillSummary } from "@/lib/types";

export default function SkillsPage() {
  const activeProject = useActiveProject();
  const projectName = activeProject?.name ?? null;
  const collab = useCollaboration();
  const serverUrl = collab.status?.reachable ? collab.status.url : null;
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  const [filter, setFilter] = useState("");
  const [selected, setSelected] = useState<SkillDetail | null>(null);
  const tour = useTour();
  const [busySlug, setBusySlug] = useState<string | null>(null);
  const [showImport, setShowImport] = useState(false);
  const [pendingPublish, setPendingPublish] = useState<SkillSummary | null>(null);
  const [pendingDelete, setPendingDelete] = useState<SkillSummary | null>(null);
  const [pendingUpdate, setPendingUpdate] = useState<SkillSummary | null>(null);
  const [conflict, setConflict] = useState<Conflict | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  /** The server's newest version of each item, by id, for "version N available". */
  const [heads, setHeads] = useState<Record<string, number>>({});

  useEffect(() => {
    fetch("/api/skills")
      .then((res) => res.json())
      .then((data) => setSkills(Array.isArray(data) ? data : []))
      .catch(() => setSkills([]));
  }, []);

  const refreshHeads = useCallback(() => {
    listRemoteSkills({ manifests: false })
      .then((remote) => setHeads(Object.fromEntries(remote.map((r) => [r.id, r.head_version]))))
      .catch(() => setHeads({}));
  }, []);

  useEffect(() => {
    if (collab.available) refreshHeads();
  }, [collab.available, refreshHeads]);

  const loadedSlugs = useMemo(
    () => new Set(activeProject?.skills ?? []),
    [activeProject]
  );

  function replaceSkill(updated: SkillSummary) {
    setSkills((prev) => {
      const rest = prev.filter((s) => s.slug !== updated.slug);
      return [...rest, updated].sort((a, b) => a.slug.localeCompare(b.slug));
    });
  }

  async function setLoaded(slug: string, loaded: boolean) {
    if (!activeProject) return;
    try {
      await setSkillLoaded(activeProject, slug, loaded);
    } catch (err) {
      window.alert(`Failed to update the project: ${err instanceof Error ? err.message : err}`);
    }
  }

  async function publish(skill: SkillSummary, opts: { onTopOf?: number; asNew?: boolean } = {}) {
    setBusySlug(skill.slug);
    setNotice(null);
    try {
      const result = await publishSkill(skill.slug, opts);
      if (result.ok) {
        replaceSkill(result.skill);
        setNotice(`Published ${skill.slug} as version ${result.skill.remoteVersion}.`);
        refreshHeads();
        return;
      }
      if (result.code === "version_conflict" || result.code === "remote_missing") {
        setConflict({ skill, refusal: result });
        return;
      }
      setNotice(result.detail);
    } finally {
      setBusySlug(null);
    }
  }

  /**
   * Replace the files here with the server's version `version` (the one the
   * researcher was shown), or its newest.
   */
  async function takeServers(skill: SkillSummary, version?: number) {
    if (!skill.remoteId) return;
    setBusySlug(skill.slug);
    setNotice(null);
    try {
      const result = await importRemoteSkill(skill.remoteId, { version, replaceModified: true });
      if (result.ok) {
        replaceSkill(result.skill);
        setNotice(`${skill.slug} is now the server's version ${result.skill.remoteVersion}.`);
        refreshHeads();
      } else {
        setNotice(result.detail);
      }
    } finally {
      setBusySlug(null);
    }
  }

  async function deleteSkill(skill: SkillSummary) {
    setBusySlug(skill.slug);
    try {
      const resp = await fetch(`/api/skills/${skill.slug}`, { method: "DELETE" });
      if (!resp.ok) {
        // The proxy wraps the backend's answer, `{"detail": …}`, as its `error`.
        const body = (await resp.json().catch(() => ({}))) as { error?: string };
        let message = body.error ?? "";
        try {
          message = String((JSON.parse(message) as { detail?: unknown }).detail ?? message);
        } catch {
          // not JSON: shown as it is
        }
        setNotice(message || `Failed to delete ${skill.slug}.`);
        return;
      }
      setSkills((prev) => prev.filter((s) => s.slug !== skill.slug));
    } finally {
      setBusySlug(null);
    }
  }

  async function openDetail(slug: string) {
    try {
      const resp = await fetch(`/api/skills/${slug}`);
      if (!resp.ok) return;
      const detail = (await resp.json()) as SkillDetail;
      setSelected(detail);
      tour.report("skill-opened");
    } catch {
      // ignore
    }
  }

  // The tour explains the open skill, then waits for it to be closed, so it
  // moves on to Load only once the researcher is done reading.
  function closeDetail() {
    setSelected(null);
    tour.report("skill-closed");
  }

  const term = filter.trim().toLowerCase();
  const matches = useCallback(
    (s: SkillSummary) =>
      !term ||
      s.slug.toLowerCase().includes(term) ||
      s.name.toLowerCase().includes(term) ||
      s.description.toLowerCase().includes(term),
    [term]
  );
  const loadedSkills = useMemo(
    () => skills.filter((s) => loadedSlugs.has(s.slug) && matches(s)),
    [skills, loadedSlugs, matches]
  );
  const otherSkills = useMemo(
    () => skills.filter((s) => !loadedSlugs.has(s.slug) && matches(s)),
    [skills, loadedSlugs, matches]
  );

  // The cards the tour points at: the first skill, and the first Load button.
  const tourOpenSlug = (loadedSkills[0] ?? otherSkills[0])?.slug;
  const tourLoadSlug = otherSkills[0]?.slug;

  function card(skill: SkillSummary, loaded: boolean) {
    const busy = busySlug === skill.slug;
    const onServer = isOnServer(skill, serverUrl);
    const head = skill.remoteId ? heads[skill.remoteId] : undefined;
    const behind = onServer && head !== undefined && head > (skill.remoteVersion ?? 0);
    const canPublish = !skill.bundled && (!onServer || skill.modified);
    return (
      <div
        key={skill.slug}
        className="skill-item"
        data-tour={skill.slug === tourOpenSlug ? "skills:open-skill" : undefined}
        onClick={() => openDetail(skill.slug)}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            openDetail(skill.slug);
          }
        }}
      >
        <div className="skill-name">
          {skill.name}
          <HpcJobsBadge jobs={skill.hpcJobs} />
        </div>
        <div className="skill-desc">{skill.description || "No description"}</div>
        <div className="skill-meta">
          <span className="skill-sharing">{sharingLabel(skill, serverUrl, head)}</span>
          {skill.repoUrl && (
            <>
              <span className="skill-meta-sep">·</span>
              <a
                href={skill.repoUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="skill-repo"
                onClick={(e) => e.stopPropagation()}
              >
                repo ↗
              </a>
            </>
          )}
        </div>
        <div className="skill-actions">
          {canPublish && (
            <button
              type="button"
              className="button button-sm"
              disabled={busy || !collab.available}
              onClick={(e) => {
                e.stopPropagation();
                if (onServer) void publish(skill);
                else setPendingPublish(skill);
              }}
              title={
                collab.available
                  ? "Publish this skill to the collaboration server"
                  : (collab.reason ?? undefined)
              }
            >
              {onServer ? "Publish changes" : "Publish"}
            </button>
          )}
          {behind && (
            <button
              type="button"
              className="button secondary button-sm"
              disabled={busy || !collab.available}
              onClick={(e) => {
                e.stopPropagation();
                if (skill.modified) setPendingUpdate(skill);
                else void takeServers(skill);
              }}
              title={collab.available ? `Import version ${head}` : (collab.reason ?? undefined)}
            >
              Update to version {head}
            </button>
          )}
          {loaded ? (
            <button
              type="button"
              className="button ghost button-sm"
              disabled={busy}
              onClick={(e) => {
                e.stopPropagation();
                void setLoaded(skill.slug, false);
              }}
            >
              Unload
            </button>
          ) : (
            <>
              <button
                type="button"
                className="button secondary button-sm"
                disabled={busy}
                data-tour={skill.slug === tourLoadSlug ? "skills:load" : undefined}
                onClick={(e) => {
                  e.stopPropagation();
                  void setLoaded(skill.slug, true);
                }}
              >
                Load
              </button>
              {!skill.bundled && (
                <button
                  type="button"
                  className="button ghost button-sm"
                  disabled={busy}
                  onClick={(e) => {
                    e.stopPropagation();
                    setPendingDelete(skill);
                  }}
                >
                  Delete
                </button>
              )}
            </>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="app-page">
      <AppTopBar
        title="Skills"
        actions={
          !projectName ? null : (
          <>
            <span className="tag">{loadedSlugs.size} loaded</span>
            <button
              type="button"
              className="button button-sm"
              onClick={() => setShowImport(true)}
              data-tour="skills:import"
              title="Import skills from a GitHub or GitLab repository, or a local folder, into this project"
              disabled={!projectName}
            >
              Import…
            </button>
          </>
          )
        }
      />
      <div className="app-page-body">
      {!projectName ? (
        <ProjectRequired what="Skills" />
      ) : (
      <section className="panel" style={{ height: "100%" }}>
        <div className="panel-body">
          <input
            className="input"
            placeholder="Search skills"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
          {notice && (
            <div className="skill-notice" role="status">
              {notice}
            </div>
          )}
          <h3 className="skill-section-title">Loaded in this project</h3>
          <div className="skill-section" data-tour="skills:loaded">
            {loadedSlugs.size === 0 ? (
              <div className="skill-empty">
                No skills loaded for this project. Load one from the skills on
                this computer below, or import one.
                <div style={{ marginTop: 10 }}>
                  <Link
                    href="/skill-hub"
                    className="button secondary button-sm"
                    style={{ textDecoration: "none" }}
                  >
                    Browse Skill Hub →
                  </Link>
                </div>
              </div>
            ) : (
              loadedSkills.map((skill) => card(skill, true))
            )}
          </div>
          <h3 className="skill-section-title">Other skills on this computer</h3>
          <div className="skill-section" data-tour="skills:available">
            {otherSkills.length === 0 ? (
              <div className="skill-empty">No other skills.</div>
            ) : (
              otherSkills.map((skill) => card(skill, false))
            )}
          </div>
        </div>
      </section>
      )}
      </div>

      {selected && (
        <div className="modal-backdrop" onClick={closeDetail}>
          <div className="modal" data-tour="skills:detail" onClick={(e) => e.stopPropagation()}>
            <div className="panel-header">
              <div className="panel-title">
                {typeof selected.frontmatter?.name === "string"
                  ? selected.frontmatter.name
                  : selected.slug}
                <HpcJobsBadge jobs={selected.hpcJobs} />
              </div>
              <button
                type="button"
                className="button ghost"
                onClick={closeDetail}
              >
                Close
              </button>
            </div>
            <div className="modal-body">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {selected.markdown}
              </ReactMarkdown>
            </div>
          </div>
        </div>
      )}

      <PublishConfirmModal
        open={pendingPublish !== null}
        title="Publish skill"
        message={pendingPublish
          ? `Publish "${pendingPublish.slug}" to the collaboration server at ${serverUrl ?? "?"}?\n\nAnyone who can reach that server can see and import it. A published version stays there; you can publish newer ones.`
          : ""}
        confirmLabel="Publish"
        onCancel={() => setPendingPublish(null)}
        onConfirm={() => {
          if (!pendingPublish) return;
          const skill = pendingPublish;
          setPendingPublish(null);
          void publish(skill);
        }}
      />
      <PublishConfirmModal
        open={pendingDelete !== null}
        title="Delete skill?"
        message={pendingDelete
          ? `Delete "${pendingDelete.slug}" from this computer?${
              isOnServer(pendingDelete, serverUrl) && !pendingDelete.modified
                  ? "\n\nIts published versions stay on the collaboration server, where you can import it again."
                  : "\n\nIts changes here are not published anywhere, so they cannot be recovered."
            }`
          : ""}
        confirmLabel="Delete"
        cancelActionLabel="Keep it"
        onCancel={() => setPendingDelete(null)}
        onConfirm={() => {
          if (!pendingDelete) return;
          const skill = pendingDelete;
          setPendingDelete(null);
          void deleteSkill(skill);
        }}
      />
      <PublishConfirmModal
        open={pendingUpdate !== null}
        title="Replace your changes?"
        message={pendingUpdate
          ? `"${pendingUpdate.slug}" has changes made here since version ${pendingUpdate.remoteVersion}. Importing the server's newest version replaces them.`
          : ""}
        confirmLabel="Replace my changes"
        cancelActionLabel="Keep my changes"
        onCancel={() => setPendingUpdate(null)}
        onConfirm={() => {
          if (!pendingUpdate) return;
          const skill = pendingUpdate;
          setPendingUpdate(null);
          void takeServers(skill);
        }}
      />
      {conflict && (
        <PublishConflictModal
          name={conflict.skill.slug}
          noun="skill"
          refusal={conflict.refusal}
          onCancel={() => setConflict(null)}
          onTakeServers={() => {
            setConflict(null);
            void takeServers(conflict.skill, conflict.refusal.headVersion ?? undefined);
          }}
          onPublishOnTop={(head) => {
            setConflict(null);
            void publish(conflict.skill, { onTopOf: head });
          }}
          onPublishAsNew={() => {
            setConflict(null);
            void publish(conflict.skill, { asNew: true });
          }}
        />
      )}

      <SkillImportModal
        open={showImport}
        project={projectName}
        onCancel={() => setShowImport(false)}
        onImported={(imported, done) => {
          // The backend loaded the new skills into the active project;
          // append them to the local catalog (de-dupe in case a refetch crossed
          // paths with the import) and refetch the project to show them loaded.
          const slugs = new Set(imported.map((s) => s.slug));
          setSkills((prev) => [...imported, ...prev.filter((s) => !slugs.has(s.slug))]);
          void refreshProjects();
          if (done) setShowImport(false);
        }}
      />
    </div>
  );
}
