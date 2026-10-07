"use client";

import {
  useEffect,
  useId,
  useRef,
  useState,
  type InputHTMLAttributes,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
} from "react";
import { useSettingsAutosave, type FieldMark, type SaveOutcome } from "@/lib/settings-autosave";
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

/** The fields the agent settings view reads, so a save refreshes the picker. */
const AGENT_FIELDS: Array<keyof UserSelfUpdate> = [
  "inference_provider",
  "inference_api_key",
  "inference_mag_api_key",
  "inference_olcf_api_key",
  "inference_custom_api_key",
  "inference_base_url",
];

/** The fields a cluster's checks read, so saving one rechecks that cluster. */
const CREDENTIAL_CLUSTER: Partial<Record<string, HpcCluster>> = {
  odo_s3m_token: "odo",
  frontier_s3m_token: "frontier",
  nersc_iri_token: "perlmutter",
  // Lux has none: a researcher signs in from a chat.
};

/** The section each field lives in, for going to a field whose save failed. */
function sectionOf(field: string): Section {
  if (field.startsWith("inference_")) return "agent";
  if (field.startsWith("odo_")) return "odo";
  if (field.startsWith("frontier_")) return "frontier";
  if (field.startsWith("nersc_")) return "perlmutter";
  if (field.startsWith("lux_")) return "lux";
  return "agent";
}

const HIDDEN_FIELD = "hpc_hidden_clusters";

/** The element a field's label, error and jump-to-field all refer to. */
function fieldId(field: string, cluster?: HpcCluster): string {
  return `settings-field-${field}${cluster ? `-${cluster}` : ""}`;
}

/** What a field component spreads onto its input, its save mark, and why its save failed. */
type FieldBinding = {
  inputProps: InputHTMLAttributes<HTMLInputElement>;
  mark?: FieldMark;
  error?: string;
};

/** How each field is named when its save is announced. */
const FIELD_LABELS: Record<string, string> = {
  inference_provider: "Inference provider",
  inference_api_key: "AmSC i2 API key",
  inference_mag_api_key: "AmSC MAG project access token",
  inference_olcf_api_key: "OLCF Inference S3M token",
  inference_custom_api_key: "Custom endpoint API key",
  inference_base_url: "Inference endpoint",
  nersc_account: "NERSC account",
  nersc_remote_dir: "NERSC remote directory",
  nersc_iri_token: "NERSC IRI token",
  odo_remote_dir: "Odo remote directory",
  odo_s3m_token: "Odo S3M token",
  frontier_remote_dir: "Frontier remote directory",
  frontier_s3m_token: "Frontier S3M token",
  lux_account: "Lux account",
  lux_remote_dir: "Lux remote directory",
};

type Bind = (field: TextField, kind: "text" | "secret") => FieldBinding;

const blankToNull = (s: string) => (s.trim() === "" ? null : s.trim());

/**
 * The settings modal: a navigation list (Appearance, Agent, and the resource
 * tree of institution › facility › cluster) beside one section at a time.
 *
 * Fetches the full `UserPublicWithConfig` view (with decrypted tokens) on
 * open — the nav-rail's cached user is the light view, which intentionally
 * omits secrets. There is no Save: each field saves itself through a
 * single-field `PUT /users/me` (see `lib/settings-autosave.ts`), text after a
 * pause, a secret on blur or paste, a switch or choice at once, and closing
 * sends whatever is still pending. Each field marks its own save (a check, a
 * cross with the reason, or a spinner when slow), a section holding a failed
 * field is marked in the navigation, and a hidden live region announces each
 * outcome. Opened from a cluster's card (`initialCluster`) it shows that
 * cluster's section, and otherwise Agent. Below about 720 px of modal width the
 * navigation and the section take turns (see `.settings-layout`).
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
  const [focusTarget, setFocusTarget] = useState<string | null>(null);
  // The latest draft, for the save callbacks, which outlive a render.
  const draftRef = useRef<Draft | null>(null);
  draftRef.current = draft;
  /** The cluster whose sidebar switch was toggled last: where a failed visibility save is shown. */
  const lastToggled = useRef<HpcCluster | null>(null);
  /** Secret fields a paste just went into, so the change that follows saves at once. */
  const pasted = useRef(new Set<string>());

  async function saveField(field: string, value: unknown) {
    await updateCurrentUser({ [field]: value } as UserSelfUpdate);
    // Fix a credential and see it straight away: recheck just that cluster
    // rather than wait for the next poll.
    const cluster = CREDENTIAL_CLUSTER[field];
    if (cluster && !draftRef.current?.hidden.has(cluster)) void recheckHpcStatus(cluster);
    if (field === HIDDEN_FIELD) void refreshHpcStatus();
    // The picker's label and list follow the provider, endpoint and key.
    if ((AGENT_FIELDS as string[]).includes(field)) void refreshAgentSettings();
  }

  const [announcement, setAnnouncement] = useState("");

  function announce(field: string, outcome: SaveOutcome) {
    const label =
      field === HIDDEN_FIELD
        ? `Show ${lastToggled.current ? HPC_CLUSTER_TITLES[lastToggled.current] : "cluster"} in sidebar`
        : (FIELD_LABELS[field] ?? field);
    setAnnouncement(outcome.ok ? `${label} saved.` : `Couldn't save ${label}: ${outcome.message}`);
  }

  const autosave = useSettingsAutosave({ save: saveField, onSettled: announce });

  async function load() {
    setLoading(true);
    setLoadError(null);
    try {
      const loaded = await fetchCurrentUserWithConfig();
      const next = draftFrom(loaded);
      const seeded: Record<string, unknown> = { inference_provider: next.provider };
      for (const field of TEXT_FIELDS) seeded[field] = blankToNull(next.text[field]);
      seeded[HIDDEN_FIELD] = HPC_CLUSTERS.filter((c) => next.hidden.has(c));
      autosave.seed(seeded);
      setUser(loaded);
      setDraft(next);
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : "Failed to load user.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
    // Once, on open.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Closing, however it happens, still sends what is pending: the requests
  // outlive the modal.
  const flushRef = useRef(autosave.flush);
  flushRef.current = autosave.flush;
  useEffect(() => () => void flushRef.current(), []);

  function close() {
    void autosave.flush();
    onClose();
  }

  // Close on Escape so the modal behaves like every other dialog in the app.
  const closeRef = useRef(close);
  closeRef.current = close;
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") closeRef.current();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // After going to a failed field, focus it once its section has rendered.
  useEffect(() => {
    if (!focusTarget) return;
    document.getElementById(focusTarget)?.focus();
    setFocusTarget(null);
  }, [focusTarget, section]);

  /** Each failed field's section and the element to focus there. */
  const failures = Object.keys(autosave.errors).flatMap((field) => {
    if (field !== HIDDEN_FIELD) return [{ section: sectionOf(field), id: fieldId(field) }];
    const cluster = lastToggled.current;
    return cluster ? [{ section: cluster as Section, id: fieldId(HIDDEN_FIELD, cluster) }] : [];
  });
  const failedSections = new Set(failures.map((f) => f.section));

  function choose(next: Section) {
    setSection(next);
    setPane("section");
    // A section marked as holding a failed field opens on that field.
    const failure = failures.find((f) => f.section === next);
    if (failure) setFocusTarget(failure.id);
  }

  const bind: Bind = (field, kind) => {
    const error = autosave.errors[field];
    return {
      error,
      mark: autosave.marks[field],
      inputProps: {
        id: fieldId(field),
        value: draft?.text[field] ?? "",
        onChange: (e) => {
          const value = e.target.value;
          setDraft((d) => (d ? { ...d, text: { ...d.text, [field]: value } } : d));
          const how = kind === "text" ? "debounce" : pasted.current.delete(field) ? "now" : "hold";
          autosave.edit(field, blankToNull(value), how);
        },
        onPaste: kind === "secret" ? () => void pasted.current.add(field) : undefined,
        onBlur: () => autosave.commit(field),
        "aria-invalid": error ? true : undefined,
        "aria-describedby": error ? `${fieldId(field)}-error` : undefined,
      },
    };
  };

  function setShown(cluster: HpcCluster, shown: boolean) {
    if (!draft) return;
    const hidden = new Set(draft.hidden);
    if (shown) hidden.delete(cluster);
    else hidden.add(cluster);
    setDraft({ ...draft, hidden });
    lastToggled.current = cluster;
    // In rail order, so the stored list does not churn with click order.
    autosave.edit(HIDDEN_FIELD, HPC_CLUSTERS.filter((c) => hidden.has(c)), "now");
  }

  function setProvider(provider: string) {
    setDraft((d) => (d ? { ...d, provider } : d));
    autosave.edit("inference_provider", provider, "now");
  }

  // Before the user loads, every cluster reads as shown: the list is the
  // same either way, and only the "hidden" markers wait for it.
  const hidden = draft?.hidden ?? new Set<HpcCluster>();

  let content: ReactNode;
  if (section === "appearance") {
    // The theme is this machine's, not the user row's, so it needs nothing
    // loaded and applies at once.
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
        bind={bind}
        setProvider={setProvider}
        providerMark={autosave.marks.inference_provider}
        providerError={autosave.errors.inference_provider}
      />
    );
  } else {
    content = (
      <ClusterSection
        key={section}
        cluster={section}
        shown={!draft.hidden.has(section)}
        onShownChange={(shown) => setShown(section, shown)}
        switchMark={lastToggled.current === section ? autosave.marks[HIDDEN_FIELD] : undefined}
        switchError={lastToggled.current === section ? autosave.errors[HIDDEN_FIELD] : undefined}
      >
        <ClusterFields cluster={section} user={user} bind={bind} />
      </ClusterSection>
    );
  }

  return (
    <div className="modal-backdrop" onClick={close}>
      <div
        className="modal settings-modal"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label="Settings"
      >
        <div className="panel-header">
          <div className="panel-title">Settings</div>
          <button type="button" className="button ghost" onClick={close}>
            Close
          </button>
        </div>
        {/* Each field's mark is visual; this says the same in words. */}
        <div className="visually-hidden" role="status" aria-live="polite">
          {announcement}
        </div>
        <div className="settings-layout" data-pane={pane}>
          <SettingsNav section={section} hidden={hidden} failed={failedSections} onChoose={choose} />
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
      </div>
    </div>
  );
}

/**
 * A field's save mark: a spinner for a slow save, a check that fades once
 * saved, a cross while failed, and nothing at all for a save in flight. Visual
 * only: the modal's live region says the same in words.
 */
function SaveMark({ mark }: { mark?: FieldMark }) {
  if (!mark) return null;
  return (
    <span className="settings-mark" data-mark={mark} aria-hidden="true">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
        {mark === "saved" && <path d="M5 12.5l4.5 4.5L19 7.5" />}
        {mark === "failed" && <path d="M6 6l12 12M18 6L6 18" />}
        {mark === "slow" && <path d="M12 3a9 9 0 1 0 9 9" />}
      </svg>
    </span>
  );
}

/** Why a field's last save failed, under the field. */
function FieldError({ field, error }: { field: string; error?: string }) {
  if (!error) return null;
  return (
    <span id={`${fieldId(field)}-error`} className="error settings-field-error">
      {error}
    </span>
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
  failed,
  onChoose,
}: {
  section: Section;
  hidden: Set<HpcCluster>;
  /** Sections holding a field whose save failed. */
  failed: Set<Section>;
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
        failed={failed.has("agent")}
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
                  failed={failed.has(cluster)}
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
  failed = false,
  onClick,
  className,
  children,
}: {
  /** Spelled out: the visible parts would otherwise run together as one word. */
  label: string;
  current: boolean;
  /** The section holds a field whose save failed: a cross replaces its note. */
  failed?: boolean;
  onClick: () => void;
  className?: string;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      className={`settings-nav-entry${className ? ` ${className}` : ""}`}
      aria-current={current ? "page" : undefined}
      aria-label={failed ? `${label}, couldn't save` : label}
      data-failed={failed || undefined}
      onClick={onClick}
    >
      {children}
      {failed && (
        <span className="settings-nav-failed">
          <SaveMark mark="failed" />
        </span>
      )}
    </button>
  );
}

/** One cluster in the tree, with the rail's dot and word for it. */
function ClusterNavEntry({
  cluster,
  hidden,
  current,
  failed,
  onClick,
}: {
  cluster: HpcCluster;
  hidden: boolean;
  current: boolean;
  failed: boolean;
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
      failed={failed}
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
  bind,
  setProvider,
  providerMark,
  providerError,
}: {
  draft: Draft;
  bind: Bind;
  setProvider: (provider: string) => void;
  providerMark?: FieldMark;
  providerError?: string;
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
          <span className="settings-control-row">
            <select
              id={fieldId("inference_provider")}
              className="input"
              value={selected}
              onChange={(e) => setProvider(e.target.value)}
              disabled={!settings}
              aria-invalid={providerError ? true : undefined}
              aria-describedby={providerError ? `${fieldId("inference_provider")}-error` : undefined}
            >
              {configured && <option value="">Custom, from configuration</option>}
              {settings?.providers.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
            <SaveMark mark={providerMark} />
          </span>
          <FieldError field="inference_provider" error={providerError} />
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
          <SavedField
            label="Inference endpoint"
            field="inference_base_url"
            binding={bind("inference_base_url", "text")}
            placeholder="https://gateway.example/v1"
            hint="Any OpenAI-compatible endpoint."
          />
        )}

        {keyField && copy && (
          <SavedField
            key={keyField}
            label={copy.label}
            field={keyField}
            binding={bind(keyField, "secret")}
            secret
            placeholder="API key"
            hint={copy.hint}
          />
        )}
      </div>
    </section>
  );
}

/** Each cluster's own fields, in the order the old collapsible sections had them. */
function ClusterFields({
  cluster,
  user,
  bind,
}: {
  cluster: HpcCluster;
  user: UserPublicWithConfig;
  bind: Bind;
}) {
  switch (cluster) {
    case "odo":
      return (
        <>
          <S3mTokenField
            label="Odo S3M token"
            field="odo_s3m_token"
            binding={bind("odo_s3m_token", "secret")}
            hint="From any OLCF project with S3M access. Odo jobs are charged to that project."
          />
          <SavedField
            label="Odo remote directory"
            field="odo_remote_dir"
            binding={bind("odo_remote_dir", "text")}
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
            field="frontier_s3m_token"
            binding={bind("frontier_s3m_token", "secret")}
            hint="From any OLCF project with S3M access, and separate from Odo's token. Frontier jobs are charged to that project."
          />
          <SavedField
            label="Frontier remote directory"
            field="frontier_remote_dir"
            binding={bind("frontier_remote_dir", "text")}
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
          <SavedField
            label="NERSC account"
            field="nersc_account"
            binding={bind("nersc_account", "text")}
            placeholder="e.g. m1234"
            hint="NERSC project account for Slurm submission."
          />
          <SavedField
            label="NERSC remote directory"
            field="nersc_remote_dir"
            binding={bind("nersc_remote_dir", "text")}
            placeholder="/pscratch/sd/<u>/<user>/.vista"
            hint={
              <>
                Absolute remote dir on the NERSC machine. Required for Perlmutter. VISTA keeps <code>jobs/</code>{" "}
                (sources) and <code>out/</code> (logs and outputs) inside it.
              </>
            }
          />
          <SavedField
            label="NERSC IRI token"
            field="nersc_iri_token"
            binding={bind("nersc_iri_token", "secret")}
            secret
            placeholder="Globus access token"
            hint="Globus access token for NERSC IRI. Stored encrypted at rest."
          />
        </>
      );
    case "lux":
      return (
        <>
          <SavedField
            label="Lux account"
            field="lux_account"
            binding={bind("lux_account", "text")}
            placeholder="e.g. abc123"
            hint="Required for Lux. The OLCF project Lux jobs are charged to."
          />
          <SavedField
            label="Lux remote directory"
            field="lux_remote_dir"
            binding={bind("lux_remote_dir", "text")}
            placeholder="/lustre/orion/<project>/proj-shared/vista"
            hint={<GroupWritableHint cluster="Lux" runsAs="you" />}
          />
        </>
      );
  }
}

/**
 * One saved text field: its label, input, why its last save failed (keeping
 * what was entered), and its hint. A secret is a password input with nothing
 * remembered by the browser. A remote directory has no default: where a
 * project keeps its files is specific to the project and the filesystem, so
 * VISTA does not guess.
 */
function SavedField({
  label,
  field,
  binding,
  secret = false,
  placeholder,
  hint,
}: {
  label: string;
  field: TextField;
  binding: FieldBinding;
  secret?: boolean;
  placeholder: string;
  hint: ReactNode;
}) {
  return (
    <label className="project-modal-label">
      {label}
      <span className="settings-input-wrap" data-marked={binding.mark ? true : undefined}>
        <input
          className="input"
          type={secret ? "password" : undefined}
          placeholder={placeholder}
          autoComplete={secret ? "off" : undefined}
          spellCheck={false}
          {...binding.inputProps}
        />
        <SaveMark mark={binding.mark} />
      </span>
      <FieldError field={field} error={binding.error} />
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
  field,
  binding,
  hint,
}: {
  label: string;
  field: TextField;
  binding: FieldBinding;
  hint: string;
}) {
  return (
    <SavedField
      label={label}
      field={field}
      binding={binding}
      secret
      placeholder="Bearer token"
      hint={
        <>
          {hint} Stored encrypted at rest.{" "}
          <a
            href="https://docs.olcf.ornl.gov/services_and_applications/s3m/overview.html#get-a-token"
            target="_blank"
            rel="noopener noreferrer"
          >
            Get a token
          </a>
          .
        </>
      }
    />
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
  switchMark,
  switchError,
  children,
}: {
  cluster: HpcCluster;
  shown: boolean;
  onShownChange: (shown: boolean) => void;
  /** The sidebar switch's save mark, if this was the one toggled. */
  switchMark?: FieldMark;
  /** Why the last save of the sidebar switch failed, if this was the one toggled. */
  switchError?: string;
  children: ReactNode;
}) {
  const state = useClusterState(cluster);
  const title = HPC_CLUSTER_TITLES[cluster];
  const { institution, facility } = resourcePlace(cluster);
  const switchId = fieldId(HIDDEN_FIELD, cluster);

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
              {CREDENTIAL_CLUSTER_FIELDS.has(cluster) && " Its credentials are kept."}
            </span>
            {switchError && (
              <span id={`${switchId}-error`} className="error settings-field-error">
                {switchError}
              </span>
            )}
          </span>
          <SaveMark mark={switchMark} />
          <button
            id={switchId}
            type="button"
            role="switch"
            aria-checked={shown}
            aria-label={`Show ${title} in sidebar`}
            aria-describedby={switchError ? `${switchId}-error` : undefined}
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

/** The clusters that store a credential of their own. */
const CREDENTIAL_CLUSTER_FIELDS = new Set(Object.values(CREDENTIAL_CLUSTER));

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
 * Deliberately outside autosave: this is an exchange, not a value. The
 * credential never reaches the browser, what the researcher pastes is
 * single-use, and the backend stores the result itself, so there is nothing
 * for a field save to carry, and it takes effect at once.
 *
 * `initiallyConnected` seeds the display from the loaded user and is not read
 * again. Re-fetching after a connection would rebuild the modal's draft under
 * fields still being edited.
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
