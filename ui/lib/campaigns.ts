/**
 * Client types + fetchers for the read-only campaign panel. Mirrors the backend's
 * CampaignStatePublic (snake_case wire shape). All calls go through the BFF proxy at
 * /api/campaigns.
 */

export type CampaignStatus =
  | "gathering"
  | "planning"
  | "running"
  | "awaiting_user"
  | "converged"
  | "exited";

export type CampaignRun = {
  id: string;
  project_id: string;
  user_id: string;
  session_id: string | null;
  domain: string;
  planner_skill: string;
  title: string | null;
  status: CampaignStatus;
  spec: Record<string, unknown>;
  plan: Array<Record<string, unknown>>;
  created_at: string;
  updated_at: string;
};

export type CampaignStep = {
  id: string;
  run_id: string;
  cycle: number;
  kind: string;
  candidate: Record<string, unknown> | null;
  status: string;
  result: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
};

export type HpcJob = {
  job_id: string;
  step_id: string;
  user_id: string;
  cluster: string;
  job_name: string | null;
  state: string;
  result_collected: boolean;
  notified: boolean;
  submitted_at: string;
  last_polled_at: string | null;
};

export type CampaignState = {
  run: CampaignRun;
  steps: CampaignStep[];
  jobs: HpcJob[];
};

export async function listCampaigns(
  projectName: string,
  chatSessionId: string | null,
  signal?: AbortSignal
): Promise<CampaignRun[]> {
  const params = new URLSearchParams({ project_name: projectName });
  if (chatSessionId) params.set("chat_session_id", chatSessionId);
  const res = await fetch(`/api/campaigns?${params.toString()}`, { signal });
  if (!res.ok) throw new Error(`Failed to list campaigns (${res.status})`);
  return res.json();
}

export async function fetchCampaignState(
  projectName: string,
  runId: string,
  signal?: AbortSignal
): Promise<CampaignState> {
  const params = new URLSearchParams({ project_name: projectName, run_id: runId });
  const res = await fetch(`/api/campaigns?${params.toString()}`, { signal });
  if (!res.ok) throw new Error(`Failed to load campaign (${res.status})`);
  return res.json();
}
