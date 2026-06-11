"use client";

import { useEffect, useState } from "react";
import {
  fetchCurrentUserWithConfig,
  updateCurrentUser,
  type UserPublicWithConfig,
  type UserSelfUpdate,
} from "@/lib/user";

/**
 * Modal for editing the authenticated user's per-user config. Fetches the
 * full `UserPublicWithConfig` view (with decrypted tokens) on open — the
 * nav-rail's cached user is the light view, which intentionally omits
 * secrets — and writes back through `PUT /users/me`, which returns the
 * updated record so we don't need a follow-up GET.
 */
export function UserSettingsModal({ onClose }: { onClose: () => void }) {
  const [user, setUser] = useState<UserPublicWithConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    setLoadError(null);
    try {
      setUser(await fetchCurrentUserWithConfig());
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : "Failed to load user.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  // Close on Escape so the modal behaves like every other dialog in the app.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal user-settings-modal"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label="User settings"
      >
        <div className="panel-header">
          <div className="panel-title">User settings</div>
          <button type="button" className="button ghost" onClick={onClose}>
            Close
          </button>
        </div>
        <div className="modal-body">
          {loading && !user && (
            <div style={{ fontSize: 13, color: "var(--muted)" }}>Loading…</div>
          )}
          {loadError && !user && (
            <div className="error" style={{ fontSize: 13 }}>
              {loadError}{" "}
              <button
                type="button"
                className="button ghost button-xs"
                onClick={() => void load()}
              >
                Retry
              </button>
            </div>
          )}

          {/* Key on user.id so the form's local draft state is rebuilt cleanly
              if the underlying user identity ever changes (e.g. after the
              cache is wiped and a different SSO user signs in). */}
          {user && <UserSettingsForm key={user.id} user={user} onClose={onClose} />}
        </div>
      </div>
    </div>
  );
}

function UserSettingsForm({
  user,
  onClose,
}: {
  user: UserPublicWithConfig;
  onClose: () => void;
}) {
  const [hpcDir, setHpcDir] = useState(user.remote_hpc_jobs_dir);
  const [nerscAccount, setNerscAccount] = useState(user.nersc_account ?? "");
  const [nerscRemoteDir, setNerscRemoteDir] = useState(user.nersc_remote_dir ?? "");
  const [frontierAccount, setFrontierAccount] = useState(user.frontier_account ?? "");
  const [frontierRemoteDir, setFrontierRemoteDir] = useState(user.frontier_remote_dir ?? "");
  const [s3mToken, setS3mToken] = useState(user.s3m_token ?? "");
  const [nerscIriToken, setNerscIriToken] = useState(user.nersc_iri_token ?? "");
  const [globusToken, setGlobusToken] = useState(user.globus_token ?? "");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  async function save() {
    // Build a minimal diff against the loaded user so we only send fields
    // that actually changed. Empty strings become null to clear nullable
    // fields; `remote_hpc_jobs_dir` is non-nullable and must stay a string,
    // so blank input is rejected with an inline error.
    const trimmedHpcDir = hpcDir.trim();
    if (trimmedHpcDir === "") {
      setSaveError("Remote HPC jobs directory cannot be empty.");
      return;
    }
    const blankToNull = (s: string) => (s.trim() === "" ? null : s.trim());
    const diff: UserSelfUpdate = {};
    if (trimmedHpcDir !== user.remote_hpc_jobs_dir) {
      diff.remote_hpc_jobs_dir = trimmedHpcDir;
    }
    const nullableCandidates: Array<
      [Exclude<keyof UserSelfUpdate, "remote_hpc_jobs_dir">, string | null, string | null]
    > = [
      ["nersc_account", user.nersc_account ?? null, blankToNull(nerscAccount)],
      ["nersc_remote_dir", user.nersc_remote_dir ?? null, blankToNull(nerscRemoteDir)],
      ["frontier_account", user.frontier_account ?? null, blankToNull(frontierAccount)],
      ["frontier_remote_dir", user.frontier_remote_dir ?? null, blankToNull(frontierRemoteDir)],
      ["s3m_token", user.s3m_token ?? null, blankToNull(s3mToken)],
      ["nersc_iri_token", user.nersc_iri_token ?? null, blankToNull(nerscIriToken)],
      ["globus_token", user.globus_token ?? null, blankToNull(globusToken)],
    ];
    for (const [key, prev, next] of nullableCandidates) {
      if (prev !== next) {
        (diff as Record<string, string | null>)[key] = next;
      }
    }
    if (Object.keys(diff).length === 0) {
      onClose();
      return;
    }
    setSaving(true);
    setSaveError(null);
    try {
      await updateCurrentUser(diff);
      onClose();
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : "Failed to save settings.");
      setSaving(false);
    }
  }

  return (
    <>
      <div className="user-settings-identity">
        <div>
          <div className="user-settings-identity-label">Signed in as</div>
          <div className="user-settings-identity-email">{user.email}</div>
        </div>
        {user.is_admin && <span className="tag">admin</span>}
      </div>

      <label className="project-modal-label">
        Remote HPC jobs directory (Odo)
        <input
          className="input"
          value={hpcDir}
          onChange={(e) => setHpcDir(e.target.value)}
          placeholder="/gpfs/wolf2/olcf/gen150/proj-shared/vista"
          spellCheck={false}
          required
        />
        <span className="user-settings-hint">
          Where hpc_jobs are copied to on Odo (via Globus). One-time setup on Odo:
          {" "}<code>mkdir -p -m 2775 &lt;this dir&gt;/out</code> — job logs and
          outputs are written there by the IRI automation user, and Globus cannot
          create group-writable directories.
        </span>
      </label>

      <label className="project-modal-label">
        Frontier account
        <input
          className="input"
          value={frontierAccount}
          onChange={(e) => setFrontierAccount(e.target.value)}
          placeholder="e.g. chm243"
          spellCheck={false}
        />
        <span className="user-settings-hint">
          OLCF project name for Frontier Slurm submissions. Must match your S3M
          token&apos;s project claim. Required for cluster=&quot;frontier&quot;.
        </span>
      </label>

      <label className="project-modal-label">
        Frontier remote directory
        <input
          className="input"
          value={frontierRemoteDir}
          onChange={(e) => setFrontierRemoteDir(e.target.value)}
          placeholder="/lustre/orion/<project>/proj-shared/vista"
          spellCheck={false}
        />
        <span className="user-settings-hint">
          Where hpc_jobs are copied to on Frontier. Required for cluster=&quot;frontier&quot;.
        </span>
      </label>

      <label className="project-modal-label">
        NERSC account
        <input
          className="input"
          value={nerscAccount}
          onChange={(e) => setNerscAccount(e.target.value)}
          placeholder="e.g. m1234"
          spellCheck={false}
        />
        <span className="user-settings-hint">
          NERSC project account for Slurm submission.
        </span>
      </label>

      <label className="project-modal-label">
        NERSC remote directory
        <input
          className="input"
          value={nerscRemoteDir}
          onChange={(e) => setNerscRemoteDir(e.target.value)}
          placeholder="/pscratch/sd/<u>/<user>/.vista"
          spellCheck={false}
        />
        <span className="user-settings-hint">
          Absolute remote dir on the NERSC machine. Required for Perlmutter.
        </span>
      </label>

      <label className="project-modal-label">
        S3M token
        <input
          className="input"
          type="password"
          value={s3mToken}
          onChange={(e) => setS3mToken(e.target.value)}
          placeholder="Bearer token"
          autoComplete="off"
          spellCheck={false}
        />
        <span className="user-settings-hint">
          Bearer token for the OLCF AmSC IRI services — enables both Odo (open
          enclave) and Frontier (moderate enclave) compute. Stored encrypted at rest.
        </span>
      </label>

      <label className="project-modal-label">
        NERSC IRI token
        <input
          className="input"
          type="password"
          value={nerscIriToken}
          onChange={(e) => setNerscIriToken(e.target.value)}
          placeholder="Globus access token"
          autoComplete="off"
          spellCheck={false}
        />
        <span className="user-settings-hint">
          Globus access token for NERSC IRI. Expires ~48h; stored encrypted at rest.
        </span>
      </label>

      <label className="project-modal-label">
        Globus token
        <input
          className="input"
          type="password"
          value={globusToken}
          onChange={(e) => setGlobusToken(e.target.value)}
          placeholder="Globus Transfer refresh token"
          autoComplete="off"
          spellCheck={false}
        />
        <span className="user-settings-hint">
          For Odo and Frontier file transfer via Globus. Mint with:
          {" "}<code>python OLCF-Globus-Transfer/get_olcf_token.py --force-login --session-domain sso.ccs.ornl.gov</code>,
          then paste the <code>refresh_token</code> value from{" "}
          <code>~/.globus/olcf_tokens.json</code>. Long-lived; stored encrypted at rest.
        </span>
      </label>

      {saveError && (
        <div className="error" style={{ fontSize: 12 }}>
          {saveError}
        </div>
      )}

      <div
        style={{
          display: "flex",
          gap: 8,
          justifyContent: "flex-end",
          marginTop: 4,
        }}
      >
        <button
          type="button"
          className="button ghost"
          onClick={onClose}
          disabled={saving}
        >
          Cancel
        </button>
        <button
          type="button"
          className="button"
          onClick={() => void save()}
          disabled={saving}
        >
          {saving ? "Saving…" : "Save"}
        </button>
      </div>
    </>
  );
}
