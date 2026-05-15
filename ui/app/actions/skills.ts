"use server";

// Server actions for reading skill metadata from the backend.
// The backend returns Skill objects shaped like SkillDetail; the list endpoint
// returns SkillMetadata which is structurally a SkillSummary.

import { backendUrl } from "@/lib/backend";
import type { SkillDetail, SkillSummary } from "@/lib/types";

export async function getSkills(): Promise<SkillSummary[]> {
  const res = await fetch(`${backendUrl}/skills`, { cache: "no-store" });
  if (!res.ok) throw new Error(`Failed to list skills (${res.status})`);
  return (await res.json()) as SkillSummary[];
}

export async function getSkillDetail(name: string): Promise<SkillDetail> {
  const res = await fetch(`${backendUrl}/skills/${encodeURIComponent(name)}`, {
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`Failed to load skill (${res.status})`);
  return (await res.json()) as SkillDetail;
}
