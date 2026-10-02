// @ts-check

const bridge = /** @type {any} */ (globalThis).vistaStartup;
const message = document.querySelector('#startup-message');
const title = document.querySelector('#startup-title');
const failure = /** @type {HTMLElement | null} */ (document.querySelector('#failure'));
const failureMessage = document.querySelector('#failure-message');
const firstRunNote = /** @type {HTMLElement | null} */ (document.querySelector('#first-run-note'));
const retry = /** @type {HTMLButtonElement | null} */ (document.querySelector('#retry'));

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
  if (retry) retry.disabled = snapshot.canRetry !== true;
}

bridge?.onState(render);
document.querySelector('#retry')?.addEventListener('click', () => bridge?.retry());
document.querySelector('#open-logs')?.addEventListener('click', () => bridge?.openLogs());
document.querySelector('#quit')?.addEventListener('click', () => bridge?.quit());
document.querySelector('#copy-diagnostics')?.addEventListener('click', async (event) => {
  const button = /** @type {HTMLButtonElement} */ (event.currentTarget);
  await bridge?.copyDiagnostics();
  const previous = button.textContent;
  button.textContent = 'Copied';
  setTimeout(() => { button.textContent = previous; }, 1200);
});
