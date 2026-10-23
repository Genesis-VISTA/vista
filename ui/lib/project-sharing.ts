/**
 * Projects on the collaboration server: the import list, publishing a project
 * (with the skills it loads and the knowledge bases it searches) and importing
 * one (with the versions of them it names), through the
 * `/api/projects/{name}/publish` and `/api/collaboration/projects*` proxies.
 *
 * Refusals are the skills' `SharingRefusal`, plus `skill_conflict`: a skill the
 * project loads needs a choice on the Skills page before the project can be
 * published (`skill` names it), and `knowledge_base_conflict`, the same for a
 * knowledge base on the Knowledge Bases page (`knowledgeBase`). Controls read
 * `useCollaboration()` first.
 */

import type { ProjectPublic } from "@/lib/agent-events";
import { fromPublic, type Project } from "@/lib/projects";
import { post, refusal, type SharingRefusal } from "@/lib/skill-sharing";

/** A skill a published project loads (backend `SkillRef`). */
export type SkillRef = {
  name: string;
  bundled: boolean;
  remote_id: string | null;
  version: number | null;
};

/** A knowledge base a published project searches (backend `KnowledgeBaseRef`). */
export type KnowledgeBaseRef = {
  slug: string;
  remote_id: string;
  version: number;
};

/** What a version of a project is (backend `ProjectManifest`). */
export type ProjectManifest = {
  format: number;
  name: string;
  description: string | null;
  system_prompt: string | null;
  tools: string[];
  usage_limits: Record<string, unknown>;
  forum_repo_url: string | null;
  skills: SkillRef[];
  knowledge_bases: KnowledgeBaseRef[];
};

/** A project on the collaboration server (backend `RemoteProject`). */
export type RemoteProject = {
  id: string;
  name: string;
  head_version: number;
  created_by_name: string | null;
  created_at: string;
  published_by_name: string | null;
  published_at: string | null;
  /** Null when it was not fetched, or is not a manifest this VISTA reads. */
  manifest: ProjectManifest | null;
  /** This installation's copy of it. */
  local: { name: string; remote_version: number | null; modified: boolean } | null;
  /** Another project here has its name: importing it needs a new one. */
  name_taken: boolean;
};

export type ProjectPublished = {
  ok: true;
  status: "published" | "unchanged";
  project: Project;
  /** Skills published first, because they were not on the server or had changed. */
  skillsPublished: string[];
  /** The same for its knowledge bases, by slug. */
  knowledgeBasesPublished: string[];
};

/** What an import did about one skill the project loads (backend `SkillOutcome`). */
export type SkillOutcome = {
  name: string;
  status: "imported" | "updated" | "unchanged" | "kept" | "missing";
  detail: string | null;
};

/** What an import did about one knowledge base the project searches (backend `KnowledgeBaseOutcome`). */
export type KnowledgeBaseOutcome = {
  slug: string;
  status: "imported" | "updated" | "unchanged" | "kept";
  detail: string | null;
};

export type ProjectImported = {
  ok: true;
  status: "imported" | "updated" | "unchanged";
  project: Project;
  skills: SkillOutcome[];
  knowledgeBases: KnowledgeBaseOutcome[];
  /** Why the Hypothesis Lab could not be set up; the import stands. */
  forumError: string | null;
};

/** The collaboration server's projects. Throws with the reason when they cannot be listed. */
export async function listRemoteProjects(
  { manifests = true }: { manifests?: boolean } = {},
): Promise<RemoteProject[]> {
  const query = manifests ? "" : "?manifests=false";
  const res = await fetch(`/api/collaboration/projects${query}`, {
    headers: { accept: "application/json" },
    cache: "no-store",
  });
  if (!res.ok) throw new Error((await refusal(res)).detail);
  const data = (await res.json()) as unknown;
  return Array.isArray(data) ? (data as RemoteProject[]) : [];
}

/**
 * Publish a project and the skills it loads that need it. `onTopOf` publishes
 * over a head a refusal reported; `asNew` as a new project.
 */
export async function publishProject(
  name: string,
  { onTopOf, asNew }: { onTopOf?: number; asNew?: boolean } = {},
): Promise<ProjectPublished | SharingRefusal> {
  const res = await post(`/api/projects/${encodeURIComponent(name)}/publish`, {
    on_top_of: onTopOf ?? null,
    as_new: asNew ?? false,
  });
  if (!(res instanceof Response)) return res;
  if (!res.ok) return refusal(res);
  const body = (await res.json()) as {
    status: ProjectPublished["status"];
    project: ProjectPublic;
    skills_published: string[];
    knowledge_bases_published?: string[];
  };
  return {
    ok: true,
    status: body.status,
    project: fromPublic(body.project),
    skillsPublished: body.skills_published ?? [],
    knowledgeBasesPublished: body.knowledge_bases_published ?? [],
  };
}

/**
 * Import a project from the server, with the skills it loads: `version`, or
 * the newest.
 */
export async function importRemoteProject(
  id: string,
  opts: { version?: number; renameTo?: string; replaceModified?: boolean } = {},
): Promise<ProjectImported | SharingRefusal> {
  const res = await post(`/api/collaboration/projects/${encodeURIComponent(id)}/import`, {
    version: opts.version ?? null,
    rename_to: opts.renameTo ?? null,
    replace_modified: opts.replaceModified ?? false,
  });
  if (!(res instanceof Response)) return res;
  if (!res.ok) return refusal(res);
  const body = (await res.json()) as {
    status: ProjectImported["status"];
    project: ProjectPublic;
    skills?: SkillOutcome[];
    knowledge_bases?: KnowledgeBaseOutcome[];
    forum_error?: string | null;
  };
  return {
    ok: true,
    status: body.status,
    project: fromPublic(body.project),
    skills: body.skills ?? [],
    knowledgeBases: body.knowledge_bases ?? [],
    forumError: body.forum_error ?? null,
  };
}

/** Whether a project here is a copy of an item on the server `serverUrl`. */
export function isProjectOnServer(project: Project, serverUrl: string | null): boolean {
  return Boolean(project.remoteId) && serverUrl !== null && project.originServer === serverUrl;
}

/**
 * One line on where a project here stands with the collaboration server at
 * `serverUrl`. A modified project "differs here": it was edited, or an import
 * kept skill versions other than the ones its version named.
 */
export function projectSharingLabel(
  project: Project,
  serverUrl: string | null,
  head?: number | null,
): string {
  if (!project.remoteId) return "Not published";
  if (serverUrl !== null && project.originServer !== serverUrl) {
    return "Published on another collaboration server";
  }
  const version = project.remoteVersion ?? 0;
  const changed = project.modified ? ", differs here" : "";
  if (head != null && head > version) {
    return `Version ${head} available (this is version ${version}${changed})`;
  }
  return project.modified ? `Differs here from version ${version}` : `Version ${version}`;
}

/** What importing this project would do here, for its button. */
export function projectImportAction(project: RemoteProject): {
  label: string;
  kind: "import" | "rename" | "update" | "reimport" | "current";
} {
  const local = project.local;
  if (!local) {
    return project.name_taken
      ? { label: "Import as…", kind: "rename" }
      : { label: "Import", kind: "import" };
  }
  if ((local.remote_version ?? 0) < project.head_version) {
    return { label: `Update to version ${project.head_version}`, kind: "update" };
  }
  if (local.modified) {
    return { label: `Re-import version ${project.head_version}`, kind: "reimport" };
  }
  return { label: "Imported ✓", kind: "current" };
}

/**
 * What a publish sent beyond the project itself, as a clause for its notice
 * (", after publishing a, b and the knowledge base c"), or "" when nothing.
 */
export function publishedFirst(result: ProjectPublished): string {
  const parts: string[] = [];
  if (result.skillsPublished.length) parts.push(result.skillsPublished.join(", "));
  const kbs = result.knowledgeBasesPublished;
  if (kbs.length) {
    parts.push(`the knowledge ${kbs.length === 1 ? "base" : "bases"} ${kbs.join(", ")}`);
  }
  return parts.length ? `, after publishing ${parts.join(" and ")}` : "";
}

/** A sentence on what an import did beyond the project itself, or null when nothing needs saying. */
export function importSummary(result: ProjectImported): string | null {
  const parts: string[] = [];
  const imported = result.skills.filter((s) => s.status === "imported" || s.status === "updated");
  if (imported.length) parts.push(`Skills imported: ${imported.map((s) => s.name).join(", ")}.`);
  for (const s of result.skills) {
    if ((s.status === "kept" || s.status === "missing") && s.detail) {
      parts.push(`${s.name}: ${s.detail}`);
    }
  }
  const kbs = result.knowledgeBases.filter((k) => k.status === "imported" || k.status === "updated");
  if (kbs.length) parts.push(`Knowledge bases imported: ${kbs.map((k) => k.slug).join(", ")}.`);
  for (const k of result.knowledgeBases) {
    if (k.status === "kept" && k.detail) parts.push(`${k.slug}: ${k.detail}`);
  }
  if (result.forumError) parts.push(`The Hypothesis Lab is not set up: ${result.forumError}`);
  return parts.length ? parts.join(" ") : null;
}
