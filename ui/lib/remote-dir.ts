// What a cluster's remote directory should hold, given the project its S3M token
// reports. Pure, so the Settings modal only decides *when* to ask.
//
// The token's `project` claim is the subproject (`gen150-vista`), and no project
// folder on orion or wolf2 has a dash, so the folder is the claim lowercased and
// cut at its first dash. Whether VISTA filled a value is read from the value
// itself: anything matching the cluster's template counts as filled, so there is
// no stored flag.

import type { HpcCluster } from "@/lib/hpc-status";

const FOLDER = /^[a-z0-9]+$/;

const TEMPLATES: Partial<Record<HpcCluster, { prefix: string; filled: RegExp }>> = {
  odo: {
    prefix: "/gpfs/wolf2/olcf/",
    filled: /^\/gpfs\/wolf2\/olcf\/[a-z0-9]+\/proj-shared\/vista\/?$/,
  },
  frontier: {
    prefix: "/lustre/orion/",
    filled: /^\/lustre\/orion\/[a-z0-9]+\/proj-shared\/vista\/?$/,
  },
  // Lux mounts Frontier's filesystem.
  lux: {
    prefix: "/lustre/orion/",
    filled: /^\/lustre\/orion\/[a-z0-9]+\/proj-shared\/vista\/?$/,
  },
};

/** The project's folder name: lowercased, cut at the first dash; null if unusable. */
export function projectFolder(project: string | null | undefined): string | null {
  if (!project) return null;
  const folder = project.trim().toLowerCase().split("-", 1)[0];
  return FOLDER.test(folder) ? folder : null;
}

/** The directory VISTA would fill for this cluster and project, or null. */
export function suggestedRemoteDir(
  cluster: HpcCluster,
  project: string | null | undefined,
): string | null {
  const template = TEMPLATES[cluster];
  const folder = projectFolder(project);
  if (!template || !folder) return null;
  return `${template.prefix}${folder}/proj-shared/vista`;
}

/** Whether `value` is the cluster's template for some project's folder. */
export function isFilledRemoteDir(cluster: HpcCluster, value: string): boolean {
  const template = TEMPLATES[cluster];
  return !!template && template.filled.test(value.trim());
}

function sameDir(a: string, b: string): boolean {
  return a.trim().replace(/\/$/, "") === b.trim().replace(/\/$/, "");
}

/**
 * The value to write into the field, or null to leave it. Fills an empty field,
 * and replaces VISTA's folder for a different project; never touches anything
 * the researcher typed.
 */
export function remoteDirFill(
  cluster: HpcCluster,
  project: string | null | undefined,
  current: string,
): string | null {
  const suggestion = suggestedRemoteDir(cluster, project);
  if (!suggestion) return null;
  if (current.trim() === "") return suggestion;
  if (isFilledRemoteDir(cluster, current) && !sameDir(current, suggestion)) {
    return suggestion;
  }
  return null;
}

/** The note under the field while it holds the folder filled for this project. */
export function filledNote(
  cluster: HpcCluster,
  project: string | null | undefined,
  current: string,
): string | null {
  const suggestion = suggestedRemoteDir(cluster, project);
  if (!suggestion || current !== suggestion) return null;
  const folder = projectFolder(project) as string;
  const claim = (project as string).trim();
  if (folder === claim) {
    return `Filled from your S3M token's project, ${folder}.`;
  }
  return `Filled from your S3M token's project (${folder} folder inferred from ${claim}).`;
}
