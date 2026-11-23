// @ts-check
//
// folder-picker.js: the system folder picker behind `window.vistaApp.pickFolder()`
// (openspec change `linked-folders`, task 7.1), with Electron's `dialog` stubbed.
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { pickFolder } from '../src/folder-picker.js';

const WINDOW = { id: 'main' };

/** @param {{ canceled: boolean, filePaths: string[] }} result */
function stub(result) {
  /** @type {Array<{ window: unknown, options: { properties: string[] } }>} */
  const calls = [];
  return {
    calls,
    dialog: {
      /** @param {unknown} window @param {{ properties: string[] }} options */
      async showOpenDialog(window, options) {
        calls.push({ window, options });
        return result;
      },
    },
  };
}

test('returns the folder picked, from a picker modal to the main window', async () => {
  const { dialog, calls } = stub({ canceled: false, filePaths: ['/Users/ada/Research/salt-data'] });
  const picked = await pickFolder({ dialog, window: WINDOW, fromMainWindow: true, fromMainFrame: true });
  assert.equal(picked, '/Users/ada/Research/salt-data');
  assert.equal(calls.length, 1);
  assert.equal(calls[0].window, WINDOW);
  assert.deepEqual(calls[0].options.properties, ['openDirectory']);
});

test('returns null when the researcher cancels', async () => {
  const { dialog } = stub({ canceled: true, filePaths: [] });
  assert.equal(
    await pickFolder({ dialog, window: WINDOW, fromMainWindow: true, fromMainFrame: true }),
    null,
  );
});

test('returns null for an empty answer', async () => {
  const { dialog } = stub({ canceled: false, filePaths: [] });
  assert.equal(
    await pickFolder({ dialog, window: WINDOW, fromMainWindow: true, fromMainFrame: true }),
    null,
  );
});

test('opens nothing for another window, a child frame, or no window', async () => {
  for (const request of [
    { window: WINDOW, fromMainWindow: false, fromMainFrame: true },
    { window: WINDOW, fromMainWindow: true, fromMainFrame: false },
    { window: null, fromMainWindow: true, fromMainFrame: true },
  ]) {
    const { dialog, calls } = stub({ canceled: false, filePaths: ['/x'] });
    assert.equal(await pickFolder({ dialog, ...request }), null);
    assert.equal(calls.length, 0, JSON.stringify(request));
  }
});
