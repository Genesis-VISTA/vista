// @ts-check

const bridge = /** @type {any} */ (globalThis).vistaStartup;
const message = document.querySelector('#startup-message');
const title = document.querySelector('#startup-title');
const failure = /** @type {HTMLElement | null} */ (document.querySelector('#failure'));
const failureMessage = document.querySelector('#failure-message');
const firstRunNote = /** @type {HTMLElement | null} */ (document.querySelector('#first-run-note'));
const retry = /** @type {HTMLButtonElement | null} */ (document.querySelector('#retry'));
const backupAndReset = /** @type {HTMLButtonElement | null} */ (document.querySelector('#backup-and-reset'));
const sandboxNotice = /** @type {HTMLElement | null} */ (document.querySelector('#sandbox-notice'));

/** @param {unknown} value */
function text(value) {
  return typeof value === 'string' ? value : '';
}

/** @param {any} snapshot */
function render(snapshot) {
  if (!snapshot || !Array.isArray(snapshot.activities)) return;
  for (const activity of snapshot.activities) {
    const item = /** @type {HTMLElement | null} */ (
      document.querySelector(`[data-phase="${CSS.escape(text(activity.phase))}"]`)
    );
    if (!item) continue;
    item.dataset.state = text(activity.state) || 'pending';
    const label = item.querySelector('span:last-child');
    if (label && activity.label) {
      label.textContent = `${text(activity.label)}${activity.skipped ? ' — already prepared' : ''}`;
    }
  }

  if (title) title.textContent = text(snapshot.title) || 'Preparing your workspace';
  if (message) message.textContent = text(snapshot.message);
  if (firstRunNote) firstRunNote.hidden = snapshot.status === 'failed';
  if (failure) failure.hidden = snapshot.status !== 'failed';
  if (failureMessage) failureMessage.textContent = text(snapshot.failureMessage);
  const actions = Array.isArray(snapshot.failureActions) ? snapshot.failureActions : [];
  if (retry) {
    retry.hidden = !actions.includes('retry');
    retry.disabled = snapshot.canRetry !== true;
  }
  if (backupAndReset) {
    backupAndReset.hidden = !actions.includes('backup-and-reset');
    backupAndReset.disabled = snapshot.canRetry !== true;
  }
  if (sandboxNotice) {
    sandboxNotice.textContent = text(snapshot.notice);
    sandboxNotice.hidden = !sandboxNotice.textContent;
  }
}

bridge?.onState(render);
document.querySelector('#retry')?.addEventListener('click', () => bridge?.retry());
document.querySelector('#backup-and-reset')?.addEventListener('click', async (event) => {
  // Until the next state update, so a second click cannot start a second reset.
  const button = /** @type {HTMLButtonElement} */ (event.currentTarget);
  button.disabled = true;
  try {
    const started = await bridge?.resetData();
    // Cancelled or failed: nothing changes state, so offer it again.
    if (!started) button.disabled = false;
  } catch {
    button.disabled = false;
  }
});
document.querySelector('#open-logs')?.addEventListener('click', () => bridge?.openLogs());
document.querySelector('#quit')?.addEventListener('click', () => bridge?.quit());
document.querySelector('#copy-diagnostics')?.addEventListener('click', async (event) => {
  const button = /** @type {HTMLButtonElement} */ (event.currentTarget);
  await bridge?.copyDiagnostics();
  const previous = button.textContent;
  button.textContent = 'Copied';
  setTimeout(() => { button.textContent = previous; }, 1200);
});
