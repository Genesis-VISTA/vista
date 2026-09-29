"use client";

import { useEffect, useId, useState, type ReactNode } from "react";
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
import {
  HPC_CLUSTERS,
  HPC_CLUSTER_TITLES,
  recheckHpcStatus,
  refreshHpcStatus,
  useHpcStatus,
  type HpcCluster,
} from "@/lib/hpc-status";
import { HpcStatusDot, STATE_LABELS, WORD_TONE } from "./HpcStatusSection";

/**
 * Modal for editing the authenticated user's per-user config. Fetches the
 * full `UserPublicWithConfig` view (with decrypted tokens) on open — the
 * nav-rail's cached user is the light view, which intentionally omits
 * secrets — and writes back through `PUT /users/me`, which returns the
 * updated record so we don't need a follow-up GET.
 *
 * Account and model settings sit at the top; below them each HPC cluster has
 * its own collapsible section holding everything about it. Opened from a
 * cluster's card in the rail (`initialCluster`), only that cluster's section
 * starts expanded.
 */
export function UserSettingsModal({
  onClose,
  initialCluster,
}: {
  onClose: () => void;
  initialCluster?: HpcCluster;
}) {
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
          {user && (
            <UserSettingsForm
              key={user.id}
              user={user}
              onClose={onClose}
              initialCluster={initialCluster}
            />
          )}
        </div>
      </div>
    </div>
  );
}

/** The fields each cluster's checks read, so a save rechecks only what changed. */
const CREDENTIAL_FIELDS: Record<HpcCluster, Array<keyof UserSelfUpdate>> = {
  odo: ["odo_s3m_token"],
  frontier: ["frontier_s3m_token"],
  perlmutter: ["nersc_iri_token"],
  lux: [], // nothing stored: a researcher signs in from a chat
};

function UserSettingsForm({
  user,
  onClose,
  initialCluster,
}: {
  user: UserPublicWithConfig;
  onClose: () => void;
  initialCluster?: HpcCluster;
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
  const [odoS3mToken, setOdoS3mToken] = useState(user.odo_s3m_token ?? "");
  const [frontierS3mToken, setFrontierS3mToken] = useState(
    user.frontier_s3m_token ?? "",
  );
  const [nerscIriToken, setNerscIriToken] = useState(user.nersc_iri_token ?? "");
  const initiallyHidden = user.hpc_hidden_clusters ?? [];
  const [hidden, setHidden] = useState<Set<string>>(() => new Set(initiallyHidden));
  const [expanded, setExpanded] = useState<Set<HpcCluster>>(
    () => new Set(initialCluster ? [initialCluster] : []),
  );
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  function toggleExpanded(cluster: HpcCluster) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(cluster)) next.delete(cluster);
      else next.add(cluster);
      return next;
    });
  }

  function setShown(cluster: HpcCluster, shown: boolean) {
    setHidden((prev) => {
      const next = new Set(prev);
      if (shown) next.delete(cluster);
      else next.add(cluster);
      return next;
    });
  }

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
      ["odo_s3m_token", user.odo_s3m_token ?? null, blankToNull(odoS3mToken)],
      [
        "frontier_s3m_token",
        user.frontier_s3m_token ?? null,
        blankToNull(frontierS3mToken),
      ],
      ["nersc_iri_token", user.nersc_iri_token ?? null, blankToNull(nerscIriToken)],
    ];
    for (const [key, prev, next] of nullableCandidates) {
      if (prev !== next) {
        (diff as Record<string, string | null>)[key] = next;
      }
    }
    // In rail order, so the stored list does not churn with click order.
    const nextHidden = HPC_CLUSTERS.filter((c) => hidden.has(c));
    const hiddenChanged =
      nextHidden.join() !== HPC_CLUSTERS.filter((c) => initiallyHidden.includes(c)).join();
    if (hiddenChanged) diff.hpc_hidden_clusters = nextHidden;

    if (Object.keys(diff).length === 0) {
      onClose();
      return;
    }
    setSaving(true);
    setSaveError(null);
    try {
      await updateCurrentUser(diff);
      // Fix a credential and see it straight away: recheck just the clusters
      // whose credentials changed rather than wait for the next poll.
      const changed = HPC_CLUSTERS.filter(
        (c) => !hidden.has(c) && CREDENTIAL_FIELDS[c].some((field) => field in diff),
      );
      if (changed.length === 1) void recheckHpcStatus(changed[0]);
      else if (changed.length > 1) void recheckHpcStatus();
      else if (hiddenChanged) void refreshHpcStatus();
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

      <div className="user-settings-section-label">Clusters</div>
      <div className="user-settings-clusters">
        {HPC_CLUSTERS.map((cluster) => (
          <ClusterSection
            key={cluster}
            cluster={cluster}
            expanded={expanded.has(cluster)}
            onToggle={() => toggleExpanded(cluster)}
            shown={!hidden.has(cluster)}
            onShownChange={(shown) => setShown(cluster, shown)}
          >
            {cluster === "odo" && (
              <>
                <S3mTokenField
                  label="Odo S3M token"
                  value={odoS3mToken}
                  onChange={setOdoS3mToken}
                  hint="Minted in Odo's OLCF project."
                />
                <GlobusConnect
                  cluster="odo"
                  label="Odo"
                  initiallyConnected={globusConnected(user, "odo")}
                  onConnected={() => void recheckHpcStatus("odo")}
                />
              </>
            )}
            {cluster === "frontier" && (
              <>
                <S3mTokenField
                  label="Frontier S3M token"
                  value={frontierS3mToken}
                  onChange={setFrontierS3mToken}
                  hint="Minted in Frontier's OLCF project, a different project from Odo's, so it needs its own token."
                />
                <GlobusConnect
                  cluster="frontier"
                  label="Frontier"
                  initiallyConnected={globusConnected(user, "frontier")}
                  onConnected={() => void recheckHpcStatus("frontier")}
                />
              </>
            )}
            {cluster === "perlmutter" && (
              <>
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
                    Globus access token for NERSC IRI. Stored encrypted at rest.
                  </span>
                </label>
              </>
            )}
          </ClusterSection>
        ))}
      </div>

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

function S3mTokenField({
  label,
  value,
  onChange,
  hint,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  hint: string;
}) {
  return (
    <label className="project-modal-label">
      {label}
      <input
        className="input"
        type="password"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="Bearer token"
        autoComplete="off"
        spellCheck={false}
      />
      <span className="user-settings-hint">
        {hint} Stored encrypted at rest.{" "}
        <a
          href="https://docs.olcf.ornl.gov/services_and_applications/s3m/overview.html#get-a-token"
          target="_blank"
          rel="noopener noreferrer"
        >
          Get a token
        </a>
        .
      </span>
    </label>
  );
}

/**
 * One cluster's collapsible section. The header carries the same dot and
 * status word as the rail's card, read from the same store, so fixing a
 * credential here shows its effect without leaving the modal.
 */
function ClusterSection({
  cluster,
  expanded,
  onToggle,
  shown,
  onShownChange,
  children,
}: {
  cluster: HpcCluster;
  expanded: boolean;
  onToggle: () => void;
  shown: boolean;
  onShownChange: (shown: boolean) => void;
  children: ReactNode;
}) {
  const view = useHpcStatus();
  const bodyId = useId();
  const title = HPC_CLUSTER_TITLES[cluster];
  const entry = view.clusters?.find((c) => c.cluster === cluster);
  // A cluster hidden when the status was fetched has no entry: the backend
  // does not check hidden clusters at all.
  const state = entry ? entry.state : view.clusters === null && !view.unavailable ? "checking" : null;

  return (
    <section className={`user-settings-cluster${expanded ? " expanded" : ""}`} aria-label={title}>
      <h3 className="user-settings-cluster-heading">
        <button
          type="button"
          className="user-settings-cluster-head"
          aria-expanded={expanded}
          aria-controls={bodyId}
          // Spelled out: the visible parts would otherwise run together as
          // one word, "FrontierReady", for a screen reader.
          aria-label={[title, shown ? null : "hidden from sidebar", state ? STATE_LABELS[state] : null]
            .filter(Boolean)
            .join(", ")}
          onClick={onToggle}
        >
          <svg className="user-settings-cluster-chevron" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
            <polyline points="9,6 15,12 9,18" />
          </svg>
          <span className="user-settings-cluster-name">{title}</span>
          {!shown && <span className="user-settings-cluster-tag">Hidden from sidebar</span>}
          {state && (
            <span
              className={`user-settings-cluster-status${WORD_TONE[state] ? ` hpc-word--${WORD_TONE[state]}` : ""}`}
            >
              <HpcStatusDot state={state} />
              {STATE_LABELS[state]}
            </span>
          )}
        </button>
      </h3>
      {expanded && (
        <div id={bodyId} className="user-settings-cluster-body">
          <div className="user-settings-switch-row">
            <span className="user-settings-switch-text">
              <span className="user-settings-switch-label">Show in sidebar</span>
              <span className="user-settings-hint">
                Hiding it also stops VISTA checking {title}. Its credentials are kept.
              </span>
            </span>
            <button
              type="button"
              role="switch"
              aria-checked={shown}
              aria-label={`Show ${title} in sidebar`}
              className="user-settings-switch"
              onClick={() => onShownChange(!shown)}
            >
              <span className="user-settings-switch-thumb" aria-hidden="true" />
            </button>
          </div>
          {children}
        </div>
      )}
    </section>
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
  onConnected,
}: {
  cluster: GlobusCluster;
  label: string;
  initiallyConnected: boolean;
  /** After a connection completes, e.g. to recheck the cluster's card. */
  onConnected?: () => void;
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
      onConnected?.();
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
