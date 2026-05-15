"use server";

// Server actions for the Project CRUD endpoints on the FastAPI backend.
// These run on the Next.js server and never leak the backend URL to the browser.

import { backendUrl } from "@/lib/backend";
import type { Project, ProjectCreate } from "@/lib/types";

async function backendJson<T>(
  path: string,
  init?: RequestInit & { errorPrefix?: string },
): Promise<T> {
  const res = await fetch(`${backendUrl}${path}`, {
    cache: "no-store",
    ...init,
    headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(
      `${init?.errorPrefix ?? "Backend error"} (${res.status}): ${detail || res.statusText}`,
    );
  }
  return res.json() as Promise<T>;
}

export async function getProjects(): Promise<Project[]> {
  return backendJson<Project[]>("/projects", { errorPrefix: "Failed to list projects" });
}

export async function getProject(name: string): Promise<Project> {
  return backendJson<Project>(`/projects/${encodeURIComponent(name)}`, {
    errorPrefix: "Failed to load project",
  });
}

export async function createProject(payload: ProjectCreate): Promise<Project> {
  return backendJson<Project>("/projects", {
    method: "POST",
    body: JSON.stringify(payload),
    errorPrefix: "Failed to create project",
  });
}

export async function updateProject(
  name: string,
  payload: ProjectCreate,
): Promise<Project> {
  return backendJson<Project>(`/projects/${encodeURIComponent(name)}`, {
    method: "PUT",
    body: JSON.stringify(payload),
    errorPrefix: "Failed to update project",
  });
}

export async function deleteProject(name: string): Promise<void> {
  const res = await fetch(`${backendUrl}/projects/${encodeURIComponent(name)}`, {
    method: "DELETE",
    cache: "no-store",
  });
  if (!res.ok && res.status !== 204) {
    const detail = await res.text().catch(() => "");
    throw new Error(`Failed to delete project (${res.status}): ${detail || res.statusText}`);
  }
}
