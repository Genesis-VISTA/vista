// @ts-check
// The update status window's plain parts (src/update-status.js, in-app-updates D9).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';

import {
  AFTER_LAST_STEP_MS, copyWindow, dismissStatusWindow, markerPresent, readStatus, SILENT_MS,
  shouldClose, statusView, windowCopyPaths, writeMarker,
} from '../src/update-status.js';

const scratch = () => mkdtempSync(path.join(tmpdir(), 'vista-status-'));

test('the copy goes into the update folder, outside the install, per platform', () => {
  assert.deepEqual(windowCopyPaths({ platform: 'darwin', packageRoot: '/Applications/VISTA', folder: '/h/updates/0.2.6' }), {
    source: '/Applications/VISTA/VISTA.app',
    target: '/h/updates/0.2.6/window/VISTA.app',
    executable: '/h/updates/0.2.6/window/VISTA.app/Contents/MacOS/VISTA',
  });
  assert.deepEqual(windowCopyPaths({ platform: 'linux', packageRoot: '/p', folder: '/h/u' }), {
    source: '/p/app/window', target: '/h/u/window', executable: '/h/u/window/VISTA',
  });
  assert.equal(windowCopyPaths({ platform: 'win32', packageRoot: '/p', folder: '/h/u' }).executable, path.join('/h/u/window', 'VISTA.exe'));
});

test('copyWindow copies the window and returns the executable; a missing window is an error', async () => {
  const root = scratch();
  const packageRoot = path.join(root, 'install');
  mkdirSync(path.join(packageRoot, 'app', 'window', 'resources'), { recursive: true });
  writeFileSync(path.join(packageRoot, 'app', 'window', 'VISTA'), '#!/bin/sh\n');
  chmodSync(path.join(packageRoot, 'app', 'window', 'VISTA'), 0o755);
  writeFileSync(path.join(packageRoot, 'app', 'window', 'resources', 'app.asar'), 'asar');
  const folder = path.join(root, 'updates', '0.2.6');
  mkdirSync(folder, { recursive: true });
  // A leftover from an earlier attempt is replaced.
  mkdirSync(path.join(folder, 'window', 'stale'), { recursive: true });

  const exe = await copyWindow({ platform: 'linux', packageRoot, folder });
  assert.equal(exe, path.join(folder, 'window', 'VISTA'));
  assert.equal(readFileSync(path.join(folder, 'window', 'resources', 'app.asar'), 'utf8'), 'asar');
  assert.equal(existsSync(path.join(folder, 'window', 'stale')), false);

  await assert.rejects(copyWindow({ platform: 'linux', packageRoot: path.join(root, 'nowhere'), folder }), /no window at/);
});

test('on macOS the app is cloned with cp -c', { skip: process.platform !== 'darwin' && 'macOS only' }, async () => {
  const root = scratch();
  const app = path.join(root, 'install', 'VISTA.app', 'Contents', 'MacOS');
  mkdirSync(app, { recursive: true });
  writeFileSync(path.join(app, 'VISTA'), '#!/bin/sh\n');
  const folder = path.join(root, 'updates', '0.2.6');
  mkdirSync(folder, { recursive: true });
  const exe = await copyWindow({ platform: 'darwin', packageRoot: path.join(root, 'install'), folder });
  assert.equal(exe, path.join(folder, 'window', 'VISTA.app', 'Contents', 'MacOS', 'VISTA'));
  assert.ok(existsSync(exe));
});

test('readStatus takes only a known step, a version-shaped version and a short detail', async () => {
  const folder = scratch();
  assert.equal(await readStatus(folder), null, 'none written yet');
  writeFileSync(path.join(folder, 'status.json'), '{"step": "installing", "version": "0.2.6", "detail": "unpacking\\ninto  C:\\\\x"}');
  assert.deepEqual(await readStatus(folder), { version: '0.2.6', step: 'installing', detail: 'unpacking into C:\\x' });
  writeFileSync(path.join(folder, 'status.json'), '{"step": "rm -rf", "version": "0.2.6"}');
  assert.equal(await readStatus(folder), null);
  writeFileSync(path.join(folder, 'status.json'), '{"step": "waiting", "version": "<b>x</b>"}');
  assert.equal((await readStatus(folder))?.version, '');
  writeFileSync(path.join(folder, 'status.json'), '{"step": "wai');
  assert.equal(await readStatus(folder), null, 'half a file');
});

test('each step reads as a sentence', () => {
  assert.deepEqual(statusView(null), { title: 'Updating VISTA', line: 'Starting the update…', failed: false });
  assert.equal(statusView({ version: '0.2.6', step: 'waiting', detail: '' }).line, 'Waiting for VISTA to close…');
  assert.deepEqual(statusView({ version: '0.2.6', step: 'installing', detail: 'unpacking into /x' }),
    { title: 'Installing VISTA 0.2.6', line: 'Unpacking into /x', failed: false });
  assert.equal(statusView({ version: '0.2.6', step: 'opening', detail: '' }).line, 'Opening VISTA 0.2.6…');
  const failed = statusView({ version: '0.2.6', step: 'failed', detail: 'no space' });
  assert.equal(failed.title, 'The update to 0.2.6 failed');
  assert.equal(failed.failed, true);
});

test('it closes when the next VISTA shows, two minutes after the last step, or when the helper goes quiet', () => {
  const at = 1_000_000;
  assert.equal(shouldClose({ changedAt: at, endedAt: null, now: at + 1000, dismissed: false }), null);
  assert.match(String(shouldClose({ changedAt: at, endedAt: null, now: at, dismissed: true })), /next VISTA/);
  assert.equal(shouldClose({ changedAt: at, endedAt: at, now: at + AFTER_LAST_STEP_MS - 1, dismissed: false }), null);
  assert.match(String(shouldClose({ changedAt: at, endedAt: at, now: at + AFTER_LAST_STEP_MS, dismissed: false })), /no VISTA took over/);
  // An installer step can take minutes; only a long silence ends it.
  assert.equal(shouldClose({ changedAt: at, endedAt: null, now: at + 10 * 60_000, dismissed: false }), null);
  assert.match(String(shouldClose({ changedAt: at, endedAt: null, now: at + SILENT_MS, dismissed: false })), /stopped writing/);
});

test('the marker is how the next VISTA closes it', () => {
  const cacheRoot = scratch();
  assert.equal(dismissStatusWindow(cacheRoot), false, 'nothing to close');
  writeMarker(cacheRoot, { pid: 42, folder: '/x' });
  assert.equal(markerPresent(cacheRoot), true);
  assert.equal(dismissStatusWindow(cacheRoot), true);
  assert.equal(markerPresent(cacheRoot), false);
});
