"use client";

import { useState } from "react";

/**
 * G5 fast-tier decision metadata attached to a `submit_hpc_job` approval
 * request by PALISADE's HPC Job Gate. All fields are optional — the
 * modal renders whatever the backend supplied (see
 * `palisade.gates.g5_hpc.G5HpcJobGate.fast_tier_metadata`).
 */
export type DecisionMetadata = {
  gate?: string;
  resolved_script?: string;
  account?: string | null;
  account_verified?: boolean | null;
  allocation_enforced?: boolean;
  requested_nodes?: number | null;
  requested_time_seconds?: number | null;
  requested_gpus?: number | null;
  partition?: string | null;
  resource_ceiling_passed?: boolean;
  binary_denylist_match?: string | null;
  invoked_binaries?: string[];
  network_targets?: string[];
  checks_passed?: string[];
  [key: string]: unknown;
};

type Props = {
  id: string;
  toolName: string;
  message: string;
  args?: Record<string, unknown> | null;
  decisionMetadata?: DecisionMetadata | null;
  onSubmit: (id: string, action: "accept" | "decline") => void;
};

function Check({ ok, label }: { ok: boolean; label: string }) {
  return (
    <li className={ok ? "g5-check ok" : "g5-check fail"}>
      <span aria-hidden>{ok ? "✓" : "✗"}</span> {label}
    </li>
  );
}

/**
 * Human-in-the-loop approval modal for high-stakes tool calls
 * (PALISADE deferred-tool-calls). When the backend attaches G5
 * decision metadata, it renders the resolved SLURM script plus the
 * fast-tier policy-check results so the user approves with full context.
 */
export default function ToolApprovalModal({
  id,
  toolName,
  message,
  args,
  decisionMetadata,
  onSubmit,
}: Props) {
  const [submitting, setSubmitting] = useState(false);

  const submit = (action: "accept" | "decline") => {
    setSubmitting(true);
    onSubmit(id, action);
  };

  const m = decisionMetadata ?? null;
  const isG5 = m?.gate === "G5";

  return (
    <div className="modal-backdrop" onClick={() => submit("decline")}>
      <div className="modal elicitation-modal" onClick={(e) => e.stopPropagation()}>
        <div className="panel-header">
          <div className="panel-title">Approve tool call</div>
        </div>
        <div className="modal-body">
          <p className="elicitation-message" style={{ whiteSpace: "pre-wrap" }}>
            {message || `Approve call to ${toolName}?`}
          </p>

          {isG5 && (
            <div className="g5-approval">
              <div className="g5-section-title">PALISADE G5 policy checks</div>
              <ul className="g5-checks">
                {m?.account_verified != null && (
                  <Check
                    ok={!!m.account_verified}
                    label={`Allocation/account verified${
                      m.account ? ` (${m.account})` : ""
                    }`}
                  />
                )}
                <Check ok={m?.resource_ceiling_passed !== false} label="Resource ceiling within limits" />
                <Check ok={!m?.binary_denylist_match} label="No mining-binary denylist match" />
              </ul>

              {(m?.requested_nodes != null ||
                m?.requested_gpus != null ||
                m?.requested_time_seconds != null ||
                m?.partition) && (
                <div className="g5-requested">
                  {m?.requested_nodes != null && <span>nodes: {m.requested_nodes}</span>}
                  {m?.requested_gpus != null && <span>gpus: {m.requested_gpus}</span>}
                  {m?.requested_time_seconds != null && (
                    <span>time: {m.requested_time_seconds}s</span>
                  )}
                  {m?.partition && <span>partition: {m.partition}</span>}
                </div>
              )}

              {m?.resolved_script && (
                <details className="g5-script" open>
                  <summary>Resolved SLURM script</summary>
                  <pre className="g5-script-body">{m.resolved_script}</pre>
                </details>
              )}
            </div>
          )}

          {!isG5 && args && (
            <pre className="g5-script-body">{JSON.stringify(args, null, 2)}</pre>
          )}

          <div className="elicitation-actions">
            <button
              type="button"
              className="button"
              onClick={() => submit("accept")}
              disabled={submitting}
            >
              {submitting ? "Submitting..." : "Approve"}
            </button>
            <button
              type="button"
              className="button ghost"
              onClick={() => submit("decline")}
              disabled={submitting}
            >
              Decline
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
