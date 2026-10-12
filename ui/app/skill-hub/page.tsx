"use client";

import { useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useActiveProject } from "@/lib/projects";
import { setSkillLoaded } from "@/lib/loaded-skills";
import type { SkillDetail, SkillSummary } from "@/lib/types";
import { AppTopBar } from "@/components/AppTopBar";

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

type HubSkill = SkillSummary & { addedAt?: number | null };

export default function SkillHubPage() {
  const activeProject = useActiveProject();
  const [skills, setSkills] = useState<HubSkill[]>([]);
  const [search, setSearch] = useState("");
  const [activeTag, setActiveTag] = useState<TagFilter>("All");
  const [sort, setSort] = useState<SortKey>("name");
  const [selected, setSelected] = useState<SkillDetail | null>(null);

  const loadedSlugs = useMemo(
    () => new Set(activeProject?.skills ?? []),
    [activeProject]
  );

  // Fetch the hub catalog.
  useEffect(() => {
    fetch("/api/skills")
      .then((res) => res.json())
      .then((data) => setSkills(Array.isArray(data) ? data : []))
      .catch(() => setSkills([]));
  }, []);

  async function toggleLoaded(slug: string) {
    if (!activeProject) return;
    try {
      await setSkillLoaded(activeProject, slug, !loadedSlugs.has(slug));
    } catch (err) {
      window.alert(`Failed to update the project: ${err instanceof Error ? err.message : err}`);
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

  const visible = useMemo(() => {
    const term = search.trim().toLowerCase();
    let list = skills.filter((skill) => {
      // Skill Hub is a public listing only; private skills live on the
      // user's /skills tab until the author publishes them.
      if (skill.isPublic === false) return false;
      if (activeTag !== "All") {
        const tags = skill.tags ?? [];
        if (!tags.includes(activeTag)) return false;
      }
      if (!term) return true;
      return (
        skill.slug.toLowerCase().includes(term) ||
        skill.name.toLowerCase().includes(term) ||
        skill.description.toLowerCase().includes(term) ||
        (skill.tags ?? []).some((t) => t.toLowerCase().includes(term))
      );
    });

    if (sort === "name") {
      list = [...list].sort((a, b) => a.name.localeCompare(b.name));
    } else {
      // Most recently added first; skills without timestamps go to the end.
      list = [...list].sort((a, b) => {
        const ta = a.addedAt ?? 0;
        const tb = b.addedAt ?? 0;
        return tb - ta;
      });
    }
    return list;
  }, [skills, search, activeTag, sort]);

  return (
    <div className="app-page">
      <AppTopBar title="Skill Hub" />
      <div className="app-page-body hub-page">
      <p className="page-lede">
        Discover skills and load them into the current chat session.
      </p>

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
          <option value="recent">Recently Added</option>
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

      <div className="hub-count">{visible.length} skills in the hub</div>

      <div className="hub-grid">
        {visible.length === 0 ? (
          <div className="hub-empty">No skills match your filters.</div>
        ) : (
          visible.map((skill) => {
            const isLoaded = loadedSlugs.has(skill.slug);
            return (
              <div
                key={skill.slug}
                className="hub-card"
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
                <div className="hub-card-head">
                  <div className="hub-card-name">{skill.name}</div>
                  <button
                    type="button"
                    className={`hub-card-action${isLoaded ? " loaded" : ""}`}
                    disabled={!activeProject}
                    title={activeProject ? undefined : "Open a project to load skills into it"}
                    onClick={(e) => {
                      e.stopPropagation();
                      void toggleLoaded(skill.slug);
                    }}
                    aria-pressed={isLoaded}
                  >
                    {isLoaded ? "Loaded ✓" : "Load"}
                  </button>
                </div>
                <div className="hub-card-desc">
                  {skill.description || "No description"}
                </div>
                {(skill.author || skill.repoUrl) && (
                  <div className="hub-card-meta">
                    {skill.author && (
                      <span className="hub-card-author">by {skill.author}</span>
                    )}
                    {skill.author && skill.repoUrl && (
                      <span className="hub-card-meta-sep">·</span>
                    )}
                    {skill.repoUrl && (
                      <a
                        href={skill.repoUrl}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="hub-card-repo"
                        onClick={(e) => e.stopPropagation()}
                      >
                        repo ↗
                      </a>
                    )}
                  </div>
                )}
                {(skill.tags?.length ?? 0) > 0 && (
                  <div className="hub-card-tags">
                    {skill.tags!.map((tag) => (
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
      </div>

      {selected && (
        <div className="modal-backdrop" onClick={() => setSelected(null)}>
          <div
            className="modal"
            onClick={(e) => e.stopPropagation()}
            role="dialog"
            aria-label={`Skill details: ${selected.slug}`}
          >
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
    </div>
  );
}
