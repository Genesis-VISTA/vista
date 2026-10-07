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

import { app, BrowserWindow, clipboard, dialog, ipcMain, Menu, nativeTheme, session, shell } from 'electron';
import { existsSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { parseArgs } from './args.js';
import { allowPermission } from './permissions.js';
import { classify, originOf } from './routing.js';
import { windowBackground } from './appearance.js';
import { acquireSingleInstanceLock, EXIT_ALREADY_OPEN, focusWindow } from './lifecycle.js';
import { LauncherController } from './launcher-controller.js';
import { resolvePackage } from './package-root.js';
import { formatDiagnostics, StartupStateMachine } from './startup-protocol.js';

const SMOKE_TEST_TIMEOUT_MS = 30_000;
const SOURCE_DIR = path.dirname(fileURLToPath(import.meta.url));

const args = parseArgs(process.argv);
const developmentApp = process.platform === 'darwin'
  && existsSync(path.join(process.resourcesPath, 'vista-development.json'));
const devMode = args.dev || developmentApp;

/** @param {string} message */
function fail(message) {
  console.error(`vista-window: ${message}`);
  app.exit(2);
}

let origin = '';
if (args.url) {
  try {
    const start = new URL(args.url);
    if (start.protocol !== 'http:' && start.protocol !== 'https:') throw new Error();
    origin = originOf(args.url);
  } catch {
    fail(`--url must be the http(s) address of the running UI (got "${args.url}")`);
  }
} else if (!args.startup) {
  // Only a Linux window opened by hand lands here: its file manager shows no
  // stderr, so say where VISTA opens from instead of vanishing.
  if (process.platform === 'linux' && app.isPackaged) {
    dialog.showErrorBox(
      'Open VISTA from the app menu',
      'This is the VISTA window, which VISTA opens itself. Open VISTA from your app menu, '
        + 'or run app/window/vista-app in the VISTA folder.',
    );
  }
  fail(`--url must be the http(s) address of the running UI (got "${args.url}")`);
}

// The single-instance lock is kept per userData directory, and the dev window and
// the installed one are both named VISTA. Without this, a dev window and an open
// installed one would contend for one lock. An explicit --user-data-dir wins.
if (devMode) app.setPath('userData', path.join(app.getPath('appData'), 'VISTA-dev'));
if (args.userDataDir) app.setPath('userData', path.resolve(args.userDataDir));

// On Linux the launcher adds --no-sandbox where the host blocks Chromium's
// sandbox (linux-desktop-window D1). This line lands in window.log, so a report
// from the field says which mode the window was in.
const rendererSandboxed = !app.commandLine.hasSwitch('no-sandbox');
console.log(`vista-window: renderer sandbox: ${rendererSandboxed ? 'on' : 'off (--no-sandbox)'}`);
// Why, when linux/vista-app started it that way: window-sandbox's reason, with
// the one-time step that turns the sandbox back on where there is one. Shown in
// the startup window, which a researcher sees, rather than only in window.log.
const sandboxNotice = rendererSandboxed ? '' : (process.env.VISTA_SANDBOX_NOTICE ?? '').trim().slice(0, 2000);

// W4: every window, the main one and any child, gets the same renderer: no
// Node, no preload, isolated and sandboxed, i.e. exactly what a browser tab has.
/** @type {Electron.WebPreferences} */
const webPreferences = {
  contextIsolation: true,
  sandbox: true,
  nodeIntegration: false,
  webviewTag: false,
  devTools: devMode,
};

// VISTA's icon (assets/, made by scripts/make-icons.js). A packaged window
// already has it in VISTA.exe or VISTA.app; Linux keeps no icon in the
// executable, and a development run is Electron's own binary, so those set it
// here. macOS ignores a window's icon, so development sets the Dock's instead,
// from the copy drawn on macOS's icon grid: the full-bleed one stands larger
// than every other icon in the Dock.
const ICON = path.join(app.getAppPath(), 'assets', 'icon.png');
const DOCK_ICON = path.join(app.getAppPath(), 'assets', 'icon-mac.png');
/** @type {Partial<Electron.BrowserWindowConstructorOptions>} */
const windowIcon = process.platform === 'linux' || !app.isPackaged ? { icon: ICON } : {};

/** @type {BrowserWindow | null} */
let mainWindow = null;
/** @type {BrowserWindow | null} */
let startupWindow = null;
/** @type {LauncherController | null} */
let launcherController = null;
let quitAllowed = false;
let mainStartupFailed = false;
/** @type {Promise<void> | null} */
let quitInProgress = null;
const allowedWindowCloses = new WeakSet();
/** @type {ReturnType<StartupStateMachine['snapshot']> | null} */
let latestStartupState = null;

function sendStartupState() {
  if (!latestStartupState || !startupWindow || startupWindow.isDestroyed()) return;
  startupWindow.webContents.send('startup:state', { ...latestStartupState, notice: sandboxNotice });
}

/** @param {BrowserWindow} window */
function guardWindowClose(window) {
  window.on('close', (event) => {
    if (quitAllowed || allowedWindowCloses.has(window)) return;
    if (!launcherController?.child) {
      if (window !== mainWindow && mainStartupFailed && mainWindow) closeForTransition(mainWindow);
      return;
    }
    event.preventDefault();
    void requestQuit();
  });
}

/** @param {BrowserWindow} window */
function closeForTransition(window) {
  allowedWindowCloses.add(window);
  window.close();
}

async function requestQuit() {
  if (quitAllowed) {
    app.quit();
    return;
  }
  if (quitInProgress) return quitInProgress;
  quitInProgress = (async () => {
    const controller = launcherController;
    /** @type {NodeJS.Timeout | undefined} */
    let stoppingTimer;
    if (controller?.child && !startupWindow && mainWindow && !mainWindow.isDestroyed()) {
      stoppingTimer = setTimeout(() => {
        if (!controller.child || !mainWindow || mainWindow.isDestroyed()) return;
        const bounds = mainWindow.getBounds();
        mainWindow.hide();
        startupWindow = createStartupWindow(bounds);
        sendStartupState();
      }, 250);
    }
    await controller?.stop();
    clearTimeout(stoppingTimer);
    quitAllowed = true;
    app.quit();
  })();
  return quitInProgress;
}

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
  // The UI asks for none of them. The one exception is writing to the
  // clipboard from VISTA's own origin, for its Copy buttons (permissions.js).
  ses.setPermissionRequestHandler((_contents, permission, callback, details) =>
    callback(allowPermission(origin, permission, details.requestingUrl)),
  );
  ses.setPermissionCheckHandler((_contents, permission, requestingOrigin) =>
    allowPermission(origin, permission, requestingOrigin),
  );

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
  if (devMode) view.push({ role: 'forceReload' }, { role: 'toggleDevTools' });
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

/**
 * @param {string} [url]
 * @param {(() => void) | null} [onShown]
 */
function createMainWindow(url = args.url, onShown = null) {
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
  guardWindowClose(win);
  win.once('ready-to-show', () => {
    if (mainStartupFailed) return;
    win.show();
    onShown?.();
  });
  win.on('closed', () => {
    nativeTheme.off('updated', onThemeUpdated);
    if (mainWindow === win) mainWindow = null;
  });
  win.loadURL(url);
  return win;
}

/** @param {Electron.Rectangle | null} [initialBounds] */
function createStartupWindow(initialBounds = null) {
  const win = new BrowserWindow({
    width: initialBounds?.width ?? 720,
    height: initialBounds?.height ?? 640,
    ...(initialBounds ? { x: initialBounds.x, y: initialBounds.y } : {}),
    minWidth: 600,
    minHeight: 540,
    title: 'VISTA',
    show: false,
    backgroundColor: windowBackground(nativeTheme.shouldUseDarkColors),
    webPreferences: {
      ...webPreferences,
      preload: path.join(SOURCE_DIR, 'startup-preload.cjs'),
    },
  });
  const onThemeUpdated = () => win.setBackgroundColor(windowBackground(nativeTheme.shouldUseDarkColors));
  nativeTheme.on('updated', onThemeUpdated);
  guardWindowClose(win);
  win.once('ready-to-show', () => win.show());
  win.on('closed', () => {
    nativeTheme.off('updated', onThemeUpdated);
    if (startupWindow === win) startupWindow = null;
    if (mainStartupFailed && !quitAllowed) void requestQuit();
  });
  win.loadFile(path.join(SOURCE_DIR, 'startup.html'));
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

if (!acquireSingleInstanceLock(app, args)) {
  // Another VISTA window is open; it gets the `second-instance` event below.
  console.error('vista-window: VISTA is already open; that window was brought forward.');
  app.exit(EXIT_ALREADY_OPEN);
}

app.on('second-instance', () => {
  focusWindow(mainWindow?.isVisible() ? mainWindow : startupWindow ?? mainWindow);
});

// The window is VISTA: closing it on macOS quits too, against the platform's
// habit, because the launcher stops the services when this process exits.
app.on('window-all-closed', () => { void requestQuit(); });

app.on('before-quit', (event) => {
  if (quitAllowed || !launcherController?.child) return;
  event.preventDefault();
  void requestQuit();
});

// The launcher's Ctrl-C and terminal-hangup path sends TERM.
for (const signal of /** @type {const} */ (['SIGTERM', 'SIGINT', 'SIGHUP'])) {
  process.on(signal, () => { void requestQuit(); });
}

app.whenReady().then(() => {
  if (!origin && !args.startup) return;
  configureSession();
  if (args.smokeTest) {
    runSmokeTest();
    return;
  }
  Menu.setApplicationMenu(buildMenu());
  if (!app.isPackaged) app.dock?.setIcon(DOCK_ICON);
  if (args.startup) {
    let launcherPath = args.launcher ? path.resolve(args.launcher) : '';
    if (!launcherPath) {
      try {
        ({ launcherPath } = resolvePackage({
          platform: process.platform,
          execPath: process.execPath,
          resourcesPath: process.resourcesPath,
        }));
      } catch (error) {
        dialog.showMessageBoxSync({
          type: 'error',
          title: 'VISTA cannot start',
          message: process.platform === 'darwin'
            ? 'VISTA.app must stay inside the folder it was distributed with.'
            : 'VISTA must stay inside the folder it was installed in.',
          detail: `${process.platform === 'darwin'
            ? 'Move VISTA.app back beside “vista”, “manifest.json”, and the app runtime, then open it again.'
            : 'The whole VISTA folder is the application: reinstall it, or put back what was moved, then open it again.'}\n\n${error instanceof Error ? error.message : 'The package layout is invalid.'}`,
          buttons: ['Quit'],
        });
        quitAllowed = true;
        app.quit();
        return;
      }
    }
    startupWindow = createStartupWindow();
    const stateDirectory = process.env.VISTA_HOME
      || path.join(app.getPath('home'), developmentApp ? '.vista-dev' : '.vista');
    if (developmentApp && !process.env.VISTA_HOME) process.env.VISTA_HOME = stateDirectory;
    const logsDirectory = path.join(stateDirectory, 'logs');
    launcherController = new LauncherController({
      launcherPath,
      logPath: path.join(logsDirectory, 'window.log'),
      onState: (snapshot) => {
        latestStartupState = snapshot;
        if (
          snapshot.status === 'failed'
          && mainWindow
          && !mainWindow.isDestroyed()
        ) {
          mainStartupFailed = true;
          const bounds = mainWindow.getBounds();
          mainWindow.hide();
          if (!startupWindow) startupWindow = createStartupWindow(bounds);
        }
        sendStartupState();
      },
      onReady: (url) => {
        mainStartupFailed = false;
        if (mainWindow && !mainWindow.isDestroyed()) {
          mainWindow.show();
          if (startupWindow && !startupWindow.isDestroyed()) closeForTransition(startupWindow);
          return;
        }
        origin = originOf(url);
        const openingWindow = startupWindow;
        mainWindow = createMainWindow(url, () => {
          console.log('vista-window: main window ready');
          if (openingWindow && !openingWindow.isDestroyed()) closeForTransition(openingWindow);
        });
      },
    });

    /** @param {Electron.IpcMainEvent | Electron.IpcMainInvokeEvent} event */
    const fromStartupWindow = (event) =>
      BrowserWindow.fromWebContents(event.sender) === startupWindow;
    ipcMain.on('startup:ready', (event) => {
      if (fromStartupWindow(event)) sendStartupState();
    });
    ipcMain.handle('startup:retry', (event) => {
      if (!fromStartupWindow(event)) return false;
      return launcherController?.retry() ?? false;
    });
    ipcMain.handle('startup:open-logs', async (event) => {
      if (!fromStartupWindow(event)) return false;
      const error = await shell.openPath(logsDirectory);
      if (error) console.error(`vista-window: could not open logs: ${error}`);
      return !error;
    });
    ipcMain.handle('startup:copy-diagnostics', (event) => {
      if (!fromStartupWindow(event) || !launcherController) return false;
      clipboard.writeText(formatDiagnostics(launcherController.current, {
        version: app.getVersion(),
        platform: `${process.platform} ${os.release()} ${process.arch}`,
      }));
      return true;
    });
    ipcMain.handle('startup:quit', (event) => {
      if (!fromStartupWindow(event)) return false;
      void requestQuit();
      return true;
    });

    try {
      launcherController.start();
    } catch (error) {
      launcherController.launcherFailure(
        `The launcher could not be started (${error instanceof Error ? error.message : 'process error'}).`,
      );
    }
    return;
  }
  mainWindow = createMainWindow();
});
