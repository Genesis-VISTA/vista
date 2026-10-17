"use client";

import { useEffect, useState, type ReactNode } from "react";
import type { FieldMark } from "@/lib/settings-autosave";
import { useProjects } from "@/lib/projects";
import {
  fetchSlackLinks,
  linkSlack,
  takeOverSlack,
  unlinkSlack,
  updateSlackConfig,
  updateSlackLink,
  type SlackConfig,
  type SlackConfigUpdate,
  type SlackLink,
} from "@/lib/slack";
import { FieldError, SaveMark, SectionHeader, fieldId } from "./SettingsParts";

const CONNECTION_WORDS: Record<SlackConfig["connection"], string> = {
  off: "Not running here",
  connected: "Connected",
  standby: "Standing by",
  error: "Can't connect",
};

/**
 * Settings → Slack. Everything about Slack is set here and never from Slack:
 * the limits and unlocking especially, so a lost phone cannot loosen them.
 *
 * The tokens are write-only, like the repository tokens: they go to the OS
 * secret store, and only whether each is set comes back. A switch saves at
 * once; a text or number field saves when it loses focus. Each field marks its
 * own save, and a refused save (turning Slack on without a token, say) shows
 * the backend's reason under the field.
 */
export function SlackSection({
  config,
  onChange,
}: {
  config: SlackConfig | null;
  onChange: (config: SlackConfig) => void;
}) {
  const [marks, setMarks] = useState<Record<string, FieldMark | undefined>>({});
  const [errors, setErrors] = useState<Record<string, string | undefined>>({});

  async function save(field: string, updates: SlackConfigUpdate): Promise<boolean> {
    setMarks((m) => ({ ...m, [field]: undefined }));
    try {
      onChange(await updateSlackConfig(updates));
      setErrors((e) => ({ ...e, [field]: undefined }));
      setMarks((m) => ({ ...m, [field]: "saved" }));
      setTimeout(() => setMarks((m) => (m[field] === "saved" ? { ...m, [field]: undefined } : m)), 2000);
      return true;
    } catch (e) {
      setErrors((x) => ({ ...x, [field]: e instanceof Error ? e.message : "Couldn't save." }));
      setMarks((m) => ({ ...m, [field]: "failed" }));
      return false;
    }
  }

  if (!config) {
    return (
      <section className="settings-section" aria-label="Slack">
        <SectionHeader title="Slack" />
        <div className="settings-section-body">
          <div className="user-settings-hint">Loading…</div>
        </div>
      </section>
    );
  }

  const field = (name: string) => ({ mark: marks[name], error: errors[name] });

  return (
    <section className="settings-section" aria-label="Slack">
      <SectionHeader title="Slack">
        <span className="user-settings-hint">
          Talk to VISTA from Slack on your phone or desktop while VISTA runs on this machine. Jobs
          run with your VISTA session here; Slack never sees a cluster credential, and VISTA never
          asks for one there. Anything that changes something waits for your Confirm in Slack.
        </span>
      </SectionHeader>
      <div className="settings-section-body">
        <ConnectionStatus config={config} onChange={onChange} />

        {!config.secret_store_available && (
          <div className="error settings-field-error">
            Slack needs the system keychain to hold its tokens, and it isn&rsquo;t available:{" "}
            {config.secret_store_problem}
          </div>
        )}

        <Switch
          name="enabled"
          label="Slack"
          hint="Off by default. Needs both tokens and at least one allowed workspace."
          checked={config.enabled}
          onToggle={(on) => save("enabled", { enabled: on })}
          {...field("enabled")}
        />
        <Switch
          name="handle_here"
          label="Handle Slack on this machine"
          hint="Only one machine answers. Turn this off on machines that shouldn't."
          checked={config.handle_here}
          onToggle={(on) => save("handle_here", { handle_here: on })}
          {...field("handle_here")}
        />

        <Group title="App tokens">
          <TokenField
            name="bot_token"
            label="Bot token"
            placeholder="xoxb-…"
            saved={config.has_bot_token}
            onSave={(value) => save("bot_token", { bot_token: value })}
            {...field("bot_token")}
          />
          <TokenField
            name="app_token"
            label="App-level token"
            placeholder="xapp-…"
            saved={config.has_app_token}
            onSave={(value) => save("app_token", { app_token: value })}
            {...field("app_token")}
            hint={
              <>
                With the <code>connections:write</code> scope, for Socket Mode. See{" "}
                <code>docs/slack.md</code> for creating the app from its manifest.
              </>
            }
          />
        </Group>

        <Group title="Who may use it">
          <ListField
            key={`teams-${config.allowed_team_ids.join()}`}
            name="allowed_team_ids"
            label="Allowed workspace IDs"
            placeholder="T0123ABCD"
            values={config.allowed_team_ids}
            hint="Events from any other workspace are ignored."
            onSave={(values) => save("allowed_team_ids", { allowed_team_ids: values })}
            {...field("allowed_team_ids")}
          />
          <ListField
            key={`channels-${config.allowed_channel_ids.join()}`}
            name="allowed_channel_ids"
            label="Allowed channel IDs"
            placeholder="Any channel"
            values={config.allowed_channel_ids}
            hint="Optional. Empty allows any channel VISTA is in; DMs are always allowed."
            onSave={(values) => save("allowed_channel_ids", { allowed_channel_ids: values })}
            {...field("allowed_channel_ids")}
          />
          <LinkedAccounts />
        </Group>

        <Group title="Limits for Slack requests">
          <span className="user-settings-hint">
            A submission from Slack over any of these is refused before it is offered for
            confirmation. Jobs started here in the app are not limited.
          </span>
          <NumberField
            key={`max_nodes-${config.max_nodes}`}
            name="max_nodes"
            label="Most nodes per job"
            value={config.max_nodes}
            min={1}
            onSave={(n) => save("max_nodes", { max_nodes: n })}
            {...field("max_nodes")}
          />
          <NumberField
            key={`max_walltime_minutes-${config.max_walltime_minutes}`}
            name="max_walltime_minutes"
            label="Longest walltime (minutes)"
            value={config.max_walltime_minutes}
            min={1}
            onSave={(n) => save("max_walltime_minutes", { max_walltime_minutes: n })}
            {...field("max_walltime_minutes")}
          />
          <NumberField
            key={`max_jobs_per_day-${config.max_jobs_per_day}`}
            name="max_jobs_per_day"
            label="Jobs per day"
            value={config.max_jobs_per_day}
            min={0}
            onSave={(n) => save("max_jobs_per_day", { max_jobs_per_day: n })}
            {...field("max_jobs_per_day")}
          />
          <Switch
            name="locked"
            label="Locked"
            hint={
              config.locked
                ? `Nothing that changes anything runs from Slack until you unlock it here.${
                    config.locked_by ? ` Locked by ${config.locked_by}` : ""
                  }${config.locked_at ? ` at ${new Date(config.locked_at).toLocaleString()}` : ""}.`
                : "Say “lock” to VISTA in Slack to block anything that changes something. Only this switch unlocks it."
            }
            checked={config.locked}
            onToggle={(on) => save("locked", { locked: on })}
            {...field("locked")}
          />
        </Group>

        <Group title="Notifications and power">
          <Switch
            name="notify_desktop_jobs"
            label="Tell me in Slack about jobs started here"
            hint="Jobs started from Slack are always reported there when they finish."
            checked={config.notify_desktop_jobs}
            onToggle={(on) => save("notify_desktop_jobs", { notify_desktop_jobs: on })}
            {...field("notify_desktop_jobs")}
          />
          <Switch
            name="prevent_sleep"
            label="Keep this machine awake"
            hint="While Slack is connected. Closing a laptop's lid still sleeps it; VISTA says so in Slack first."
            checked={config.prevent_sleep}
            onToggle={(on) => save("prevent_sleep", { prevent_sleep: on })}
            {...field("prevent_sleep")}
          />
          <Switch
            name="background_mode"
            label="Run VISTA in the background"
            hint={
              config.background_problem ??
              "Start VISTA at login with no window, and keep it running after you close the window, so Slack can reach it while you are away. Takes effect at your next login; open VISTA as usual to see it."
            }
            checked={config.background_mode}
            disabled={config.background_problem !== null && !config.background_mode}
            onToggle={(on) => save("background_mode", { background_mode: on })}
            {...field("background_mode")}
          />
        </Group>
      </div>
    </section>
  );
}

function ConnectionStatus({
  config,
  onChange,
}: {
  config: SlackConfig;
  onChange: (config: SlackConfig) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const tone = config.connection === "connected" ? "" : config.connection === "error" ? " hpc-word--warn" : " slack-status-word--quiet";

  async function takeOver() {
    setBusy(true);
    setError(null);
    try {
      onChange(await takeOverSlack());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't take over.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="slack-status" role="status">
      <span className={`slack-status-word${tone}`}>{CONNECTION_WORDS[config.connection]}</span>
      {config.connection_detail && <span className="user-settings-hint">{config.connection_detail}</span>}
      {config.connection === "standby" && (
        <button type="button" className="button ghost button-xs" disabled={busy} onClick={() => void takeOver()}>
          Handle Slack here instead
        </button>
      )}
      {error && <span className="error settings-field-error">{error}</span>}
    </div>
  );
}

function Group({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="settings-profile-group">
      <h3 className="settings-profile-heading">{title}</h3>
      {children}
    </div>
  );
}

type Saved = { mark?: FieldMark; error?: string };

// The text and number fields start from the saved value and are keyed by it,
// so a save that changes it (the backend sorts and upper-cases IDs) remounts
// them with the new value.

function Switch({
  name,
  label,
  hint,
  checked,
  disabled,
  onToggle,
  mark,
  error,
}: Saved & {
  name: string;
  label: string;
  hint: string;
  checked: boolean;
  disabled?: boolean;
  onToggle: (on: boolean) => void;
}) {
  return (
    <div className="user-settings-switch-row">
      <span className="user-settings-switch-text">
        <span className="user-settings-switch-label">{label}</span>
        <span className="user-settings-hint">{hint}</span>
        <FieldError field={`slack-${name}`} error={error} />
      </span>
      <SaveMark mark={mark} />
      <button
        id={fieldId(`slack-${name}`)}
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label={label}
        aria-describedby={error ? `${fieldId(`slack-${name}`)}-error` : undefined}
        className="user-settings-switch"
        disabled={disabled}
        onClick={() => onToggle(!checked)}
      >
        <span className="user-settings-switch-thumb" aria-hidden="true" />
      </button>
    </div>
  );
}

function TokenField({
  name,
  label,
  placeholder,
  saved,
  onSave,
  hint,
  mark,
  error,
}: Saved & {
  name: string;
  label: string;
  placeholder: string;
  saved: boolean;
  onSave: (value: string) => Promise<boolean>;
  hint?: ReactNode;
}) {
  const [value, setValue] = useState("");
  const id = fieldId(`slack-${name}`);

  async function commit(next = value) {
    if (!next.trim()) return;
    if (await onSave(next.trim())) setValue("");
  }

  return (
    <div className="project-modal-label">
      <label htmlFor={id}>{label}</label>
      <span className="settings-input-wrap" data-marked={mark ? true : undefined}>
        <input
          id={id}
          className="input"
          type="password"
          autoComplete="off"
          spellCheck={false}
          placeholder={saved ? "Saved. Paste a new one to replace it" : placeholder}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onPaste={(e) => {
            const pasted = e.clipboardData.getData("text");
            e.preventDefault();
            setValue(pasted);
            void commit(pasted);
          }}
          onBlur={() => void commit()}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? `${id}-error` : undefined}
        />
        <SaveMark mark={mark} />
      </span>
      <FieldError field={`slack-${name}`} error={error} />
      <span className="user-settings-hint">
        {saved ? "Saved in the system keychain; it is never shown again." : "Not set."} {hint}
      </span>
    </div>
  );
}

function ListField({
  name,
  label,
  placeholder,
  values,
  hint,
  onSave,
  mark,
  error,
}: Saved & {
  name: string;
  label: string;
  placeholder: string;
  values: string[];
  hint: string;
  onSave: (values: string[]) => Promise<boolean>;
}) {
  const joined = values.join(", ");
  const [text, setText] = useState(joined);
  const id = fieldId(`slack-${name}`);

  return (
    <div className="project-modal-label">
      <label htmlFor={id}>{label}</label>
      <span className="settings-input-wrap" data-marked={mark ? true : undefined}>
        <input
          id={id}
          className="input"
          spellCheck={false}
          placeholder={placeholder}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onBlur={() => {
            if (text === joined) return;
            void onSave(text.split(/[\s,]+/).filter(Boolean));
          }}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? `${id}-error` : undefined}
        />
        <SaveMark mark={mark} />
      </span>
      <FieldError field={`slack-${name}`} error={error} />
      <span className="user-settings-hint">{hint} Separate several with commas.</span>
    </div>
  );
}

function NumberField({
  name,
  label,
  value,
  min,
  onSave,
  mark,
  error,
}: Saved & {
  name: string;
  label: string;
  value: number;
  min: number;
  onSave: (n: number) => Promise<boolean>;
}) {
  const [text, setText] = useState(String(value));
  const id = fieldId(`slack-${name}`);

  return (
    <div className="project-modal-label">
      <label htmlFor={id}>{label}</label>
      <span className="settings-input-wrap" data-marked={mark ? true : undefined}>
        <input
          id={id}
          className="input"
          type="number"
          inputMode="numeric"
          min={min}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onBlur={() => {
            const n = Number(text);
            if (!Number.isInteger(n) || n < min) {
              setText(String(value));
              return;
            }
            if (n !== value) void onSave(n);
          }}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? `${id}-error` : undefined}
        />
        <SaveMark mark={mark} />
      </span>
      <FieldError field={`slack-${name}`} error={error} />
    </div>
  );
}

/**
 * The Slack accounts linked to you. Linking starts in Slack: DM VISTA "link",
 * and enter the code it replies with here, within 10 minutes.
 */
function LinkedAccounts() {
  const { projects } = useProjects();
  const [links, setLinks] = useState<SlackLink[] | null>(null);
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchSlackLinks().then(setLinks, (e) => setError(e instanceof Error ? e.message : "Couldn't load links."));
  }, []);

  async function run(action: () => Promise<void>) {
    setError(null);
    try {
      await action();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't save.");
    }
  }

  const replace = (link: SlackLink) =>
    setLinks((all) => (all ?? []).map((l) => (l.id === link.id ? link : l)));

  return (
    <div className="project-modal-label">
      <span>Your linked Slack accounts</span>
      {links?.length === 0 && <span className="user-settings-hint">None yet.</span>}
      {links?.map((link) => (
        <div key={link.id} className="slack-link">
          <span className="slack-link-who">
            {link.slack_user_id} <span className="user-settings-hint">in {link.team_id}</span>
          </span>
          <label className="user-settings-hint">
            New conversations in{" "}
            <select
              className="input slack-link-project"
              value={link.project_id ?? ""}
              onChange={(e) =>
                void run(async () => replace(await updateSlackLink(link.id, { project_id: e.target.value || null })))
              }
            >
              <option value="">First project</option>
              {projects.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </label>
          <label className="user-settings-hint slack-link-check">
            <input
              type="checkbox"
              checked={link.notify_in_thread}
              onChange={(e) =>
                void run(async () => replace(await updateSlackLink(link.id, { notify_in_thread: e.target.checked })))
              }
            />{" "}
            Job notices in the job&rsquo;s thread, not a DM
          </label>
          <button
            type="button"
            className="button ghost button-xs"
            onClick={() =>
              void run(async () => {
                await unlinkSlack(link.id);
                setLinks((all) => (all ?? []).filter((l) => l.id !== link.id));
              })
            }
          >
            Unlink
          </button>
        </div>
      ))}
      <form
        className="slack-link-form"
        onSubmit={(e) => {
          e.preventDefault();
          if (!code.trim()) return;
          void run(async () => {
            const link = await linkSlack(code.trim());
            setLinks((all) => [...(all ?? []), link]);
            setCode("");
          });
        }}
      >
        <input
          className="input"
          aria-label="Link code from Slack"
          placeholder="Link code"
          autoComplete="off"
          spellCheck={false}
          value={code}
          onChange={(e) => setCode(e.target.value)}
        />
        <button type="submit" className="button button-xs">
          Link
        </button>
      </form>
      <span className="user-settings-hint">
        DM VISTA &ldquo;link&rdquo; in Slack and enter the code it replies with. Codes last 10
        minutes. Only linked accounts can use VISTA; anyone else is politely turned away.
      </span>
      {error && <span className="error settings-field-error">{error}</span>}
    </div>
  );
}
