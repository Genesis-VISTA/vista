"use client";

import { useEffect, useState } from "react";
import {
  completeGlobusLogin,
  fetchCurrentUserWithConfig,
  startGlobusLogin,
  updateCurrentUser,
  type GlobusCluster,
  type UserPublicWithConfig,
  type UserSelfUpdate,
} from "@/lib/user";
import { displayModelName, qualifyModelInput } from "@/lib/models";

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
  const [inferenceApiKey, setInferenceApiKey] = useState(user.inference_api_key ?? "");
  const [inferenceModel, setInferenceModel] = useState(
    displayModelName(user.inference_model ?? ""),
  );
  const [inferenceBaseUrl, setInferenceBaseUrl] = useState(
    user.inference_base_url ?? "",
  );
  const [nerscAccount, setNerscAccount] = useState(user.nersc_account ?? "");
  const [nerscRemoteDir, setNerscRemoteDir] = useState(user.nersc_remote_dir ?? "");
  const [s3mToken, setS3mToken] = useState(user.s3m_token ?? "");
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
      [
        "inference_api_key",
        user.inference_api_key ?? null,
        blankToNull(inferenceApiKey),
      ],
      [
        "inference_model",
        user.inference_model ?? null,
        blankToNull(qualifyModelInput(inferenceModel)),
      ],
      [
        "inference_base_url",
        user.inference_base_url ?? null,
        blankToNull(inferenceBaseUrl),
      ],
      ["nersc_account", user.nersc_account ?? null, blankToNull(nerscAccount)],
      ["nersc_remote_dir", user.nersc_remote_dir ?? null, blankToNull(nerscRemoteDir)],
      ["s3m_token", user.s3m_token ?? null, blankToNull(s3mToken)],
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
        Inference API key
        <input
          className="input"
          type="password"
          value={inferenceApiKey}
          onChange={(e) => setInferenceApiKey(e.target.value)}
          placeholder="API key"
          autoComplete="off"
          spellCheck={false}
        />
        <span className="user-settings-hint">
          Key for the inference endpoint below. Required to chat; everything
          else works without it. Stored encrypted at rest, and picked up on your
          next message without a restart.{" "}
          <a
            href="https://api.i2-core.american-science-cloud.org"
            target="_blank"
            rel="noopener noreferrer"
          >
            Get a key
          </a>
          .
        </span>
      </label>

      <label className="project-modal-label">
        Model
        <input
          className="input"
          value={inferenceModel}
          onChange={(e) => setInferenceModel(e.target.value)}
          placeholder="claude-sonnet"
          spellCheck={false}
        />
        <span className="user-settings-hint">
          Optional override, by name. Leave blank to use the server default.
          Also settable from the model picker next to the project switcher.
        </span>
      </label>

      <label className="project-modal-label">
        Inference endpoint
        <input
          className="input"
          value={inferenceBaseUrl}
          onChange={(e) => setInferenceBaseUrl(e.target.value)}
          placeholder="https://api.i2-core.american-science-cloud.org"
          spellCheck={false}
        />
        <span className="user-settings-hint">
          OpenAI-compatible endpoint. Optional override; leave blank to use
          the server default.
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
          Bearer token for the OLCF AmSC IRI service. Stored encrypted at rest.{" "}
          <a
            href="https://docs.olcf.ornl.gov/services_and_applications/s3m/overview.html#get-a-token"
            target="_blank"
            rel="noopener noreferrer"
          >
            Get a token
          </a>
          . Expires in 24 hours.
        </span>
      </label>

      <div className="user-settings-section-label">File transfer</div>
      <div className="user-settings-hint" style={{ marginTop: -4 }}>
        Odo and Frontier move files through Globus, which needs your permission
        once per cluster. Connecting opens a Globus login and gives you a code
        to paste back here.
      </div>
      <GlobusConnect
        cluster="odo"
        label="Odo"
        initiallyConnected={globusConnected(user, "odo")}
      />
      <GlobusConnect
        cluster="frontier"
        label="Frontier"
        initiallyConnected={globusConnected(user, "frontier")}
      />

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

/**
 * Whether a cluster has a *whole* Globus credential.
 *
 * Both halves or neither: the Transfer token lists the cluster's directories
 * and the collection token reads what is in them, and one without the other
 * finds an output directory it cannot open. A connection made before VISTA
 * moved to the HTTPS interface has only the first, so it reads as unconnected
 * here — which is the prompt to connect again, and the only honest answer.
 */
function globusConnected(
  user: UserPublicWithConfig,
  cluster: GlobusCluster,
): boolean {
  const transfer =
    cluster === "odo" ? user.odo_globus_token : user.frontier_globus_token;
  const https =
    cluster === "odo"
      ? user.odo_globus_https_token
      : user.frontier_globus_https_token;
  return Boolean(
    (transfer && https) || (user.globus_token && user.globus_https_token),
  );
}

/**
 * Connect one cluster's Globus account.
 *
 * Deliberately outside the form's save diff: this is an exchange, not a value.
 * The credential never reaches the browser, what the researcher pastes is
 * single-use, and the backend stores the result itself — so Save has nothing to
 * carry, and a connection in progress survives saving the rest of the form.
 *
 * `initiallyConnected` seeds the display from the loaded user and is not read
 * again. Re-fetching after a connection would rebuild the form and discard any
 * unsaved edits in the fields above.
 */
function GlobusConnect({
  cluster,
  label,
  initiallyConnected,
}: {
  cluster: GlobusCluster;
  label: string;
  initiallyConnected: boolean;
}) {
  const [connected, setConnected] = useState(initiallyConnected);
  const [identity, setIdentity] = useState<string | null>(null);
  const [authorizeUrl, setAuthorizeUrl] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState<"starting" | "finishing" | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Between clicking Connect and pasting the code the researcher leaves for a
  // browser, and closing the modal in the meantime is easy to do -- saving
  // another field closes it outright. The backend keeps the flow alive for
  // fifteen minutes either way, so the address is worth keeping too: without
  // it, coming back means logging in to Globus a second time for nothing.
  // Read in an effect rather than in the initial state so the server-rendered
  // markup and the first client render agree.
  const stashKey = `vista.globus.authorize.${cluster}`;
  useEffect(() => {
    try {
      const stashed = window.sessionStorage.getItem(stashKey);
      if (stashed) setAuthorizeUrl(stashed);
    } catch {
      // Storage can be unavailable. Costs the researcher a second login, and
      // nothing else, so there is nothing to report.
    }
  }, [stashKey]);

  function stash(url: string | null) {
    try {
      if (url === null) window.sessionStorage.removeItem(stashKey);
      else window.sessionStorage.setItem(stashKey, url);
    } catch {
      // As above.
    }
  }

  function forget() {
    setAuthorizeUrl(null);
    stash(null);
    setCode("");
  }

  async function start() {
    setBusy("starting");
    setError(null);
    try {
      const started = await startGlobusLogin(cluster);
      setAuthorizeUrl(started.authorize_url);
      stash(started.authorize_url);
      setCode("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not start the connection.");
    } finally {
      setBusy(null);
    }
  }

  async function finish() {
    setBusy("finishing");
    setError(null);
    try {
      const result = await completeGlobusLogin(cluster, code);
      setConnected(true);
      setIdentity(result.identity);
      forget();
    } catch (e) {
      // The address stays on screen. A rejected code is usually a mistyped or
      // half-copied one, and making the researcher start the login again to
      // try a second time would be the wrong lesson to draw from it.
      setError(e instanceof Error ? e.message : "That code was not accepted.");
    } finally {
      setBusy(null);
    }
  }

  const pending = authorizeUrl !== null;
  const status = pending
    ? "Waiting for your code"
    : identity
      ? `Connected as ${identity}`
      : connected
        ? "Connected"
        : "Not connected";

  return (
    <div className="user-settings-globus">
      <div className="user-settings-globus-head">
        <span className="user-settings-globus-cluster">{label}</span>
        <span
          className={`user-settings-globus-status ${
            pending ? "pending" : connected ? "connected" : "absent"
          }`}
        >
          {status}
        </span>
        {!pending && (
          <button
            type="button"
            className="button ghost button-xs"
            onClick={() => void start()}
            disabled={busy !== null}
          >
            {busy === "starting"
              ? "Starting…"
              : connected
                ? "Reconnect"
                : "Connect"}
          </button>
        )}
      </div>

      {pending && (
        <>
          <div className="user-settings-hint">
            Open this address, log in to {label}, and paste the code Globus gives
            you. Use only this address: starting again replaces it, and a code
            from an older one will not be accepted.
          </div>
          <div className="user-settings-globus-url">{authorizeUrl}</div>
          <div className="user-settings-globus-actions">
            <a
              className="button ghost button-xs"
              href={authorizeUrl}
              target="_blank"
              rel="noopener noreferrer"
            >
              Open in browser
            </a>
            <button
              type="button"
              className="button ghost button-xs"
              onClick={() => {
                forget();
                setError(null);
              }}
              disabled={busy !== null}
            >
              Cancel
            </button>
          </div>
          <div className="user-settings-globus-actions">
            <input
              className="input"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              placeholder="Paste the code from Globus"
              autoComplete="off"
              spellCheck={false}
            />
            <button
              type="button"
              className="button button-xs"
              onClick={() => void finish()}
              disabled={busy !== null || code.trim() === ""}
            >
              {busy === "finishing" ? "Connecting…" : "Finish"}
            </button>
          </div>
        </>
      )}

      {error && (
        <div className="error" style={{ fontSize: 11, lineHeight: 1.4 }}>
          {error}
        </div>
      )}
    </div>
  );
}
