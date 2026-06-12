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
  const [nerscAccount, setNerscAccount] = useState(user.nersc_account ?? "");
  const [nerscRemoteDir, setNerscRemoteDir] = useState(user.nersc_remote_dir ?? "");
  const [odoS3mToken, setOdoS3mToken] = useState(user.odo_s3m_token ?? "");
  const [frontierS3mToken, setFrontierS3mToken] = useState(user.frontier_s3m_token ?? "");
  const [nerscIriToken, setNerscIriToken] = useState(user.nersc_iri_token ?? "");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  async function save() {
    // Build a minimal diff against the loaded user so we only send fields
    // that actually changed. Empty strings become null to clear the field.
    const blankToNull = (s: string) => (s.trim() === "" ? null : s.trim());
    const diff: UserSelfUpdate = {};
    const nullableCandidates: Array<
      [keyof UserSelfUpdate, string | null, string | null]
    > = [
      ["nersc_account", user.nersc_account ?? null, blankToNull(nerscAccount)],
      ["nersc_remote_dir", user.nersc_remote_dir ?? null, blankToNull(nerscRemoteDir)],
      ["odo_s3m_token", user.odo_s3m_token ?? null, blankToNull(odoS3mToken)],
      ["frontier_s3m_token", user.frontier_s3m_token ?? null, blankToNull(frontierS3mToken)],
      ["nersc_iri_token", user.nersc_iri_token ?? null, blankToNull(nerscIriToken)],
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
        Odo S3M token
        <input
          className="input"
          type="password"
          value={odoS3mToken}
          onChange={(e) => setOdoS3mToken(e.target.value)}
          placeholder="Bearer token"
          autoComplete="off"
          spellCheck={false}
        />
        <span className="user-settings-hint">
          Bearer token for the OLCF AmSC IRI service on Odo (open enclave).
          Required for cluster=&quot;odo&quot;. Stored encrypted at rest.
        </span>
      </label>

      <label className="project-modal-label">
        Frontier S3M token
        <input
          className="input"
          type="password"
          value={frontierS3mToken}
          onChange={(e) => setFrontierS3mToken(e.target.value)}
          placeholder="Bearer token"
          autoComplete="off"
          spellCheck={false}
        />
        <span className="user-settings-hint">
          Bearer token for the OLCF AmSC IRI service on Frontier (moderate
          enclave). Required for cluster=&quot;frontier&quot;. Stored encrypted at rest.
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
