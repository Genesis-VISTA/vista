"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { refreshProjects, useActiveProject } from "@/lib/projects";
import { setSkillLoaded } from "@/lib/loaded-skills";
import { AppTopBar } from "@/components/AppTopBar";
import { ProjectRequired } from "@/components/ProjectRequired";
import { SkillImportModal } from "@/components/SkillImportModal";
import { PublishConfirmModal } from "@/components/PublishConfirmModal";
import type { SkillDetail, SkillSummary } from "@/lib/types";

export default function SkillsPage() {
  const activeProject = useActiveProject();
  const projectName = activeProject?.name ?? null;
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  const [filter, setFilter] = useState("");
  const [selected, setSelected] = useState<SkillDetail | null>(null);
  const [busySlug, setBusySlug] = useState<string | null>(null);
  const [showImport, setShowImport] = useState(false);
  const [pendingPublishSlug, setPendingPublishSlug] = useState<string | null>(null);
  const [pendingUnloadSkill, setPendingUnloadSkill] = useState<SkillSummary | null>(null);

  useEffect(() => {
    fetch("/api/skills")
      .then((res) => res.json())
      .then((data) => setSkills(Array.isArray(data) ? data : []))
      .catch(() => setSkills([]));
  }, []);

  const loadedSlugs = useMemo(
    () => new Set(activeProject?.skills ?? []),
    [activeProject]
  );

  async function unload(slug: string) {
    if (!activeProject) return;
    try {
      await setSkillLoaded(activeProject, slug, false);
    } catch (err) {
      window.alert(`Failed to unload: ${err instanceof Error ? err.message : err}`);
    }
  }

  async function publish(slug: string) {
    setBusySlug(slug);
    try {
      const resp = await fetch(`/api/skills/${slug}`, {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ is_public: true }),
      });
      if (!resp.ok) {
        window.alert("Failed to publish. See server logs for details.");
        return;
      }
      setSkills((prev) =>
        prev.map((s) => (s.slug === slug ? { ...s, isPublic: true } : s))
      );
    } finally {
      setBusySlug(null);
    }
  }

  async function deleteUnpublishedSkill(slug: string) {
    setBusySlug(slug);
    try {
      const resp = await fetch(`/api/skills/${slug}`, {
        method: "DELETE",
      });
      if (!resp.ok) {
        window.alert("Failed to delete unpublished skill. See server logs for details.");
        return;
      }
      await unload(slug);
      setSkills((prev) => prev.filter((s) => s.slug !== slug));
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
    } catch {
      // ignore
    }
  }

  const loadedSkills = useMemo(() => {
    const term = filter.trim().toLowerCase();
    const list = skills.filter((s) => loadedSlugs.has(s.slug));
    if (!term) return list;
    return list.filter(
      (s) =>
        s.slug.toLowerCase().includes(term) ||
        s.name.toLowerCase().includes(term) ||
        s.description.toLowerCase().includes(term)
    );
  }, [skills, loadedSlugs, filter]);

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
              title="Import a skill from a GitHub repository or a local folder into this project"
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
            placeholder="Search loaded skills"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
          <div style={{ marginTop: 12, display: "flex", flexDirection: "column", gap: 8 }}>
            {loadedSlugs.size === 0 ? (
              <div
                style={{
                  fontSize: 13,
                  color: "var(--muted)",
                  padding: "20px 16px",
                  border: "1px dashed var(--line)",
                  borderRadius: 8,
                  textAlign: "center",
                }}
              >
                No skills loaded for this session.
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
              loadedSkills.map((skill) => (
                <div
                  key={skill.slug}
                  className="skill-item"
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
                  <div className="skill-name">{skill.name}</div>
                  <div className="skill-desc">{skill.description || "No description"}</div>
                  {(skill.author || skill.repoUrl) && (
                    <div className="skill-meta">
                      {skill.author && (
                        <span className="skill-author">by {skill.author}</span>
                      )}
                      {skill.author && skill.repoUrl && (
                        <span className="skill-meta-sep">·</span>
                      )}
                      {skill.repoUrl && (
                        <a
                          href={skill.repoUrl}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="skill-repo"
                          onClick={(e) => e.stopPropagation()}
                        >
                          repo ↗
                        </a>
                      )}
                    </div>
                  )}
                  <div className="skill-actions">
                    {skill.isPublic ? (
                      <span className="skill-published" title="Listed on the Skill Hub">
                        Published ✓
                      </span>
                    ) : (
                      <button
                        type="button"
                        className="button button-sm"
                        disabled={busySlug === skill.slug}
                        onClick={(e) => {
                          e.stopPropagation();
                          setPendingPublishSlug(skill.slug);
                        }}
                        title="Publish this skill to the Skill Hub (permanent)"
                      >
                        Publish
                      </button>
                    )}
                    <button
                      type="button"
                      className="button ghost button-sm"
                      disabled={busySlug === skill.slug}
                      onClick={(e) => {
                        e.stopPropagation();
                        if (skill.isPublic) {
                          void unload(skill.slug);
                          return;
                        }
                        setPendingUnloadSkill(skill);
                      }}
                    >
                      Unload
                    </button>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      </section>
      )}
      </div>

      {selected && (
        <div className="modal-backdrop" onClick={() => setSelected(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <div className="panel-header">
              <div className="panel-title">
                {typeof selected.frontmatter?.name === "string"
                  ? selected.frontmatter.name
                  : selected.slug}
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
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {selected.markdown}
              </ReactMarkdown>
            </div>
          </div>
        </div>
      )}

      <PublishConfirmModal
        open={pendingPublishSlug !== null}
        title="Publish skill to Skill Hub"
        message={pendingPublishSlug
          ? `Publish "${pendingPublishSlug}" to the Skill Hub?\n\nThis action is permanent - once published, a skill cannot be made private again.`
          : ""}
        confirmLabel="Publish"
        cancelActionLabel="Keep private"
        busy={pendingPublishSlug !== null && busySlug === pendingPublishSlug}
        onCancel={() => setPendingPublishSlug(null)}
        onConfirm={() => {
          if (!pendingPublishSlug) return;
          const slug = pendingPublishSlug;
          setPendingPublishSlug(null);
          void publish(slug);
        }}
      />
      <PublishConfirmModal
        open={pendingUnloadSkill !== null}
        title="Unload unpublished skill?"
        message={pendingUnloadSkill
          ? `Unload "${pendingUnloadSkill.slug}" without publishing?\n\nThis skill is not published to the Skill Hub. If you unload it now, it will disappear completely and you may not be able to recover it.`
          : ""}
        confirmLabel="Unload permanently"
        cancelActionLabel="Go back"
        onCancel={() => setPendingUnloadSkill(null)}
        onConfirm={() => {
          if (!pendingUnloadSkill) return;
          const slug = pendingUnloadSkill.slug;
          setPendingUnloadSkill(null);
          void deleteUnpublishedSkill(slug);
        }}
      />

      <SkillImportModal
        open={showImport}
        project={projectName}
        onCancel={() => setShowImport(false)}
        onImported={(skill) => {
          // The backend loaded the new private skill into the active project;
          // append it to the local catalog (de-dupe in case a refetch crossed
          // paths with the import) and refetch the project to show it loaded.
          setSkills((prev) => {
            const without = prev.filter((s) => s.slug !== skill.slug);
            return [skill, ...without];
          });
          void refreshProjects();
          setShowImport(false);
        }}
      />
    </div>
  );
}
