// @ts-check
//
// The update helpers (update/apply-update.sh and .ps1), hermetically: a fake
// install folder, a fake installer that succeeds or fails, a fake "open" that
// records what it would open, and a process that keeps running from the install
// folder. The PowerShell twin runs where pwsh is installed.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import { chmodSync, constants, copyFileSync, existsSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const HELPERS = path.join(HERE, '..', 'update');
const VERSION = '0.2.5';

const hasPwsh = spawnSync('pwsh', ['-NoProfile', '-Command', 'exit 0']).status === 0;

/**
 * @param {'sh' | 'ps1'} kind
 * @param {{ installer: 'succeeds' | 'fails' }} options
 */
function world(kind, { installer }) {
  // Resolved: macOS's /var is /private/var, and a process's path is reported resolved.
  const root = realpathSync(mkdtempSync(path.join(tmpdir(), 'vista-apply-')));
  const install = path.join(root, 'install', 'VISTA');
  const home = path.join(root, 'home');
  const cache = path.join(home, 'updates', VERSION);
  mkdirSync(install, { recursive: true });
  mkdirSync(cache, { recursive: true });
  writeFileSync(path.join(install, 'VERSION'), '0.2.3\n');
  const opened = path.join(root, 'opened.txt');
  const invoked = path.join(root, 'installer-args.txt');

  if (kind === 'sh') {
    writeFileSync(path.join(cache, 'install.sh'), installer === 'succeeds'
      ? `#!/bin/bash\nprintf '%s|%s|%s\\n' "$*" "$VISTA_INSTALL_BASE_URL" "$VISTA_INSTALL_DIR" > '${invoked}'\necho '==> installed VISTA ${VERSION}'\necho '==> for diagnostics, run: vista'\necho ${VERSION} > "$VISTA_INSTALL_DIR/VERSION"\n`
      : `#!/bin/bash\necho '==> downloading'\necho 'error: VISTA needs about 7 GB free in /x, and there is 2 GB.' >&2\nexit 1\n`);
    const open = path.join(root, 'open.sh');
    writeFileSync(open, `#!/bin/bash\necho "$1 $(cat "$1/VERSION")" >> '${opened}'\n`);
    chmodSync(open, 0o755);
    return { root, install, home, cache, opened, invoked, open, installerPath: path.join(cache, 'install.sh') };
  }
  writeFileSync(path.join(cache, 'install.ps1'), installer === 'succeeds'
    ? `param([string]$Version, [switch]$NoLaunch)\n"$Version|$NoLaunch|$env:VISTA_INSTALL_BASE_URL|$env:VISTA_INSTALL_DIR" | Set-Content '${invoked}'\nWrite-Host '==> installed VISTA ${VERSION}'\nWrite-Host '==> for diagnostics, run: vista.cmd'\nSet-Content (Join-Path $env:VISTA_INSTALL_DIR 'VERSION') '${VERSION}'\n`
    : `param([string]$Version, [switch]$NoLaunch)\nWrite-Host 'error: VISTA was not installed: VISTA is running from C:\\x. Close VISTA, then run this again.'\nexit 1\n`);
  const open = path.join(root, 'open.ps1');
  writeFileSync(open, `param([string]$Dir)\n"$Dir $((Get-Content (Join-Path $Dir 'VERSION')).Trim())" | Add-Content '${opened}'\n`);
  return { root, install, home, cache, opened, invoked, open, installerPath: path.join(cache, 'install.ps1') };
}

/**
 * @param {'sh' | 'ps1'} kind
 * @param {ReturnType<typeof world>} w
 * @param {Record<string, string>} [extra]
 */
function runHelper(kind, w, extra = {}) {
  const env = {
    ...process.env,
    VISTA_UPDATE_INSTALL_DIR: w.install,
    VISTA_UPDATE_VERSION: VERSION,
    VISTA_UPDATE_CACHE: w.cache,
    VISTA_UPDATE_INSTALLER: w.installerPath,
    VISTA_HOME: w.home,
    VISTA_UPDATE_OPEN: w.open,
    VISTA_UPDATE_WAIT_SECONDS: '3',
    ...extra,
  };
  const [command, args] = kind === 'sh'
    ? ['/bin/bash', [path.join(HELPERS, 'apply-update.sh')]]
    : ['pwsh', ['-NoProfile', '-NonInteractive', '-File', path.join(HELPERS, 'apply-update.ps1')]];
  return spawnSync(command, args, { env, encoding: 'utf8', timeout: 60_000 });
}

/** @param {string} file */
const read = (file) => (existsSync(file) ? readFileSync(file, 'utf8') : '');

for (const kind of /** @type {const} */ (['sh', 'ps1'])) {
  const skip = kind === 'ps1' && !hasPwsh ? 'pwsh is not installed' : false;

  test(`${kind}: a successful install opens the new version and leaves no failure`, { skip }, () => {
    const w = world(kind, { installer: 'succeeds' });
    const result = runHelper(kind, w);
    assert.equal(result.status, 0, read(path.join(w.home, 'logs', 'update.log')));
    if (kind === 'sh') {
      assert.equal(read(w.invoked).trim(), `--version ${VERSION} --no-launch|file://${w.cache}|${w.install}`);
    } else {
      assert.equal(read(w.invoked).trim(), `${VERSION}|True|${w.cache}|${w.install}`);
    }
    assert.equal(read(w.opened).trim(), `${w.install} ${VERSION}`);
    assert.equal(existsSync(path.join(w.home, 'updates', 'last-failure.json')), false);
    assert.match(read(path.join(w.home, 'logs', 'update.log')), /installed VISTA 0\.2\.5; opening it/);
  });

  test(`${kind}: a failed install records why and opens the version still installed`, { skip }, () => {
    const w = world(kind, { installer: 'fails' });
    const result = runHelper(kind, w);
    assert.equal(result.status, 1);
    const failure = JSON.parse(read(path.join(w.home, 'updates', 'last-failure.json')));
    assert.equal(failure.version, VERSION);
    assert.equal(failure.log, 'update.log');
    assert.match(failure.reason, kind === 'sh' ? /^VISTA needs about 7 GB free/ : /^VISTA is running from/);
    assert.equal(read(w.opened).trim(), `${w.install} 0.2.3`);
  });

  test(`${kind}: the status window is told each step, ending with opening`, { skip }, () => {
    const w = world(kind, { installer: 'succeeds' });
    const result = runHelper(kind, w);
    assert.equal(result.status, 0);
    const log = read(path.join(w.home, 'logs', 'update.log'));
    const steps = [...log.matchAll(/apply-update: status: (.+)$/gm)].map((m) => m[1]);
    assert.deepEqual(steps, ['waiting', 'installing (starting the installer)', `installing (installed VISTA ${VERSION})`, 'opening']);
    assert.deepEqual(JSON.parse(read(path.join(w.cache, 'status.json'))), { version: VERSION, step: 'opening', detail: '' });
    assert.match(log, /no update status window/);
  });

  test(`${kind}: a failure ends the status as failed, with the reason`, { skip }, () => {
    const w = world(kind, { installer: 'fails' });
    runHelper(kind, w);
    const status = JSON.parse(read(path.join(w.cache, 'status.json')));
    assert.equal(status.step, 'failed');
    assert.match(status.detail, kind === 'sh' ? /^VISTA needs about 7 GB free/ : /^VISTA is running from/);
  });

  test(`${kind}: the status window is started from the update's folder and does not hold up the install`, { skip }, async () => {
    const w = world(kind, { installer: 'succeeds' });
    // A stand-in for the copied window: it records how it was started, then
    // keeps running from the update's folder, as the real one does.
    const windowDir = path.join(w.cache, 'window');
    mkdirSync(windowDir, { recursive: true });
    const started = path.join(w.root, 'status-window-args.txt');
    const stand = path.join(windowDir, 'VISTA');
    writeFileSync(stand, `#!/bin/bash\nprintf '%s\\n' "$@" > '${started}'\nexec sleep 20 < /dev/null > /dev/null 2>&1\n`);
    chmodSync(stand, 0o755);
    try {
      const result = runHelper(kind, w, { VISTA_UPDATE_STATUS_WINDOW: stand, VISTA_UPDATE_WAIT_SECONDS: '5' });
      assert.equal(result.status, 0, read(path.join(w.home, 'logs', 'update.log')));
      await new Promise((resolve) => setTimeout(resolve, 300));
      assert.equal(read(started).trim(), `--update-status=${w.cache}`);
      assert.equal(read(w.opened).trim(), `${w.install} ${VERSION}`);
      assert.match(read(path.join(w.home, 'logs', 'update.log')), /started the update status window/);
    } finally {
      spawnSync('pkill', ['-f', stand]);
    }
  });

  test(`${kind}: VISTA that never closes is not installed over`, { skip }, async () => {
    const w = world(kind, { installer: 'succeeds' });
    // A process run from the install folder, as VISTA's services are: its
    // command line (pgrep -f) and its executable's path (Get-Process) are there.
    // A copy of a system binary is killed on macOS, so the copy is this node:
    // a clone where APFS has them, so it costs nothing.
    const lingering = path.join(w.install, path.basename(process.execPath));
    copyFileSync(process.execPath, lingering, constants.COPYFILE_FICLONE);
    chmodSync(lingering, 0o755);
    const child = spawn(lingering, ['-e', 'setTimeout(() => {}, 30000)'], { stdio: 'ignore' });
    try {
      await new Promise((resolve) => setTimeout(resolve, 500));
      const result = runHelper(kind, w, { VISTA_UPDATE_WAIT_SECONDS: '2' });
      assert.equal(result.status, 1);
      const failure = JSON.parse(read(path.join(w.home, 'updates', 'last-failure.json')));
      assert.match(failure.reason, /did not finish closing within 2 seconds/);
      assert.equal(read(w.invoked), '', 'the installer must not have run');
      assert.equal(read(w.opened).trim(), `${w.install} 0.2.3`);
    } finally {
      child.kill('SIGKILL');
    }
  });
}
