"use client";

// Skills page — shows skills loaded for the active project (read-only viewer).

import { useEffect, useState } from "react";
import { getSkills } from "@/app/actions/skills";
import { useActiveProject } from "@/lib/projects";
import type { SkillSummary } from "@/lib/types";

export default function SkillsPage() {
  const { activeProject } = useActiveProject();
  const [skills, setSkills] = useState<SkillSummary[]>([]);

  useEffect(() => {
    getSkills().then(setSkills).catch(() => setSkills([]));
  }, []);

  const projectSkills = activeProject
    ? skills.filter((s) => activeProject.skills.includes(s.name))
    : [];

  return (
    <div className="h-full overflow-auto p-6">
      <h1 className="text-2xl font-semibold">Skills</h1>
      <p className="text-sm text-gray-600 mb-4">
        {activeProject
          ? `Skills loaded for "${activeProject.name}"`
          : "Select a project to view its loaded skills."}
      </p>

      {projectSkills.length === 0 && activeProject && (
        <div className="text-sm text-gray-500">No skills attached to this project.</div>
      )}
      <div className="space-y-3">
        {projectSkills.map((s) => (
          <div key={s.name} className="border rounded-md p-3">
            <div className="font-medium">{s.name}</div>
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
        ))}
      </div>
    </div>
  );
}
