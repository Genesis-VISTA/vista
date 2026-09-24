// @ts-check
//
// The window's routing, lifecycle and lock-down, driven through Playwright's
// Electron support against test/fixture-server.js (design T2). Needs a display:
// validation lane, not PR CI.
//
//   npx playwright test -c playwright.config.js
import { test, expect, _electron as electron } from '@playwright/test';
import { spawn } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import electronBinary from 'electron';

import { startFixtureServer } from './fixture-server.js';

const APP_DIR = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const EXTERNAL = 'https://example.org';

/** @type {import('node:http').Server} */
let server;
/** @type {string} */
let origin;
/** @type {string} */
let profile;
/** @type {import('@playwright/test').ElectronApplication} */
let app;
/** @type {import('@playwright/test').Page} */
let page;

test.beforeAll(async () => {
  ({ server, origin } = await startFixtureServer());
});

test.afterAll(async () => {
  server.close();
});

test.beforeEach(async () => {
  profile = mkdtempSync(path.join(tmpdir(), 'vista-window-'));
  app = await electron.launch({
    args: [APP_DIR, `--url=${origin}/`, `--user-data-dir=${profile}`],
  });
  // Record instead of opening a real browser, and save downloads without a
  // dialog. Both replace behaviour at the edge of the app, not the routing
  // decision under test.
  await app.evaluate(({ shell, session }, downloadDir) => {
    /** @type {any} */ (globalThis).opened = [];
    shell.openExternal = async (url) => {
      /** @type {any} */ (globalThis).opened.push(url);
    };
    /** @type {any} */ (globalThis).downloaded = [];
    session.defaultSession.on('will-download', (_event, item) => {
      item.setSavePath(`${downloadDir}/${item.getFilename()}`);
      /** @type {any} */ (globalThis).downloaded.push(item.getFilename());
    });
  }, profile);
  page = await app.firstWindow();
  await page.waitForLoadState('domcontentloaded');
});

test.afterEach(async () => {
  await app.close();
  rmSync(profile, { recursive: true, force: true });
});

/** @returns {Promise<string[]>} */
const opened = () => app.evaluate(() => /** @type {any} */ (globalThis).opened);

/** @param {string} url */
async function expectOpenedExternally(url) {
  await expect.poll(opened).toContain(url);
  expect(app.windows()).toHaveLength(1);
  expect(page.url()).toBe(`${origin}/`);
}

test('an external target=_blank link opens in the system browser', async () => {
  await page.click('#external-blank');
  await expectOpenedExternally(`${EXTERNAL}/doi`);
});

test('window.open to another site opens in the system browser', async () => {
  await page.click('#window-open-external');
  await expectOpenedExternally(`${EXTERNAL}/elicitation`);
});

test('navigating the window to another site is refused and opened externally', async () => {
  // Clicked from inside the page: `page.click` waits for the navigation it
  // scheduled to finish, and one cancelled in will-navigate never does.
  await page.evaluate(() => /** @type {HTMLElement} */ (document.querySelector('#external-nav')).click());
  await expectOpenedExternally(`${EXTERNAL}/nav`);
});

test('a same-origin redirect to another site is refused and opened externally', async () => {
  await page.click('#redirect-out');
  await expectOpenedExternally(`${EXTERNAL}/redirected`);
});

test('a file: link does not replace the page', async () => {
  await page.click('#file-nav');
  await page.waitForTimeout(500);
  expect(page.url()).toBe(`${origin}/`);
  expect(await opened()).toEqual([]);
});

test('a same-origin target=_blank link opens a child window', async () => {
  const [child] = await Promise.all([app.waitForEvent('window'), page.click('#same-origin-blank')]);
  await child.waitForLoadState('domcontentloaded');
  expect(await child.title()).toBe('VISTA child');
  expect(await opened()).toEqual([]);
  await child.close();
  expect(app.windows()).toHaveLength(1);
});

test('a same-origin PDF opens in a child window and is viewed, not downloaded', async () => {
  const [child] = await Promise.all([app.waitForEvent('window'), page.click('#pdf-blank')]);
  await child.waitForLoadState('load');
  expect(child.url()).toBe(`${origin}/paper.pdf`);
  expect(await child.evaluate(() => document.contentType)).toBe('application/pdf');
  if (process.env.VISTA_WINDOW_SHOTS) {
    await child.waitForTimeout(1500);
    await child.screenshot({ path: path.join(process.env.VISTA_WINDOW_SHOTS, 'pdf-child.png') });
  }
  expect(await app.evaluate(() => /** @type {any} */ (globalThis).downloaded)).toEqual([]);
  await child.close();
  expect(app.windows()).toHaveLength(1);
});

test('same-origin navigation stays in the window', async () => {
  await page.click('#same-origin-nav');
  await page.waitForURL(`${origin}/child`);
  expect(await opened()).toEqual([]);
});

test('a download is saved, not opened as a page', async () => {
  await page.click('#download');
  await expect
    .poll(() => app.evaluate(() => /** @type {any} */ (globalThis).downloaded))
    .toEqual(['file.txt']);
  expect(app.windows()).toHaveLength(1);
  expect(page.url()).toBe(`${origin}/`);
});

test('the page has what a browser tab has, and no more', async () => {
  const probe = await page.evaluate(async () => ({
    require: typeof (/** @type {any} */ (globalThis).require),
    process: typeof (/** @type {any} */ (globalThis).process),
    notifications: await Notification.requestPermission(),
  }));
  expect(probe).toEqual({ require: 'undefined', process: 'undefined', notifications: 'denied' });
});

test('a second instance exits and leaves the first window in place', async () => {
  const second = spawn(/** @type {string} */ (/** @type {unknown} */ (electronBinary)), [
    APP_DIR,
    `--url=${origin}/`,
    `--user-data-dir=${profile}`,
  ]);
  const code = await new Promise((resolve) => second.on('exit', resolve));
  expect(code).toBe(0);
  expect(app.windows()).toHaveLength(1);
});

/**
 * @param {string} url
 * @returns {Promise<number | null>}
 */
function smokeTest(url) {
  const child = spawn(/** @type {string} */ (/** @type {unknown} */ (electronBinary)), [
    APP_DIR,
    '--smoke-test',
    `--url=${url}`,
  ]);
  return new Promise((resolve) => child.on('exit', resolve));
}

test('--smoke-test exits 0 when the UI loads and 1 when it cannot', async () => {
  expect(await smokeTest(`${origin}/`)).toBe(0);
  // A port nothing listens on: the fixture's own, once closed.
  const { server: spare, origin: closed } = await startFixtureServer();
  await new Promise((resolve) => spare.close(resolve));
  expect(await smokeTest(`${closed}/`)).toBe(1);
});
