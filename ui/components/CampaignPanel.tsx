"use client";

import { useEffect, useState } from "react";

import {
  CampaignRun,
  CampaignState,
  fetchCampaignState,
  listCampaigns,
} from "../lib/campaigns";

const POLL_MS = 5000;

/** Most recently updated campaign in the conversation (there's usually just one). */
function pickActive(runs: CampaignRun[]): CampaignRun | null {
  if (runs.length === 0) return null;
  return [...runs].sort((a, b) => (b.updated_at || "").localeCompare(a.updated_at || ""))[0];
}

function planText(step: Record<string, unknown>): string {
  return typeof step.text === "string" ? step.text : JSON.stringify(step);
}

function metricsText(result: Record<string, unknown> | null): string | null {
  if (!result) return null;
  const metrics = result.metrics;
  if (metrics && typeof metrics === "object") {
    const entries = Object.entries(metrics as Record<string, unknown>);
    if (entries.length === 0) return null;
    return entries.map(([k, v]) => `${k}=${v}`).join(", ");
  }
  return null;
}

/**
 * Read-only view of the active conversation's campaign: plan, per-step status, and HPC
 * job states. Polls the campaign state; renders nothing when the conversation has no
 * campaign. Campaign creation + plan edits happen through the chat planner, not here.
 */
export default function CampaignPanel({
  projectName,
  chatSessionId,
  onPresenceChange,
}: {
  projectName: string | null;
  chatSessionId: string | null;
  /**
   * Whether this conversation has a campaign at all. The Jobs tab is built
   * from this: a campaign is a SPLASH-specific thing that nearly every
   * conversation lacks, and a permanently empty tab is worse than no tab.
   * Reported from here because this is where the polling already lives.
   */
  onPresenceChange?: (present: boolean) => void;
}) {
  const [state, setState] = useState<CampaignState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshNonce, setRefreshNonce] = useState(0);

  useEffect(() => {
    if (!projectName || !chatSessionId) return;
    const pName = projectName;
    const sId = chatSessionId;
    const controller = new AbortController();
    let active = true;

    async function poll() {
      try {
        const target = pickActive(await listCampaigns(pName, sId, controller.signal));
        if (!target) {
          if (active) {
            setState(null);
            setError(null);
          }
          return;
        }
        const next = await fetchCampaignState(pName, target.id, controller.signal);
        if (active) {
          setState(next);
          setError(null);
        }
      } catch (err) {
        if (active && (err as Error).name !== "AbortError") setError((err as Error).message);
      }
    }

    void poll();
    const timer = setInterval(() => void poll(), POLL_MS);
    return () => {
      active = false;
      controller.abort();
      clearInterval(timer);
    };
  }, [projectName, chatSessionId, refreshNonce]);

  const present = Boolean(projectName && chatSessionId && state);
  useEffect(() => {
    onPresenceChange?.(present);
  }, [present, onPresenceChange]);

  if (!present || !state) return null;

  const { run, steps, jobs } = state;
  const jobsByStep = new Map(jobs.map((job) => [job.step_id, job] as const));

  return (
    <section className="campaign-panel">
      <div className="campaign-head">
        <span className="campaign-title">{run.title || "SPLASH campaign"}</span>
        <span className={`campaign-status campaign-status-${run.status}`}>
          {run.status.replace("_", " ")}
        </span>
        <button
          className="button ghost"
          type="button"
          onClick={() => setRefreshNonce((n) => n + 1)}
        >
          Refresh
        </button>
      </div>

      {run.plan.length > 0 && (
        <ol className="campaign-plan">
          {run.plan.map((step, i) => (
            <li key={i}>{planText(step)}</li>
          ))}
        </ol>
      )}

      {steps.length > 0 && (
        <div className="campaign-steps">
          {steps.map((step) => {
            const job = jobsByStep.get(step.id);
            const metrics = metricsText(step.result);
            return (
              <div key={step.id} className="campaign-step">
                <span className="campaign-step-kind">
                  cycle {step.cycle} · {step.kind}
                </span>
                <span className={`campaign-status campaign-status-${step.status}`}>{step.status}</span>
                {job && (
                  <span className="campaign-job">
                    {job.job_name ?? job.job_id} · {job.state}
                  </span>
                )}
                {metrics && <span className="campaign-metrics">{metrics}</span>}
              </div>
            );
          })}
        </div>
      )}

      {error && <div className="campaign-error">{error}</div>}
      <div className="campaign-note">
        Continue in chat to advance the campaign. v1 scores TBR + density; other properties are advisory.
      </div>
    </section>
  );
}
