// @ts-check
// The update status window's page (in-app-updates D9): the main process sends
// what to say; this only shows it.

const bridge = /** @type {any} */ (globalThis).vistaUpdate;
const title = document.querySelector('#update-title');
const line = document.querySelector('#update-line');
const progress = /** @type {HTMLElement | null} */ (document.querySelector('#update-progress'));

/** @param {unknown} value */
const text = (value) => (typeof value === 'string' ? value : '');

bridge?.onStatus((/** @type {any} */ view) => {
  if (!view) return;
  if (title) title.textContent = text(view.title);
  if (line) line.textContent = text(view.line);
  if (progress) progress.dataset.state = view.failed ? 'failed' : 'running';
  document.title = text(view.title) || 'Updating VISTA';
});
