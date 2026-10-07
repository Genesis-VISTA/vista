"use client";

import { useEffect, useId, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type ReactNode } from "react";
import {
  completeGlobusLogin,
  fetchCurrentUserWithConfig,
  startGlobusLogin,
  updateCurrentUser,
  type GlobusCluster,
  type UserPublicWithConfig,
  type UserSelfUpdate,
} from "@/lib/user";
import { refreshAgentSettings, useAgentSettings } from "@/lib/agent-settings";
import {
  HPC_CLUSTERS,
  HPC_CLUSTER_TITLES,
  RESOURCE_TREE,
  recheckHpcStatus,
  refreshHpcStatus,
  resourcePlace,
  useHpcStatus,
  type HpcCluster,
  type HpcDisplayState,
} from "@/lib/hpc-status";
import { useTheme, type ThemeChoice } from "@/lib/theme";
import { HpcStatusDot, STATE_LABELS, WORD_TONE } from "./HpcStatusSection";

/** What the modal can show: Appearance, Agent, or one cluster's section. */
type Section = "appearance" | "agent" | HpcCluster;

/** Every free-text field the modal edits, as named in `UserSelfUpdate`. */
const TEXT_FIELDS = [
  "inference_api_key",
  "inference_mag_api_key",
  "inference_olcf_api_key",
  "inference_custom_api_key",
  "inference_base_url",
  "nersc_account",
  "nersc_remote_dir",
  "odo_remote_dir",
  "frontier_remote_dir",
  "lux_remote_dir",
  "lux_account",
  "odo_s3m_token",
  "frontier_s3m_token",
  "nersc_iri_token",
] as const satisfies ReadonlyArray<keyof UserSelfUpdate & keyof UserPublicWithConfig>;

type TextField = (typeof TEXT_FIELDS)[number];

/**
 * What the researcher has typed or toggled and not yet saved. It lives above
 * the sections, so moving between them keeps every edit.
 */
type Draft = {
  text: Record<TextField, string>;
  /** Null until the researcher chooses one: the backend's choice stands. */
  provider: string | null;
  hidden: Set<HpcCluster>;
};

function draftFrom(user: UserPublicWithConfig): Draft {
  const text = {} as Record<TextField, string>;
  for (const field of TEXT_FIELDS) text[field] = user[field] ?? "";
  return {
    text,
    provider: user.inference_provider ?? null,
    hidden: new Set((user.hpc_hidden_clusters ?? []).filter((c): c is HpcCluster => HPC_CLUSTERS.includes(c as HpcCluster))),
  };
}

/** The fields each cluster's checks read, so a save rechecks only what changed. */
const CREDENTIAL_FIELDS: Record<HpcCluster, Array<keyof UserSelfUpdate>> = {
  odo: ["odo_s3m_token"],
  frontier: ["frontier_s3m_token"],
  perlmutter: ["nersc_iri_token"],
  lux: [], // nothing stored: a researcher signs in from a chat
};

/** The fields the agent settings view reads, so a save refreshes the picker. */
const AGENT_FIELDS: Array<keyof UserSelfUpdate> = [
  "inference_provider",
  "inference_api_key",
  "inference_mag_api_key",
  "inference_olcf_api_key",
  "inference_custom_api_key",
  "inference_base_url",
];

/**
 * The settings modal: a navigation list (Appearance, Agent, and the resource
 * tree of institution › facility › cluster) beside one section at a time.
 *
 * Fetches the full `UserPublicWithConfig` view (with decrypted tokens) on
 * open — the nav-rail's cached user is the light view, which intentionally
 * omits secrets — and writes back through `PUT /users/me`. Opened from a
 * cluster's card (`initialCluster`) it shows that cluster's section, and
 * otherwise Agent. Below about 720 px of modal width the navigation and the
 * section take turns (see `.settings-layout` in globals.css).
 */
export function UserSettingsModal({
  onClose,
  initialCluster,
}: {
  onClose: () => void;
  initialCluster?: HpcCluster;
}) {
  const [user, setUser] = useState<UserPublicWithConfig | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [section, setSection] = useState<Section>(initialCluster ?? "agent");
  // Only read in a narrow modal, where the list and a section take turns. A
  // deep link from a card goes straight to its section there too.
  const [pane, setPane] = useState<"nav" | "section">(initialCluster ? "section" : "nav");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    setLoadError(null);
    try {
      const loaded = await fetchCurrentUserWithConfig();
      setUser(loaded);
      setDraft(draftFrom(loaded));
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

  function choose(next: Section) {
    setSection(next);
    setPane("section");
  }

  function setText(field: TextField) {
    return (value: string) =>
      setDraft((d) => (d ? { ...d, text: { ...d.text, [field]: value } } : d));
  }

  function setShown(cluster: HpcCluster, shown: boolean) {
    setDraft((d) => {
      if (!d) return d;
      const hidden = new Set(d.hidden);
      if (shown) hidden.delete(cluster);
      else hidden.add(cluster);
      return { ...d, hidden };
    });
  }

  async function save() {
    if (!user || !draft) return;
    // Build a minimal diff against the loaded user so we only send fields
    // that actually changed. Empty strings become null to clear the field.
    const blankToNull = (s: string) => (s.trim() === "" ? null : s.trim());
    const diff: UserSelfUpdate = {};
    for (const field of TEXT_FIELDS) {
      const next = blankToNull(draft.text[field]);
      if ((user[field] ?? null) !== next) (diff as Record<string, string | null>)[field] = next;
    }
    if (draft.provider !== (user.inference_provider ?? null)) diff.inference_provider = draft.provider;
    // In rail order, so the stored list does not churn with click order.
    const initiallyHidden = user.hpc_hidden_clusters ?? [];
    const nextHidden = HPC_CLUSTERS.filter((c) => draft.hidden.has(c));
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
        (c) => !draft.hidden.has(c) && CREDENTIAL_FIELDS[c].some((field) => field in diff),
      );
      if (changed.length === 1) void recheckHpcStatus(changed[0]);
      else if (changed.length > 1) void recheckHpcStatus();
      else if (hiddenChanged) void refreshHpcStatus();
      // The picker's label and list follow the provider, endpoint and key.
      if (AGENT_FIELDS.some((field) => field in diff)) void refreshAgentSettings();
      onClose();
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : "Failed to save settings.");
      setSaving(false);
    }
  }

  // Before the user loads, every cluster reads as shown: the list is the
  // same either way, and only the "hidden" markers wait for it.
  const hidden = draft?.hidden ?? new Set<HpcCluster>();

  let content: ReactNode;
  if (section === "appearance") {
    // The theme is this machine's, not the user row's, so it needs nothing
    // loaded and applies at once rather than on Save.
    content = <AppearanceSection />;
  } else if (!user || !draft) {
    content = (
      <div className="settings-section-body">
        {loading && <div style={{ fontSize: 13, color: "var(--muted)" }}>Loading…</div>}
        {loadError && (
          <div className="error" style={{ fontSize: 13 }}>
            {loadError}{" "}
            <button type="button" className="button ghost button-xs" onClick={() => void load()}>
              Retry
            </button>
          </div>
        )}
      </div>
    );
  } else if (section === "agent") {
    content = (
      <AgentSection
        draft={draft}
        setText={setText}
        setProvider={(provider) => setDraft((d) => (d ? { ...d, provider } : d))}
      />
    );
  } else {
    content = (
      <ClusterSection
        key={section}
        cluster={section}
        shown={!draft.hidden.has(section)}
        onShownChange={(shown) => setShown(section, shown)}
      >
        <ClusterFields cluster={section} user={user} draft={draft} setText={setText} />
      </ClusterSection>
    );
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal settings-modal"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label="Settings"
      >
        <div className="panel-header">
          <div className="panel-title">Settings</div>
          <button type="button" className="button ghost" onClick={onClose}>
            Close
          </button>
        </div>
        <div className="settings-layout" data-pane={pane}>
          <SettingsNav section={section} hidden={hidden} onChoose={choose} />
          <div className="settings-main">
            <button type="button" className="settings-back" onClick={() => setPane("nav")}>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
                <polyline points="15,6 9,12 15,18" />
              </svg>
              All settings
            </button>
            {content}
          </div>
        </div>
        <div className="settings-footer">
          {saveError && (
            <div className="error" style={{ fontSize: 12 }}>
              {saveError}
            </div>
          )}
          <button type="button" className="button ghost" onClick={onClose} disabled={saving}>
            Cancel
          </button>
          <button type="button" className="button" onClick={() => void save()} disabled={saving || !draft}>
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}

/**
 * A cluster's state as the rail shows it, from the same store. A cluster
 * hidden when the status was fetched has no entry: the backend does not
 * check hidden clusters at all.
 */
function useClusterState(cluster: HpcCluster): HpcDisplayState | null {
  const view = useHpcStatus();
  const entry = view.clusters?.find((c) => c.cluster === cluster);
  return entry ? entry.state : view.clusters === null && !view.unavailable ? "checking" : null;
}

function SettingsNav({
  section,
  hidden,
  onChoose,
}: {
  section: Section;
  hidden: Set<HpcCluster>;
  onChoose: (section: Section) => void;
}) {
  const { settings } = useAgentSettings();
  const agentNote = settings ? (settings.hasCredential ? "Key set" : "No key") : null;

  return (
    <nav className="settings-nav" aria-label="Settings sections">
      <NavEntry label="Appearance" current={section === "appearance"} onClick={() => onChoose("appearance")}>
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
          <circle cx="12" cy="12" r="4" />
          <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2" />
        </svg>
        <span className="settings-nav-name">Appearance</span>
      </NavEntry>
      <NavEntry
        label={["Agent", agentNote?.toLowerCase()].filter(Boolean).join(", ")}
        current={section === "agent"}
        onClick={() => onChoose("agent")}
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
          <rect x="4" y="6" width="16" height="13" rx="3" />
          <path d="M12 2v4M9 12v1M15 12v1" />
        </svg>
        <span className="settings-nav-name">Agent</span>
        {agentNote && (
          <span className={`settings-nav-note${settings?.hasCredential ? "" : " hpc-word--warn"}`}>{agentNote}</span>
        )}
      </NavEntry>

      <div className="settings-nav-group">Resources</div>
      {RESOURCE_TREE.map((institution) => (
        <div key={institution.id} role="group" aria-label={institution.name}>
          <div className="settings-nav-institution">{institution.name}</div>
          {institution.facilities.map((facility) => (
            <div key={facility.id} role="group" aria-label={facility.name}>
              <div className="settings-nav-facility">{facility.name}</div>
              {facility.clusters.map((cluster) => (
                <ClusterNavEntry
                  key={cluster}
                  cluster={cluster}
                  hidden={hidden.has(cluster)}
                  current={section === cluster}
                  onClick={() => onChoose(cluster)}
                />
              ))}
            </div>
          ))}
        </div>
      ))}
    </nav>
  );
}

function NavEntry({
  label,
  current,
  onClick,
  className,
  children,
}: {
  /** Spelled out: the visible parts would otherwise run together as one word. */
  label: string;
  current: boolean;
  onClick: () => void;
  className?: string;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      className={`settings-nav-entry${className ? ` ${className}` : ""}`}
      aria-current={current ? "page" : undefined}
      aria-label={label}
      onClick={onClick}
    >
      {children}
    </button>
  );
}

/** One cluster in the tree, with the rail's dot and word for it. */
function ClusterNavEntry({
  cluster,
  hidden,
  current,
  onClick,
}: {
  cluster: HpcCluster;
  hidden: boolean;
  current: boolean;
  onClick: () => void;
}) {
  const state = useClusterState(cluster);
  const title = HPC_CLUSTER_TITLES[cluster];
  return (
    <NavEntry
      className="settings-nav-resource"
      label={[title, hidden ? "hidden from sidebar" : null, state ? STATE_LABELS[state] : null]
        .filter(Boolean)
        .join(", ")}
      current={current}
      onClick={onClick}
    >
      <span className="settings-nav-dot">{state && <HpcStatusDot state={state} />}</span>
      <span className="settings-nav-name">{title}</span>
      {hidden ? (
        <span className="settings-nav-note">Hidden from sidebar</span>
      ) : (
        state && (
          <span className={`settings-nav-note${WORD_TONE[state] ? ` hpc-word--${WORD_TONE[state]}` : ""}`}>
            {STATE_LABELS[state]}
          </span>
        )
      )}
    </NavEntry>
  );
}

function SectionHeader({
  titleId,
  title,
  place,
  children,
}: {
  titleId?: string;
  title: string;
  /** A line above the title, e.g. the cluster's institution and facility. */
  place?: string;
  /** Below the title: a description, or the cluster's status. */
  children?: ReactNode;
}) {
  return (
    <div className="settings-section-header">
      {place && <span className="settings-section-place">{place}</span>}
      <h2 id={titleId} className="settings-section-title">
        {title}
      </h2>
      {children}
    </div>
  );
}

function AppearanceSection() {
  const titleId = useId();
  return (
    <section className="settings-section" aria-labelledby={titleId}>
      <SectionHeader titleId={titleId} title="Appearance">
        <span className="user-settings-hint">For this computer. Applies straight away.</span>
      </SectionHeader>
      <div className="settings-section-body">
        <AppearanceSetting labelledBy={titleId} />
      </div>
    </section>
  );
}

/** Which stored key each provider uses; see `PROVIDER_PRESETS` in the backend. */
const PROVIDER_KEY_FIELDS: Record<string, TextField> = {
  i2: "inference_api_key",
  mag: "inference_mag_api_key",
  olcf: "inference_olcf_api_key",
  custom: "inference_custom_api_key",
};

const KEY_SUFFIX = "Required to chat; everything else works without it. Stored encrypted at rest.";

/** Each provider's key field: its label and what to say about it. */
function keyFieldCopy(provider: string, configured: boolean): { label: string; hint: ReactNode } {
  if (configured) {
    return { label: "API key", hint: <>Key for the configured endpoint. {KEY_SUFFIX}</> };
  }
  switch (provider) {
    case "i2":
      return {
        label: "AmSC i2 API key",
        hint: (
          <>
            {KEY_SUFFIX}{" "}
            <a href="https://api.i2-core.american-science-cloud.org" target="_blank" rel="noopener noreferrer">
              Get a key
            </a>
            .
          </>
        ),
      };
    case "mag":
      return {
        label: "AmSC MAG project access token",
        hint: <>A project access token for the AmSC Model Access Gateway. {KEY_SUFFIX}</>,
      };
    case "olcf":
      return {
        label: "OLCF Inference S3M token",
        hint: (
          <>
            An S3M token from an OLCF project with access to the Inference Service, separate from Odo&apos;s
            and Frontier&apos;s. {KEY_SUFFIX}{" "}
            <a
              href="https://docs.olcf.ornl.gov/services_and_applications/olcf_inference/index.html"
              target="_blank"
              rel="noopener noreferrer"
            >
              About OLCF Inference
            </a>
            .
          </>
        ),
      };
    default:
      return { label: "Custom endpoint API key", hint: <>Key for the endpoint above. {KEY_SUFFIX}</> };
  }
}

/**
 * The inference provider and its credential. The options come from the
 * backend (`GET /users/me/inference`), never from a list of the interface's
 * own; there is no Model field, since the model picker is the one place to
 * choose a model.
 */
function AgentSection({
  draft,
  setText,
  setProvider,
}: {
  draft: Draft;
  setText: (field: TextField) => (value: string) => void;
  setProvider: (provider: string) => void;
}) {
  const { settings, error } = useAgentSettings();
  // The installation's configuration is in effect only until the researcher
  // chooses a provider of their own.
  const configured = draft.provider === null && settings?.source === "config";
  const selected = draft.provider ?? (configured ? "" : (settings?.provider ?? ""));
  const option = settings?.providers.find((p) => p.id === selected);
  const keyField = configured ? "inference_api_key" : PROVIDER_KEY_FIELDS[selected];
  const copy = keyField ? keyFieldCopy(selected, configured) : null;

  return (
    <section className="settings-section" aria-label="Agent">
      <SectionHeader title="Agent">
        <span className="user-settings-hint">
          The model provider VISTA&apos;s assistant runs on. Changes apply from your next message.
        </span>
      </SectionHeader>
      <div className="settings-section-body">
        {error && !settings && <div className="error" style={{ fontSize: 13 }}>{error}</div>}
        <label className="project-modal-label">
          Inference provider
          <select
            className="input"
            value={selected}
            onChange={(e) => setProvider(e.target.value)}
            disabled={!settings}
          >
            {configured && <option value="">Custom, from configuration</option>}
            {settings?.providers.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
          <span className="user-settings-hint">
            {configured ? (
              <>
                From this installation&apos;s own configuration, at <code>{settings?.baseUrl}</code>. Choose a
                provider to use it instead.
              </>
            ) : option?.defaultModel ? (
              <>Uses {option.defaultModel} unless you choose another model from the model picker.</>
            ) : option ? (
              <>Has no default model: choose one from the model picker at the top of the chat.</>
            ) : null}
          </span>
        </label>

        {option?.takesUrl && (
          <label className="project-modal-label">
            Inference endpoint
            <input
              className="input"
              value={draft.text.inference_base_url}
              onChange={(e) => setText("inference_base_url")(e.target.value)}
              placeholder="https://gateway.example/v1"
              spellCheck={false}
            />
            <span className="user-settings-hint">Any OpenAI-compatible endpoint.</span>
          </label>
        )}

        {keyField && copy && (
          <label className="project-modal-label">
            {copy.label}
            <input
              key={keyField}
              className="input"
              type="password"
              value={draft.text[keyField]}
              onChange={(e) => setText(keyField)(e.target.value)}
              placeholder="API key"
              autoComplete="off"
              spellCheck={false}
            />
            <span className="user-settings-hint">{copy.hint}</span>
          </label>
        )}
      </div>
    </section>
  );
}

/** Each cluster's own fields, in the order the old collapsible sections had them. */
function ClusterFields({
  cluster,
  user,
  draft,
  setText,
}: {
  cluster: HpcCluster;
  user: UserPublicWithConfig;
  draft: Draft;
  setText: (field: TextField) => (value: string) => void;
}) {
  const t = draft.text;
  switch (cluster) {
    case "odo":
      return (
        <>
          <S3mTokenField
            label="Odo S3M token"
            value={t.odo_s3m_token}
            onChange={setText("odo_s3m_token")}
            hint="From any OLCF project with S3M access. Odo jobs are charged to that project."
          />
          <RemoteDirField
            label="Odo remote directory"
            value={t.odo_remote_dir}
            onChange={setText("odo_remote_dir")}
            placeholder="/gpfs/wolf2/olcf/<project>/proj-shared/vista"
            hint={<GroupWritableHint cluster="Odo" />}
          />
          <GlobusConnect
            cluster="odo"
            label="Odo"
            initiallyConnected={globusConnected(user, "odo")}
            onConnected={() => void recheckHpcStatus("odo")}
          />
        </>
      );
    case "frontier":
      return (
        <>
          <S3mTokenField
            label="Frontier S3M token"
            value={t.frontier_s3m_token}
            onChange={setText("frontier_s3m_token")}
            hint="From any OLCF project with S3M access, and separate from Odo's token. Frontier jobs are charged to that project."
          />
          <RemoteDirField
            label="Frontier remote directory"
            value={t.frontier_remote_dir}
            onChange={setText("frontier_remote_dir")}
            placeholder="/lustre/orion/<project>/proj-shared/vista"
            hint={<GroupWritableHint cluster="Frontier" />}
          />
          <GlobusConnect
            cluster="frontier"
            label="Frontier"
            initiallyConnected={globusConnected(user, "frontier")}
            onConnected={() => void recheckHpcStatus("frontier")}
          />
        </>
      );
    case "perlmutter":
      return (
        <>
          <label className="project-modal-label">
            NERSC account
            <input
              className="input"
              value={t.nersc_account}
              onChange={(e) => setText("nersc_account")(e.target.value)}
              placeholder="e.g. m1234"
              spellCheck={false}
            />
            <span className="user-settings-hint">NERSC project account for Slurm submission.</span>
          </label>

          <label className="project-modal-label">
            NERSC remote directory
            <input
              className="input"
              value={t.nersc_remote_dir}
              onChange={(e) => setText("nersc_remote_dir")(e.target.value)}
              placeholder="/pscratch/sd/<u>/<user>/.vista"
              spellCheck={false}
            />
            <span className="user-settings-hint">
              Absolute remote dir on the NERSC machine. Required for Perlmutter. VISTA keeps <code>jobs/</code>{" "}
              (sources) and <code>out/</code> (logs and outputs) inside it.
            </span>
          </label>

          <label className="project-modal-label">
            NERSC IRI token
            <input
              className="input"
              type="password"
              value={t.nersc_iri_token}
              onChange={(e) => setText("nersc_iri_token")(e.target.value)}
              placeholder="Globus access token"
              autoComplete="off"
              spellCheck={false}
            />
            <span className="user-settings-hint">Globus access token for NERSC IRI. Stored encrypted at rest.</span>
          </label>
        </>
      );
    case "lux":
      return (
        <>
          <label className="project-modal-label">
            Lux account
            <input
              className="input"
              value={t.lux_account}
              onChange={(e) => setText("lux_account")(e.target.value)}
              placeholder="e.g. abc123"
              spellCheck={false}
            />
            <span className="user-settings-hint">Required for Lux. The OLCF project Lux jobs are charged to.</span>
          </label>
          <RemoteDirField
            label="Lux remote directory"
            value={t.lux_remote_dir}
            onChange={setText("lux_remote_dir")}
            placeholder="/lustre/orion/<project>/proj-shared/vista"
            hint={<GroupWritableHint cluster="Lux" runsAs="you" />}
          />
        </>
      );
  }
}

/**
 * The folder on a cluster where VISTA puts this researcher's job sources and
 * outputs. No default: where a project keeps its files is specific to the
 * project and the filesystem, so VISTA does not guess.
 */
function RemoteDirField({
  label,
  value,
  onChange,
  placeholder,
  hint,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
  hint: ReactNode;
}) {
  return (
    <label className="project-modal-label">
      {label}
      <input
        className="input"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        spellCheck={false}
      />
      <span className="user-settings-hint">{hint}</span>
    </label>
  );
}

/**
 * On the OLCF clusters VISTA keeps a researcher's sources in
 * `<dir>.<user>.jobs` and everyone's logs and outputs in a shared `<dir>.out`
 * beside it. Odo and Frontier jobs run as the project's IRI automation user,
 * which creates `<dir>.out`, so the folder holding them must be writable by the
 * project's group. Temporary, until S3M tokens can use the IRI filesystem API.
 */
function GroupWritableHint({
  cluster,
  runsAs = "your project's IRI automation user",
}: {
  cluster: string;
  runsAs?: string;
}) {
  return (
    <>
      Required for {cluster}. VISTA keeps <code>&lt;dir&gt;.&lt;user&gt;.jobs</code> (your
      sources) and <code>&lt;dir&gt;.out</code> (logs and outputs, shared with your project)
      beside this directory. Jobs run as {runsAs}, so the folder holding them must be writable
      by the project&apos;s group, as <code>proj-shared</code> already is.
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
 * One cluster's section: where it lives, the same dot and status word as the
 * rail's card (read from the same store, so fixing a credential here shows its
 * effect without leaving the modal), its "Show in sidebar" switch, and then
 * its own fields.
 */
function ClusterSection({
  cluster,
  shown,
  onShownChange,
  children,
}: {
  cluster: HpcCluster;
  shown: boolean;
  onShownChange: (shown: boolean) => void;
  children: ReactNode;
}) {
  const state = useClusterState(cluster);
  const title = HPC_CLUSTER_TITLES[cluster];
  const { institution, facility } = resourcePlace(cluster);

  return (
    <section className="settings-section" aria-label={title}>
      <SectionHeader title={title} place={`${institution.name} › ${facility.name}`}>
        {state && (
          <span
            className={`settings-section-status${WORD_TONE[state] ? ` hpc-word--${WORD_TONE[state]}` : ""}`}
          >
            <HpcStatusDot state={state} />
            {STATE_LABELS[state]}
          </span>
        )}
      </SectionHeader>
      <div className="settings-section-body">
        <div className="user-settings-switch-row">
          <span className="user-settings-switch-text">
            <span className="user-settings-switch-label">Show in sidebar</span>
            <span className="user-settings-hint">
              Hiding it also stops VISTA checking {title}.
              {CREDENTIAL_FIELDS[cluster].length > 0 && " Its credentials are kept."}
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

const THEME_OPTIONS: { value: ThemeChoice; label: string }[] = [
  { value: "system", label: "System" },
  { value: "light", label: "Light" },
  { value: "dark", label: "Dark" },
];

/**
 * System / Light / Dark. A radio group rather than a select so all three are
 * visible at once, with the WAI-ARIA keyboard model: one tab stop on the
 * checked option, arrow keys move and select together. Named by the
 * Appearance section's heading (`labelledBy`).
 */
function AppearanceSetting({ labelledBy }: { labelledBy: string }) {
  const { choice, setChoice } = useTheme();
  const refs = useRef<(HTMLButtonElement | null)[]>([]);

  function onKeyDown(e: ReactKeyboardEvent<HTMLDivElement>) {
    const steps: Record<string, number> = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 };
    const step = steps[e.key];
    if (!step) return;
    e.preventDefault();
    const current = THEME_OPTIONS.findIndex((o) => o.value === choice);
    const next = (current + step + THEME_OPTIONS.length) % THEME_OPTIONS.length;
    setChoice(THEME_OPTIONS[next].value);
    refs.current[next]?.focus();
  }

  return (
    <div className="user-settings-appearance">
      <div className="theme-choice" role="radiogroup" aria-labelledby={labelledBy} onKeyDown={onKeyDown}>
        {THEME_OPTIONS.map((option, i) => (
          <button
            key={option.value}
            ref={(el) => {
              refs.current[i] = el;
            }}
            type="button"
            role="radio"
            aria-checked={choice === option.value}
            tabIndex={choice === option.value ? 0 : -1}
            className="theme-choice-option"
            onClick={() => setChoice(option.value)}
          >
            {option.label}
          </button>
        ))}
      </div>
      <span className="user-settings-hint">System matches your computer&apos;s light or dark setting.</span>
    </div>
  );
}
