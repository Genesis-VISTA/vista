"use client";

// Skill hub — browse and load/unload skills for the current chat session.

import { useEffect, useMemo, useState } from "react";
import { getSkills, getSkillDetail } from "@/app/actions/skills";
import { useLoadedSkills } from "@/lib/skills";
import type { SkillSummary, SkillDetail } from "@/lib/types";

export default function SkillHubPage() {
  const { loadedSlugs, toggleSkill } = useLoadedSkills();
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  const [query, setQuery] = useState("");
  const [detail, setDetail] = useState<SkillDetail | null>(null);

  useEffect(() => {
    getSkills().then(setSkills).catch(() => setSkills([]));
  }, []);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return skills;
    return skills.filter(
      (s) =>
        s.name.toLowerCase().includes(q) ||
        s.description.toLowerCase().includes(q) ||
        (s.tags ?? []).some((t) => t.toLowerCase().includes(q)),
    );
  }, [skills, query]);

  return (
    <div className="h-full overflow-auto p-6">
      <h1 className="text-2xl font-semibold mb-1">Skill Hub</h1>
      <p className="text-sm text-gray-600 mb-4">
        Browse, load, and view skills available to the agent.
      </p>

      <input
        className="w-full border rounded px-3 py-2 text-sm mb-4"
        placeholder="Search skills…"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {filtered.map((s) => {
          const loaded = loadedSlugs.includes(s.name);
          return (
            <div key={s.name} className="border rounded-md p-3">
              <div className="flex items-start justify-between gap-2">
                <button
                  type="button"
                  onClick={() => getSkillDetail(s.name).then(setDetail)}
                  className="text-left font-medium hover:underline"
                >
                  {s.name}
                </button>
                <button
                  type="button"
                  onClick={() => toggleSkill(s.name)}
                  className={`text-xs rounded px-2 py-1 ${
                    loaded
                      ? "bg-emerald-100 text-emerald-700"
                      : "border border-gray-300 hover:bg-gray-50"
                  }`}
                >
                  {loaded ? "Loaded" : "Load"}
                </button>
              </div>
              <div className="text-xs text-gray-600 mt-1">{s.description}</div>
              {s.tags && s.tags.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1">
                  {s.tags.map((t) => (
                    <span key={t} className="text-[10px] bg-gray-100 text-gray-600 rounded px-1.5 py-0.5">
                      {t}
                    </span>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {detail && (
        <div
          className="fixed inset-0 z-40 flex items-center justify-center bg-black/40"
          onClick={() => setDetail(null)}
        >
          <div
            className="bg-white rounded-md p-5 max-w-3xl w-full max-h-[90vh] overflow-auto"
            onClick={(e) => e.stopPropagation()}
          >
            <h2 className="text-lg font-semibold mb-3">{detail.name}</h2>
            <pre className="whitespace-pre-wrap text-sm font-mono text-gray-800">{detail.body}</pre>
            <div className="mt-4 flex justify-end">
              <button
                type="button"
                className="px-3 py-1.5 text-sm rounded border hover:bg-gray-50"
                onClick={() => setDetail(null)}
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
