// @ts-check
import { test, expect, _electron as electron } from '@playwright/test';
import { existsSync, mkdirSync, readFileSync, mkdtempSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn } from 'node:child_process';

import electronBinary from 'electron';

import { startFixtureServer } from './fixture-server.js';

const APP_DIR = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const FAKE_LAUNCHER = fileURLToPath(new URL('./fake-launcher.js', import.meta.url));
const AS_ROOT = process.getuid?.() === 0;
const ROOT_ARGS = AS_ROOT ? ['--no-sandbox'] : [];

/** @type {import('node:http').Server} */
let server;
/** @type {string} */
let origin;

test.beforeAll(async () => {
  ({ server, origin } = await startFixtureServer());
});

test.afterAll(() => server.close());

/**
 * @param {string} scenario
 * @param {{ extraArgs?: string[], env?: Record<string, string>, prepare?: (state: string) => void }} [options]
 */
async function launchStartup(scenario, { extraArgs = [], env = {}, prepare } = {}) {
  const directory = mkdtempSync(path.join(tmpdir(), 'vista-startup-'));
  prepare?.(path.join(directory, 'state'));
  const profile = path.join(directory, 'profile');
  const marker = path.join(directory, 'stopped');
  const count = path.join(directory, 'starts');
  const attempt = path.join(directory, 'attempt');
  const args = [
    APP_DIR,
    '--startup',
    `--launcher=${FAKE_LAUNCHER}`,
    `--user-data-dir=${profile}`,
    ...ROOT_ARGS,
    ...extraArgs,
  ];
  const application = await electron.launch({
    args,
    env: {
      ...process.env,
      VISTA_FAKE_SCENARIO: scenario,
      VISTA_FAKE_UI_URL: `${origin}/`,
      VISTA_FAKE_STOP_MARKER: marker,
      VISTA_FAKE_START_COUNT: count,
      VISTA_FAKE_ATTEMPT_FILE: attempt,
      VISTA_HOME: path.join(directory, 'state'),
      ...env,
    },
  });
  const startup = await application.firstWindow();
  return { application, startup, directory, profile, marker, count, attempt, args };
}

/** @param {import('@playwright/test').ElectronApplication} application */
async function mainWindow(application) {
  await expect.poll(() => application.windows().some((page) => page.url() === `${origin}/`)).toBe(true);
  const main = application.windows().find((page) => page.url() === `${origin}/`);
  if (!main) throw new Error('main VISTA window did not open');
  return main;
}

test('first run transitions to the main renderer without exposing startup IPC', async () => {
  const fixture = await launchStartup('success');
  await expect(fixture.startup.locator('#startup-title')).toContainText('Preparing');
  await expect(fixture.startup.locator('[data-phase="resources"]')).toHaveAttribute('data-state', 'running');
  // The page shows VISTA's own icon, the one the Dock and the app menu show.
  await expect.poll(() => fixture.startup.locator('.app-mark')
    .evaluate((/** @type {HTMLImageElement} */ img) => img.naturalWidth)).toBe(1024);
  const main = await mainWindow(fixture.application);
  await expect(main).toHaveTitle('VISTA fixture');
  const bounds = await fixture.application.evaluate(({ BrowserWindow }, targetUrl) => {
    const window = BrowserWindow.getAllWindows().find(candidate => candidate.webContents.getURL() === targetUrl);
    return window?.getBounds();
  }, `${origin}/`);
  expect(bounds).toMatchObject({ width: 1280, height: 860 });
  expect(await main.evaluate(() => typeof /** @type {any} */ (globalThis).vistaStartup)).toBe('undefined');
  await expect.poll(() => fixture.application.windows().length).toBe(1);
  await fixture.application.close();
  expect(readFileSync(fixture.marker, 'utf8')).toBe('stopped\n');
  rmSync(fixture.directory, { recursive: true, force: true });
});

test('subsequent startup visibly marks idempotent work as already prepared', async () => {
  const fixture = await launchStartup('skipped');
  await expect(fixture.startup.locator('[data-phase="resources"]')).toContainText('already prepared');
  await mainWindow(fixture.application);
  await fixture.application.close();
  rmSync(fixture.directory, { recursive: true, force: true });
});

test('failure enables retry only after cleanup, then transitions normally', async () => {
  const fixture = await launchStartup('retry');
  await expect(fixture.startup.locator('#failure')).toBeVisible();
  await expect(fixture.startup.locator('#retry')).toBeEnabled();
  await fixture.startup.locator('#retry').click();
  await mainWindow(fixture.application);
  expect(readFileSync(fixture.count, 'utf8').trim().split('\n')).toHaveLength(2);
  await fixture.application.close();
  rmSync(fixture.directory, { recursive: true, force: true });
});

test('incompatible data offers Back up and reset, which moves the data and starts again', async () => {
  const fixture = await launchStartup('incompatible');
  await expect(fixture.startup.locator('#failure')).toBeVisible();
  await expect(fixture.startup.locator('#failure-message')).toHaveText(
    'Your local data is incompatible with this version of VISTA, please reset.');
  await expect(fixture.startup.locator('#backup-and-reset')).toBeVisible();
  await expect(fixture.startup.locator('#backup-and-reset')).toBeEnabled();
  await expect(fixture.startup.locator('#retry')).toBeHidden();
  await expect(fixture.startup.locator('#open-logs')).toBeVisible();
  await expect(fixture.startup.locator('#quit')).toBeVisible();

  const state = path.join(fixture.directory, 'state');
  mkdirSync(path.join(state, 'volumes', 'p-1'), { recursive: true });
  writeFileSync(path.join(state, 'vista.db'), 'old', 'utf8');
  mkdirSync(path.join(state, 'huggingface'), { recursive: true });

  // Answer the two dialogs as a researcher would: confirm, then Continue.
  await fixture.application.evaluate(({ dialog }) => {
    const answers = [1, 0, 1]; // Cancel first, then confirm and Continue
    /** @type {any[]} */
    const shown = [];
    /** @type {any} */ (globalThis).shownDialogs = shown;
    dialog.showMessageBox = /** @type {any} */ (async (/** @type {any} */ _window, /** @type {any} */ options) => {
      shown.push(options);
      return { response: answers.shift() ?? 1 };
    });
  });
  await fixture.startup.locator('#backup-and-reset').click();
  await expect.poll(() => fixture.application.evaluate(() => /** @type {any} */ (globalThis).shownDialogs.length)).toBe(1);
  // Cancelled: nothing moved, the failure still shows.
  expect(existsSync(path.join(state, 'vista.db'))).toBe(true);
  expect(existsSync(path.join(state, 'backups'))).toBe(false);
  await expect(fixture.startup.locator('#backup-and-reset')).toBeVisible();
  await fixture.startup.locator('#backup-and-reset').click();
  await mainWindow(fixture.application);

  const shown = await fixture.application.evaluate(() => /** @type {any} */ (globalThis).shownDialogs);
  expect(shown).toHaveLength(3);
  expect(shown[0]).toMatchObject({ buttons: ['Back up and reset', 'Cancel'], defaultId: 1 });
  const [backup] = readdirSync(path.join(state, 'backups'));
  expect(shown[2].message).toContain(path.join(state, 'backups', backup));
  expect(existsSync(path.join(state, 'vista.db'))).toBe(false);
  expect(existsSync(path.join(state, 'backups', backup, 'vista.db'))).toBe(true);
  expect(existsSync(path.join(state, 'backups', backup, 'README.txt'))).toBe(true);
  expect(existsSync(path.join(state, 'huggingface'))).toBe(true);
  expect(readFileSync(fixture.count, 'utf8').trim().split('\n')).toHaveLength(2);
  await fixture.application.close();
  rmSync(fixture.directory, { recursive: true, force: true });
});

test('closing during startup stops the supervised launcher', async () => {
  const fixture = await launchStartup('hold');
  await expect(fixture.startup.locator('[data-phase="ui"]')).toHaveAttribute('data-state', 'running');
  const exited = new Promise((resolve) => fixture.application.process().once('exit', resolve));
  await fixture.startup.close();
  await exited;
  expect(readFileSync(fixture.marker, 'utf8')).toBe('stopped\n');
  rmSync(fixture.directory, { recursive: true, force: true });
});

test('second launches during startup and after transition start no second stack', async () => {
  const fixture = await launchStartup('hold');
  await expect(fixture.startup.locator('[data-phase="ui"]')).toHaveAttribute('data-state', 'running');

  /** @param {Awaited<ReturnType<typeof launchStartup>>} target */
  const runSecond = (target) => new Promise((resolve) => {
    const child = spawn(/** @type {string} */ (/** @type {unknown} */ (electronBinary)), target.args, {
      env: {
        ...process.env,
        VISTA_FAKE_SCENARIO: 'hold',
        VISTA_FAKE_UI_URL: `${origin}/`,
        VISTA_FAKE_START_COUNT: target.count,
      },
    });
    child.once('exit', resolve);
  });
  await runSecond(fixture);
  expect(readFileSync(fixture.count, 'utf8').trim().split('\n')).toHaveLength(1);
  await fixture.application.close();
  rmSync(fixture.directory, { recursive: true, force: true });

  const ready = await launchStartup('success');
  await mainWindow(ready.application);
  await runSecond(ready);
  expect(readFileSync(ready.count, 'utf8').trim().split('\n')).toHaveLength(1);
  await ready.application.close();
  rmSync(ready.directory, { recursive: true, force: true });
});

test('a supervisor failure after transition returns to the startup error window', async () => {
  const fixture = await launchStartup('after-ready-failure');
  await mainWindow(fixture.application);
  /** @type {import('@playwright/test').Page | undefined} */
  let recovery;
  await expect.poll(async () => {
    for (const page of fixture.application.windows()) {
      if (page.url().endsWith('/startup.html') && await page.locator('#failure').isVisible()) {
        recovery = page;
        return true;
      }
    }
    return false;
  }).toBe(true);
  if (!recovery) throw new Error('startup recovery window did not open');
  await expect(recovery.locator('#failure')).toBeVisible();
  await expect(recovery.locator('#retry')).toBeEnabled();
  const exited = new Promise((resolve) => fixture.application.process().once('exit', resolve));
  await recovery.close();
  await exited;
  rmSync(fixture.directory, { recursive: true, force: true });
});

// linux/vista-app passes window-sandbox's reason when it starts the window
// without the sandbox (task 4.11); a sandboxed window ignores it.
test('an unsandboxed window shows why in the startup window, and a sandboxed one does not', async () => {
  const notice = 'The VISTA window is running without Chromium\'s sandbox: a test says so.\nTo turn it on, run:\n  sudo true';
  let fixture = await launchStartup('hold', { extraArgs: ['--no-sandbox'], env: { VISTA_SANDBOX_NOTICE: notice } });
  await expect(fixture.startup.locator('#sandbox-notice')).toBeVisible();
  expect(await fixture.startup.locator('#sandbox-notice').evaluate((element) => element.textContent)).toBe(notice);
  await fixture.application.close();
  // As root every window here runs without the sandbox, so there is no
  // sandboxed one to check.
  if (AS_ROOT) return;
  fixture = await launchStartup('hold', { env: { VISTA_SANDBOX_NOTICE: notice } });
  await expect(fixture.startup.locator('[data-phase="preflight"]')).not.toHaveAttribute('data-state', 'pending');
  await expect(fixture.startup.locator('#sandbox-notice')).toBeHidden();
  await fixture.application.close();
});

test('a failed update is said first, and VISTA opens once the researcher continues', async () => {
  const fixture = await launchStartup('success', {
    prepare: (state) => {
      mkdirSync(path.join(state, 'updates'), { recursive: true });
      writeFileSync(path.join(state, 'updates', 'last-failure.json'), JSON.stringify({
        version: '0.2.5',
        reason: 'VISTA did not finish closing within 60 seconds, so nothing was installed.',
        log: 'update.log',
      }));
    },
  });
  const notice = fixture.startup.locator('#update-failure');
  await expect(notice).toBeVisible();
  await expect(fixture.startup.locator('#update-failure-title')).toHaveText('Update to 0.2.5 failed');
  await expect(fixture.startup.locator('#update-failure-message')).toContainText('nothing was installed');
  await expect(notice.getByRole('button', { name: 'Open Logs' })).toBeVisible();
  await expect(notice.getByRole('button', { name: 'Copy Diagnostics' })).toBeVisible();

  // Ready, but held: the notice stays until Continue.
  const continueButton = notice.getByRole('button', { name: 'Continue to VISTA' });
  await expect(continueButton).toBeVisible();
  const visibleMain = () => fixture.application.evaluate(({ BrowserWindow }, targetUrl) =>
    BrowserWindow.getAllWindows().some((window) => window.webContents.getURL() === targetUrl && window.isVisible()), `${origin}/`);
  await new Promise((resolve) => setTimeout(resolve, 500));
  expect(await visibleMain()).toBe(false);

  await continueButton.click();
  await mainWindow(fixture.application);
  await expect.poll(visibleMain).toBe(true);
  await expect.poll(() => fixture.application.windows().length).toBe(1);
  // Read once: the next start says nothing.
  expect(existsSync(path.join(fixture.directory, 'state', 'updates', 'last-failure.json'))).toBe(false);
  await fixture.application.close();
  rmSync(fixture.directory, { recursive: true, force: true });
});

test('the main window has the update bridge; one not started from a package refuses to restart', async () => {
  const fixture = await launchStartup('success');
  expect(await fixture.startup.evaluate(() => typeof /** @type {any} */ (globalThis).vistaApp)).toBe('undefined');
  const main = await mainWindow(fixture.application);
  expect(await main.evaluate(() => /** @type {any} */ (globalThis).vistaApp?.isVistaApp)).toBe(true);
  const answer = await main.evaluate(() => /** @type {any} */ (globalThis).vistaApp.restartToUpdate('0.2.5'));
  expect(answer).toEqual({ ok: false, reason: 'This VISTA was not installed from a package, so it cannot update itself.' });
  // Nothing stopped.
  await new Promise((resolve) => setTimeout(resolve, 300));
  expect(existsSync(fixture.marker)).toBe(false);
  await fixture.application.close();
  rmSync(fixture.directory, { recursive: true, force: true });
});

// ─── the update status window (in-app-updates D9) ───────────────────────────

// Run in Electron, where `fs` reads a .asar as a folder: a Windows or Linux
// window has resources/app.asar, which the copy must carry as the file it is.
test('the window copy for Windows and Linux carries app.asar whole under Electron', async () => {
  const directory = mkdtempSync(path.join(tmpdir(), 'vista-window-copy-'));
  const window = path.join(directory, 'package', 'app', 'window');
  mkdirSync(path.join(window, 'resources'), { recursive: true });
  const binary = /** @type {string} */ (/** @type {unknown} */ (electronBinary));
  const asars = [path.join(path.dirname(binary), '..', 'Resources'), path.join(path.dirname(binary), 'resources')]
    .map((resources) => path.join(resources, 'default_app.asar'))
    .filter((file) => existsSync(file));
  expect(asars.length).toBeGreaterThan(0);
  writeFileSync(path.join(window, 'resources', 'app.asar'), readFileSync(asars[0]));
  writeFileSync(path.join(window, 'VISTA'), 'window');
  const folder = path.join(directory, 'updates', '0.2.6');
  mkdirSync(folder, { recursive: true });
  const script = path.join(directory, 'copy.mjs');
  const module = new URL('../src/update-status.js', import.meta.url).href;
  writeFileSync(
    script,
    `import { copyWindow } from ${JSON.stringify(module)};\n` +
      `console.log(await copyWindow({ platform: 'linux', packageRoot: ${JSON.stringify(path.join(directory, 'package'))}, folder: ${JSON.stringify(folder)} }));\n`,
  );
  const result = await new Promise((resolve) => {
    const child = spawn(binary, [script], { env: { ...process.env, ELECTRON_RUN_AS_NODE: '1' } });
    let output = '';
    child.stdout.on('data', (/** @type {Buffer} */ chunk) => (output += chunk));
    child.stderr.on('data', (/** @type {Buffer} */ chunk) => (output += chunk));
    child.on('exit', (/** @type {number | null} */ code) => resolve({ code, output }));
  });
  expect(result).toEqual({ code: 0, output: `${path.join(folder, 'window', 'VISTA')}\n` });
  expect(readFileSync(path.join(folder, 'window', 'resources', 'app.asar'))).toEqual(readFileSync(asars[0]));
  rmSync(directory, { recursive: true, force: true });
});

test('the update status window shows each step and closes when its marker goes', async () => {
  const directory = mkdtempSync(path.join(tmpdir(), 'vista-update-status-'));
  const home = path.join(directory, 'home');
  const folder = path.join(home, 'updates', '0.2.6');
  mkdirSync(folder, { recursive: true });
  const status = (/** @type {object} */ body) => writeFileSync(path.join(folder, 'status.json'), JSON.stringify(body));
  status({ version: '0.2.6', step: 'waiting', detail: '' });
  // The helper's marker, written before it starts the window.
  const marker = path.join(home, 'updates', 'status-window.json');
  writeFileSync(marker, JSON.stringify({ folder }));
  // The window finds the marker beside its folder, not in VISTA_HOME: here that
  // names another home, with no marker, and the window must still stay up.
  const application = await electron.launch({
    args: [APP_DIR, `--update-status=${folder}`, ...ROOT_ARGS],
    env: { ...process.env, VISTA_HOME: path.join(directory, 'another-home') },
  });
  const exited = new Promise((resolve) => application.process().once('exit', resolve));
  const page = await application.firstWindow();
  await expect(page.locator('#update-title')).toHaveText('Updating to VISTA 0.2.6');
  await expect(page.locator('#update-line')).toHaveText('Waiting for VISTA to close…');
  // It leaves the marker alone, for the next VISTA to remove.
  expect(existsSync(marker)).toBe(true);
  // It has its own profile, so it holds no lock the next VISTA needs.
  await expect.poll(() => existsSync(path.join(folder, 'window-data'))).toBe(true);

  status({ version: '0.2.6', step: 'installing', detail: 'unpacking into /x' });
  await expect(page.locator('#update-title')).toHaveText('Installing VISTA 0.2.6');
  await expect(page.locator('#update-line')).toHaveText('Unpacking into /x');
  // A read that fails mid-replace (Windows locks the file a moment) keeps the step shown.
  writeFileSync(path.join(folder, 'status.json'), '{"version": "0.2');
  await new Promise((resolve) => setTimeout(resolve, 750));
  await expect(page.locator('#update-title')).toHaveText('Installing VISTA 0.2.6');
  await expect(page.locator('#update-line')).toHaveText('Unpacking into /x');
  status({ version: '0.2.6', step: 'opening', detail: '' });
  await expect(page.locator('#update-line')).toHaveText('Opening VISTA 0.2.6…');
  // No bridge but its own.
  expect(await page.evaluate(() => typeof /** @type {any} */ (globalThis).vistaStartup)).toBe('undefined');

  rmSync(marker);
  await exited;
  rmSync(directory, { recursive: true, force: true });
});

// A failed update reopens VISTA within a second, which can show its startup
// window before the status window has even started.
test('an update status window that starts after VISTA is back closes at once', async () => {
  const directory = mkdtempSync(path.join(tmpdir(), 'vista-update-status-'));
  const home = path.join(directory, 'home');
  const folder = path.join(home, 'updates', '0.2.6');
  mkdirSync(folder, { recursive: true });
  writeFileSync(path.join(folder, 'status.json'), JSON.stringify({ version: '0.2.6', step: 'failed', detail: 'x' }));
  const application = await electron.launch({
    args: [APP_DIR, `--update-status=${folder}`, ...ROOT_ARGS],
    env: { ...process.env, VISTA_HOME: home },
  });
  const started = Date.now();
  await new Promise((resolve) => application.process().once('exit', resolve));
  expect(Date.now() - started).toBeLessThan(10_000);
  expect(existsSync(path.join(home, 'updates', 'status-window.json'))).toBe(false);
  rmSync(directory, { recursive: true, force: true });
});

test('a starting VISTA closes a waiting update status window once its startup window shows', async () => {
  const fixture = await launchStartup('success', {
    prepare: (state) => {
      mkdirSync(path.join(state, 'updates'), { recursive: true });
      writeFileSync(path.join(state, 'updates', 'status-window.json'), '{"pid": 1, "folder": "/x"}\n');
    },
  });
  await expect(fixture.startup.locator('#startup-title')).toBeVisible();
  await expect.poll(() => existsSync(path.join(fixture.directory, 'state', 'updates', 'status-window.json'))).toBe(false);
  await mainWindow(fixture.application);
  await fixture.application.close();
  rmSync(fixture.directory, { recursive: true, force: true });
});
