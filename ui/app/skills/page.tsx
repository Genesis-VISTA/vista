"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { SkillDetail, SkillSummary } from "@/lib/types";

const LOADED_SKILLS_STORAGE_KEY = "vista.loadedSkills.v1";

function readLoadedSlugs(): Set<string> {
  if (typeof window === "undefined") return new Set();
  try {
    const raw = window.localStorage.getItem(LOADED_SKILLS_STORAGE_KEY);
    if (!raw) return new Set();
    const parsed = JSON.parse(raw);
    if (Array.isArray(parsed)) {
      return new Set(parsed.filter((s): s is string => typeof s === "string"));
    }
  } catch {
    // ignore
  }
  return new Set();
}

export default function SkillsPage() {
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  const [loadedSlugs, setLoadedSlugs] = useState<Set<string>>(readLoadedSlugs);
  const [filter, setFilter] = useState("");
  const [selected, setSelected] = useState<SkillDetail | null>(null);

  useEffect(() => {
    fetch("/api/skills")
      .then((res) => res.json())
      .then((data) => setSkills(Array.isArray(data) ? data : []))
      .catch(() => setSkills([]));
  }, []);

  // Persist + sync, same pattern as the chat and hub pages.
  useEffect(() => {
    try {
      window.localStorage.setItem(
        LOADED_SKILLS_STORAGE_KEY,
        JSON.stringify(Array.from(loadedSlugs))
      );
    } catch {
      // ignore
    }
  }, [loadedSlugs]);

  useEffect(() => {
    function reread() {
      const next = readLoadedSlugs();
      setLoadedSlugs((prev) => {
        if (prev.size === next.size && Array.from(prev).every((s) => next.has(s))) {
          return prev;
        }
        return next;
      });
    }
    function onStorage(e: StorageEvent) {
      if (e.key === LOADED_SKILLS_STORAGE_KEY) reread();
    }
    window.addEventListener("storage", onStorage);
    window.addEventListener("focus", reread);
    return () => {
      window.removeEventListener("storage", onStorage);
      window.removeEventListener("focus", reread);
    };
  }, []);

  function unload(slug: string) {
    setLoadedSlugs((prev) => {
      if (!prev.has(slug)) return prev;
      const next = new Set(prev);
      next.delete(slug);
      return next;
    });
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
    <div className="standalone-page">
      <section className="panel" style={{ height: "100%" }}>
        <div className="panel-header">
          <div className="panel-title">Skills</div>
          <span className="tag">{loadedSlugs.size} loaded</span>
        </div>
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
                  <button
                    type="button"
                    className="button ghost button-sm"
                    style={{ marginTop: 8, alignSelf: "flex-start" }}
                    onClick={(e) => {
                      e.stopPropagation();
                      unload(skill.slug);
                    }}
                  >
                    Unload
                  </button>
                </div>
              ))
            )}
          </div>
        </div>
      </section>

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
    </div>
  );
}
