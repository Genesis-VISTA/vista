"use client";

import { useState } from "react";
import {
  activityLines,
  checkNow,
  checksSentence,
  dataResetSentence,
  fetchActivity,
  progressText,
  startDownload,
  useVersion,
  vistaApp,
  type VersionInfo,
} from "@/lib/version";

/**
 * About's update state and actions (openspec change `in-app-updates`).
 *
 * Update downloads and verifies the release while VISTA keeps running, then
 * Restart to update asks to confirm, listing what a restart interrupts, before
 * the window (`window.vistaApp`) stops VISTA and installs it. VISTA runs only
 * in its window, so there is no other path.
 */
export function UpdateControls({ info }: { info: VersionInfo }) {
  const { checking, error, download } = useVersion();

  if (!info.latest) {
    return (
      <div className="settings-about-update">
        <p className="settings-about-state" role="status">
          {checksSentence(info)}
        </p>
        {error && <p className="error settings-about-error">{error}</p>}
        {info.checks === "on" && (
          <button
            type="button"
            className="button ghost button-xs"
            disabled={checking}
            onClick={() => void checkNow()}
          >
            {checking ? "Checking…" : "Check now"}
          </button>
        )}
      </div>
    );
  }

  const mine = download?.version === info.latest ? download : null;

  return (
    <div className="settings-about-update" data-update-available>
      <p className="settings-about-state" role="status">
        VISTA {info.latest} is available.{" "}
        {info.notes_url && (
          <a href={info.notes_url} target="_blank" rel="noreferrer">
            Release notes
          </a>
        )}
      </p>
      {mine?.state === "ready" ? (
        <RestartToUpdate version={info.latest} dataReset={info.data_reset} />
      ) : mine?.state === "downloading" ? (
        <div className="settings-about-progress">
          <progress
            max={mine.total || undefined}
            value={mine.bytes}
            aria-label={`Downloading VISTA ${info.latest}`}
          />
          <span className="user-settings-hint">
            Downloading and verifying… {progressText(mine)}. You can keep working.
          </span>
        </div>
      ) : (
        <div className="settings-about-actions">
          {mine?.state === "failed" && (
            <p className="error settings-about-error" role="alert">
              {mine.error ?? "The download failed."}
            </p>
          )}
          <button
            type="button"
            className="button secondary button-xs"
            onClick={() => void startDownload(info.latest!)}
          >
            {mine?.state === "failed" ? "Try again" : `Update to ${info.latest}`}
          </button>
        </div>
      )}
    </div>
  );
}

type Confirm =
  | { step: "closed" }
  | { step: "loading" }
  | { step: "open"; lines: string[] | null }
  | { step: "restarting" }
  | { step: "refused"; reason: string };

/** The verified download is ready: confirm, then hand over to the window. */
function RestartToUpdate({ version, dataReset }: { version: string; dataReset: boolean | null }) {
  const [confirm, setConfirm] = useState<Confirm>({ step: "closed" });

  async function open() {
    setConfirm({ step: "loading" });
    try {
      setConfirm({ step: "open", lines: activityLines(await fetchActivity()) });
    } catch {
      // Not knowing what is running is no reason to refuse: say so instead.
      setConfirm({ step: "open", lines: null });
    }
  }

  async function restart() {
    const bridge = vistaApp();
    if (!bridge) {
      // Only `./launch.sh --no-electron` has no window, and a checkout is never offered an update.
      setConfirm({ step: "refused", reason: "Restarting into an update needs VISTA's window." });
      return;
    }
    setConfirm({ step: "restarting" });
    try {
      const answer = await bridge.restartToUpdate(version);
      if (!answer.ok) setConfirm({ step: "refused", reason: answer.reason ?? "The window refused to restart." });
    } catch (e) {
      setConfirm({ step: "refused", reason: e instanceof Error ? e.message : "The window refused to restart." });
    }
  }

  if (confirm.step === "closed" || confirm.step === "refused") {
    return (
      <div className="settings-about-actions">
        {confirm.step === "refused" && (
          <p className="error settings-about-error" role="alert">
            {confirm.reason}
          </p>
        )}
        <span className="user-settings-hint">Downloaded and verified.</span>
        <button type="button" className="button secondary button-xs" onClick={() => void open()}>
          Restart to update
        </button>
      </div>
    );
  }
  if (confirm.step === "loading") {
    return <p className="user-settings-hint">Checking what is running…</p>;
  }
  if (confirm.step === "restarting") {
    return (
      <p className="user-settings-hint" role="status">
        Restarting into VISTA {version}…
      </p>
    );
  }

  const reset = dataResetSentence(dataReset);
  return (
    <div
      className="settings-about-confirm"
      role="alertdialog"
      aria-labelledby="settings-about-confirm-title"
    >
      <h3 id="settings-about-confirm-title" className="settings-profile-heading">
        Restart to update to {version}?
      </h3>
      {confirm.lines === null ? (
        <p className="user-settings-hint">VISTA couldn&apos;t tell what is running. Anything in progress stops.</p>
      ) : confirm.lines.length === 0 ? (
        <p className="user-settings-hint">Nothing is running, so nothing is interrupted.</p>
      ) : (
        <>
          <p className="user-settings-hint">Restarting now interrupts:</p>
          <ul className="settings-about-activity">
            {confirm.lines.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </>
      )}
      {reset && (
        <p className={dataReset ? "settings-about-reset hpc-word--warn" : "user-settings-hint"}>{reset}</p>
      )}
      <div className="skill-editor-actions">
        <button type="button" className="button" onClick={() => setConfirm({ step: "closed" })}>
          Cancel
        </button>
        <button type="button" className="button secondary" onClick={() => void restart()}>
          Restart now
        </button>
      </div>
    </div>
  );
}
