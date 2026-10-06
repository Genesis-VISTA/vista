// @ts-check
import { test, expect, _electron as electron } from '@playwright/test';
import { readFileSync, mkdtempSync, rmSync } from 'node:fs';
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

/** @param {string} scenario */
async function launchStartup(scenario) {
  const directory = mkdtempSync(path.join(tmpdir(), 'vista-startup-'));
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
