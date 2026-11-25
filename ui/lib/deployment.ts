"use client";

import { useVersion } from "@/lib/version";

/**
 * Which deployment this VISTA is, from the backend at runtime (openspec change
 * `multi-user-hosted`, design D7): `desktop`, a researcher's own computer, or
 * `hosted`, a shared service behind a gateway. The answer rides on
 * `GET /api/version`, which `lib/version.ts` already fetches once for the page,
 * so this adds no request and one build serves both. There is deliberately no
 * `NEXT_PUBLIC_` variable for it.
 *
 * Until the answer arrives, or if it cannot be read, this reads as `desktop`:
 * the backend refuses what a hosted deployment turns off whatever the page
 * shows, so the worst a slow answer costs is a control that shows for a moment
 * and then goes. Hide a control with this *after* the backend refuses it.
 */
export type Deployment = "desktop" | "hosted";

export type DeploymentView = {
  deployment: Deployment;
  hosted: boolean;
  /** The gateway's logout page, when a hosted backend reports one. */
  signOutUrl: string | null;
  /** The HPC clusters this deployment supports; null (unknown, or an older backend) means all. */
  allowedClusters: string[] | null;
  /**
   * The signed-in user's name.
   * TODO(groups 4/5): there is no `/me` route yet; once `CurrentUser` lands,
   * read it from there. Until then a hosted menu shows no name.
   */
  userName: string | null;
};

/** The pure part, for tests: what `useDeployment` makes of a version answer. */
export function deploymentOf(
  info: {
    deployment?: Deployment;
    sign_out_url?: string | null;
    allowed_clusters?: string[];
  } | null,
): DeploymentView {
  const deployment = info?.deployment === "hosted" ? "hosted" : "desktop";
  return {
    deployment,
    hosted: deployment === "hosted",
    signOutUrl: deployment === "hosted" ? (info?.sign_out_url ?? null) : null,
    allowedClusters: info?.allowed_clusters ?? null,
    userName: null,
  };
}

export function useDeployment(): DeploymentView {
  return deploymentOf(useVersion().info);
}

/** Whether this deployment supports `cluster`; every cluster until the answer says otherwise. */
export function clusterAllowed(view: DeploymentView, cluster: string): boolean {
  return view.allowedClusters === null || view.allowedClusters.includes(cluster);
}
