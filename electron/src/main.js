// @ts-check
//
// VISTA's window. It loads the UI address it is given and nothing else: the
// launcher (`vista`, or `./launch.sh --electron` in development) starts the
// services, waits for them, runs this as a child, and stops everything when it
// exits (design W1). Nothing here knows the services exist.
//
//   VISTA --url=http://127.0.0.1:3000          the packaged window
//   electron . --dev --url=http://localhost:3000   development, with DevTools
//   VISTA --smoke-test --url=...               load once, exit 0 or 1 (B3)
//   --user-data-dir=<dir>                      separate profile (tests)

import { app, BrowserWindow, Menu, nativeTheme, session, shell } from 'electron';
import path from 'node:path';

import { classify, originOf } from './routing.js';
import { windowBackground } from './appearance.js';

const SMOKE_TEST_TIMEOUT_MS = 30_000;

/**
 * @param {string[]} argv
 * @returns {{ url: string, dev: boolean, smokeTest: boolean, userDataDir: string | null }}
 */
function parseArgs(argv) {
  /** @param {string} name */
  const value = (name) => {
    const hit = argv.find((a) => a.startsWith(`--${name}=`));
    return hit ? hit.slice(name.length + 3) : null;
  };
  return {
    url: value('url') ?? '',
    dev: argv.includes('--dev'),
    smokeTest: argv.includes('--smoke-test'),
    userDataDir: value('user-data-dir'),
  };
}

const args = parseArgs(process.argv);

/** @param {string} message */
function fail(message) {
  console.error(`vista-window: ${message}`);
  app.exit(2);
}

let origin = '';
try {
  const start = new URL(args.url);
  if (start.protocol !== 'http:' && start.protocol !== 'https:') throw new Error();
  origin = originOf(args.url);
} catch {
  fail(`--url must be the http(s) address of the running UI (got "${args.url}")`);
}

// The single-instance lock is kept per userData directory, and the dev window and
// the installed one are both named VISTA. Without this, a dev window and an open
// installed one would contend for one lock. An explicit --user-data-dir wins.
if (args.dev) app.setPath('userData', path.join(app.getPath('appData'), 'VISTA-dev'));
if (args.userDataDir) app.setPath('userData', path.resolve(args.userDataDir));

// What the window exits with when another VISTA window already holds the lock.
// The launchers read it (EX_TEMPFAIL); 0 means only that the user closed it.
const EXIT_ALREADY_OPEN = 75;

// On Linux the launcher adds --no-sandbox where the host blocks Chromium's
// sandbox (linux-desktop-window D1). This line lands in window.log, so a report
// from the field says which mode the window was in.
const rendererSandboxed = !app.commandLine.hasSwitch('no-sandbox');
console.log(`vista-window: renderer sandbox: ${rendererSandboxed ? 'on' : 'off (--no-sandbox)'}`);

// W4: every window, the main one and any child, gets the same renderer: no
// Node, no preload, isolated and sandboxed, i.e. exactly what a browser tab has.
/** @type {Electron.WebPreferences} */
const webPreferences = {
  contextIsolation: true,
  sandbox: true,
  nodeIntegration: false,
  webviewTag: false,
  devTools: args.dev,
};

// VISTA's icon (assets/, made by scripts/make-icons.js). A packaged window
// already has it in VISTA.exe or VISTA.app; Linux keeps no icon in the
// executable, and a development run is Electron's own binary, so those set it
// here. macOS ignores a window's icon, so development sets the Dock's instead.
const ICON = path.join(app.getAppPath(), 'assets', 'icon.png');
/** @type {Partial<Electron.BrowserWindowConstructorOptions>} */
const windowIcon = process.platform === 'linux' || !app.isPackaged ? { icon: ICON } : {};

/** @type {BrowserWindow | null} */
let mainWindow = null;

// Child windows that have not shown a page yet: the ones a PDF sent to the
// browser would otherwise leave empty (D3).
/** @type {WeakSet<Electron.WebContents>} */
const blankChildren = new WeakSet();

// ─── routing (W3) ───────────────────────────────────────────────────────────

/** @param {string} url */
function openExternally(url) {
  shell.openExternal(url).catch((error) => {
    console.error(`vista-window: could not open ${url} in the browser: ${error}`);
  });
}

// Applied to every WebContents the app ever creates, so child windows (a PDF
// opened from a knowledge base) get the same rules as the main window without
// having to be wired up individually.
app.on('web-contents-created', (_event, contents) => {
  contents.setWindowOpenHandler(({ url }) => {
    switch (classify(origin, url)) {
      case 'in-app':
        return {
          action: 'allow',
          overrideBrowserWindowOptions: { width: 1000, height: 800, ...windowIcon, webPreferences },
        };
      case 'external':
        openExternally(url);
        return { action: 'deny' };
      default:
        return { action: 'deny' };
    }
  });

  /**
   * @param {Electron.Event<{ url: string, isMainFrame: boolean }>} event
   */
  const guardNavigation = (event) => {
    // A sandboxed card's own document has no http address; refusing it would
    // leave the card blank. Only a subframe can be one of these.
    if (!event.isMainFrame && (event.url === 'about:srcdoc' || event.url === 'about:blank')) return;
    const route = classify(origin, event.url);
    if (route === 'in-app') return;
    event.preventDefault();
    if (route === 'external') openExternally(event.url);
  };
  // will-navigate is main-frame only, so a link clicked inside an iframe (a
  // SandboxedHtmlCard, whose sandbox has no popups) would load in the app.
  // will-frame-navigate covers every frame.
  contents.on('will-frame-navigate', guardNavigation);
  // will-redirect fires for subframes too, and a redirect inside a card is not
  // the researcher going anywhere.
  contents.on('will-redirect', (event) => {
    if (event.isMainFrame) guardNavigation(event);
  });

  contents.on('will-attach-webview', (event) => event.preventDefault());

  contents.on('did-create-window', (child) => {
    blankChildren.add(child.webContents);
    child.webContents.once('did-navigate', () => blankChildren.delete(child.webContents));
  });
});

// D3: without the sandbox, PDFs (the largest parser of content VISTA did not
// produce) open in the system browser, inside its sandbox, instead of here.
// Keyed on the response's type, so a PDF reached by any link is covered.
// Answering 204 leaves the page that followed the link where it was.
/** @param {Electron.Session} ses */
function sendPdfsToBrowser(ses) {
  ses.webRequest.onHeadersReceived((details, callback) => {
    const frame = details.resourceType === 'mainFrame' || details.resourceType === 'subFrame';
    if (!frame || !isInlinePdf(details.responseHeaders ?? {})) {
      callback({});
      return;
    }
    openExternally(details.url);
    callback({ statusLine: 'HTTP/1.1 204 No Content', responseHeaders: {} });
    const contents = details.webContents;
    if (contents && blankChildren.has(contents)) BrowserWindow.fromWebContents(contents)?.close();
  });
}

/**
 * A PDF the page would display. One sent as an attachment is a download and
 * stays one.
 * @param {Record<string, string[]>} headers
 */
function isInlinePdf(headers) {
  /** @param {string} name */
  const header = (name) =>
    Object.entries(headers)
      .find(([key]) => key.toLowerCase() === name)?.[1]
      .join(',')
      .toLowerCase() ?? '';
  return header('content-type').startsWith('application/pdf') && !header('content-disposition').startsWith('attachment');
}

// ─── session (W3, W4) ───────────────────────────────────────────────────────

function configureSession() {
  const ses = session.defaultSession;

  // The page gets no camera, microphone, notifications, location, and so on.
  // The UI asks for none of them.
  ses.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  ses.setPermissionCheckHandler(() => false);

  // Electron's default already asks where to save. This only starts that
  // dialog in Downloads, as a browser would.
  ses.on('will-download', (_event, item) => {
    item.setSaveDialogOptions({
      defaultPath: path.join(app.getPath('downloads'), item.getFilename()),
    });
  });

  if (!rendererSandboxed) sendPdfsToBrowser(ses);
}

// ─── menu (W5) ──────────────────────────────────────────────────────────────

// Without an Edit menu, macOS never delivers Cmd-C/V/X/A/Z to the page.
function buildMenu() {
  /** @type {Electron.MenuItemConstructorOptions[]} */
  const view = [{ role: 'reload' }];
  if (args.dev) view.push({ role: 'forceReload' }, { role: 'toggleDevTools' });
  view.push(
    { type: 'separator' },
    { role: 'resetZoom' },
    { role: 'zoomIn' },
    { role: 'zoomOut' },
    { type: 'separator' },
    { role: 'togglefullscreen' },
  );
  /** @type {Electron.MenuItemConstructorOptions[]} */
  const template = [
    process.platform === 'darwin' ? { role: 'appMenu' } : { role: 'fileMenu' },
    { role: 'editMenu' },
    { label: 'View', submenu: view },
    { role: 'windowMenu' },
  ];
  return Menu.buildFromTemplate(template);
}

// ─── windows ────────────────────────────────────────────────────────────────

function createMainWindow() {
  const win = new BrowserWindow({
    width: 1280,
    height: 860,
    minWidth: 720,
    minHeight: 500,
    title: 'VISTA',
    show: false,
    backgroundColor: windowBackground(nativeTheme.shouldUseDarkColors),
    ...windowIcon,
    webPreferences,
  });
  // The OS can change appearance while VISTA is open (macOS Auto at sunset);
  // the page follows through prefers-color-scheme, and the frame behind it
  // has to as well or a resize shows the old ground.
  const onThemeUpdated = () => win.setBackgroundColor(windowBackground(nativeTheme.shouldUseDarkColors));
  nativeTheme.on('updated', onThemeUpdated);
  win.once('ready-to-show', () => win.show());
  win.on('closed', () => {
    nativeTheme.off('updated', onThemeUpdated);
    mainWindow = null;
  });
  win.loadURL(args.url);
  return win;
}

// B3: prove a (relocated, re-signed) window can load the running UI, without
// showing anything. No single-instance lock, so a researcher's open VISTA
// window cannot turn a build's smoke test into a false failure.
function runSmokeTest() {
  const win = new BrowserWindow({ show: false, webPreferences });
  const timer = setTimeout(() => {
    console.error(`vista-window: smoke test timed out after ${SMOKE_TEST_TIMEOUT_MS / 1000}s`);
    app.exit(1);
  }, SMOKE_TEST_TIMEOUT_MS);

  win.webContents.on('did-fail-load', (_event, code, description, url, isMainFrame) => {
    // -3 is ERR_ABORTED, which a redirect produces on the way to a page that
    // then loads normally.
    if (!isMainFrame || code === -3) return;
    clearTimeout(timer);
    console.error(`vista-window: smoke test could not load ${url}: ${description} (${code})`);
    app.exit(1);
  });
  win.webContents.on('did-finish-load', () => {
    const title = win.webContents.getTitle();
    clearTimeout(timer);
    if (!title.trim()) {
      console.error('vista-window: smoke test loaded a page with no title');
      app.exit(1);
      return;
    }
    console.log(`vista-window: smoke test loaded "${title}"`);
    app.exit(0);
  });
  win.loadURL(args.url).catch(() => {
    // Reported by did-fail-load above.
  });
}

// ─── lifecycle (W5) ─────────────────────────────────────────────────────────

if (origin && !args.smokeTest && !app.requestSingleInstanceLock()) {
  // Another VISTA window is open; it gets the `second-instance` event below.
  console.error('vista-window: VISTA is already open; that window was brought forward.');
  app.exit(EXIT_ALREADY_OPEN);
}

app.on('second-instance', () => {
  if (!mainWindow) return;
  if (mainWindow.isMinimized()) mainWindow.restore();
  mainWindow.show();
  mainWindow.focus();
});

// The window is VISTA: closing it on macOS quits too, against the platform's
// habit, because the launcher stops the services when this process exits.
app.on('window-all-closed', () => app.quit());

// The launcher's Ctrl-C and terminal-hangup path sends TERM.
for (const signal of /** @type {const} */ (['SIGTERM', 'SIGINT', 'SIGHUP'])) {
  process.on(signal, () => app.quit());
}

app.whenReady().then(() => {
  if (!origin) return;
  configureSession();
  if (args.smokeTest) {
    runSmokeTest();
    return;
  }
  Menu.setApplicationMenu(buildMenu());
  if (!app.isPackaged) app.dock?.setIcon(ICON);
  mainWindow = createMainWindow();
});
