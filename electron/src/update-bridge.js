// @ts-check
//
// Restarting into an update (openspec change `in-app-updates`, design D4–D6).
//
// The backend downloads and verifies a release into VISTA_HOME/updates/<version>/
// and records each file's sha256 in ready.json. The page can only ask the window
// to restart into a version; everything else is decided here, from what the
// window knows itself: which window and frame asked, from which origin, whether
// this VISTA is an installed package, what the backend says is ready, and what
// the files on disk hash to now. No path, command or URL ever comes from the page.

import { createHash } from 'node:crypto';
import { createReadStream, existsSync, readFileSync, rmSync } from 'node:fs';
import path from 'node:path';

import { originOf } from './routing.js';

/** A version as releases spell it: 0.2.5, 0.2.6-rc1. Nothing a path could hide in. */
export const VERSION_PATTERN = /^\d+(\.\d+){1,3}(-?(a|b|rc)\d+)?$/;
const PLAIN_NAME = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;
const HEX_SHA256 = /^[0-9a-f]{64}$/;

/** What the cache must hold for the installer to run from it. */
export const REQUIRED = Object.freeze({
  darwin: { platform: 'mac-arm64', ext: 'tar.gz', installer: 'install.sh' },
  linux: { platform: 'linux-x86', ext: 'tar.gz', installer: 'install.sh' },
  win32: { platform: 'win-x86', ext: 'zip', installer: 'install.ps1' },
});

/**
 * @param {string} version
 * @param {NodeJS.Platform} platform
 */
export function requiredFiles(version, platform) {
  const layout = REQUIRED[/** @type {keyof typeof REQUIRED} */ (platform)];
  if (!layout) return null;
  const archive = `vista-${version}-${layout.platform}.${layout.ext}`;
  return { archive, checksum: `${archive}.sha256`, installer: layout.installer };
}

/** @param {string} file */
export function sha256File(file) {
  return new Promise((resolve, reject) => {
    const hash = createHash('sha256');
    createReadStream(file)
      .on('error', reject)
      .on('data', (chunk) => hash.update(chunk))
      .on('end', () => resolve(hash.digest('hex')));
  });
}

/**
 * @typedef {object} RestartRequest
 * @property {boolean} fromMainWindow the sender is the main window's own webContents
 * @property {boolean} fromMainFrame the request came from its top frame, not an iframe
 * @property {string} frameUrl the requesting frame's URL
 * @property {string} origin VISTA's own origin
 * @property {unknown} version what the page asked for
 * @property {string | null} packageRoot the installed package, or null when not run from one
 * @property {string} cacheRoot VISTA_HOME/updates
 * @property {NodeJS.Platform} platform
 * @property {() => Promise<{ state?: unknown, version?: unknown }>} downloadStatus the backend's GET /updates/download
 */

/**
 * Whether to restart into `request.version`, and if so from which folder.
 * Every refusal names its reason, for the page and window.log.
 *
 * @param {RestartRequest} request
 * @returns {Promise<{ ok: true, folder: string, version: string, installer: string } | { ok: false, reason: string }>}
 */
export async function checkRestartRequest(request) {
  /** @param {string} reason */
  const refuse = (reason) => ({ ok: /** @type {const} */ (false), reason });
  if (!request.fromMainWindow || !request.fromMainFrame) {
    return refuse('Only VISTA\'s main window can restart into an update.');
  }
  let frameOrigin = '';
  try {
    frameOrigin = originOf(request.frameUrl);
  } catch {
    frameOrigin = '';
  }
  if (!request.origin || frameOrigin !== request.origin) {
    return refuse('Only VISTA\'s own pages can restart into an update.');
  }
  const version = request.version;
  if (typeof version !== 'string' || !VERSION_PATTERN.test(version)) {
    return refuse('That is not a VISTA version.');
  }
  if (!request.packageRoot) {
    return refuse('This VISTA was not installed from a package, so it cannot update itself.');
  }
  const files = requiredFiles(version, request.platform);
  if (!files) return refuse(`VISTA cannot update itself on ${request.platform}.`);

  let status;
  try {
    status = await request.downloadStatus();
  } catch (error) {
    return refuse(`VISTA could not ask its backend about the download (${error instanceof Error ? error.message : error}).`);
  }
  if (status?.state !== 'ready' || status.version !== version) {
    return refuse(`VISTA ${version} has not been downloaded and verified.`);
  }

  const folder = path.join(request.cacheRoot, version);
  let ready;
  try {
    ready = JSON.parse(readFileSync(path.join(folder, 'ready.json'), 'utf8'));
  } catch {
    return refuse('The downloaded update has no record of its hashes.');
  }
  if (ready?.version !== version || !ready.files || typeof ready.files !== 'object') {
    return refuse('The downloaded update\'s record does not match it.');
  }
  for (const name of [files.archive, files.checksum, files.installer]) {
    if (!Object.hasOwn(ready.files, name)) return refuse(`The downloaded update has no ${name}.`);
  }
  for (const [name, expected] of Object.entries(ready.files)) {
    if (!PLAIN_NAME.test(name) || typeof expected !== 'string' || !HEX_SHA256.test(expected)) {
      return refuse('The downloaded update\'s record is malformed.');
    }
    const file = path.join(folder, name);
    let actual;
    try {
      actual = await sha256File(file);
    } catch {
      return refuse(`The downloaded update is missing ${name}.`);
    }
    if (actual !== expected) {
      return refuse(`${name} changed after it was verified, so VISTA will not install it.`);
    }
  }
  return { ok: true, folder, version, installer: files.installer };
}

/**
 * How to run the helper, detached, once it is copied into the update's folder.
 * Its inputs travel in the environment, never its command line: the helper waits
 * until no process's command line names the install folder, and must not be one.
 *
 * @param {{ platform: NodeJS.Platform, helper: string, packageRoot: string, version: string, folder: string, vistaHome: string, installer: string, env?: NodeJS.ProcessEnv }} hand
 */
export function helperCommand({ platform, helper, packageRoot, version, folder, vistaHome, installer, env = process.env }) {
  const vars = {
    ...env,
    VISTA_UPDATE_INSTALL_DIR: packageRoot,
    VISTA_UPDATE_VERSION: version,
    VISTA_UPDATE_CACHE: folder,
    VISTA_UPDATE_INSTALLER: path.join(folder, installer),
    VISTA_HOME: vistaHome,
  };
  if (platform === 'win32') {
    return {
      command: 'powershell.exe',
      args: ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden', '-File', helper],
      env: vars,
    };
  }
  return { command: '/bin/bash', args: [helper], env: vars };
}

/**
 * The helper shipped with this window, for each platform.
 * @param {NodeJS.Platform} platform
 */
export function helperName(platform) {
  return platform === 'win32' ? 'apply-update.ps1' : 'apply-update.sh';
}

/**
 * Hand over to the helper, then stop VISTA exactly as closing the window does.
 * The helper is running before anything stops, so it is there to wait for
 * every process to exit and only then install.
 *
 * @param {{ spawnHelper: () => void, notify?: () => void, quit: () => Promise<void> | void }} steps
 */
export async function handOff({ spawnHelper, notify, quit }) {
  spawnHelper();
  notify?.();
  await quit();
}

const CONTROL = /[\u0000-\u001f\u007f]/g;

/**
 * The failure an update left behind (VISTA_HOME/updates/last-failure.json),
 * read once and removed, so the next start does not report it again.
 *
 * @param {string} cacheRoot
 * @returns {{ version: string, reason: string } | null}
 */
export function takeUpdateFailure(cacheRoot) {
  const file = path.join(cacheRoot, 'last-failure.json');
  if (!existsSync(file)) return null;
  let parsed = null;
  try {
    parsed = JSON.parse(readFileSync(file, 'utf8'));
  } catch {
    parsed = {};
  } finally {
    rmSync(file, { force: true });
  }
  const version = typeof parsed?.version === 'string' && VERSION_PATTERN.test(parsed.version) ? parsed.version : '';
  const reason = typeof parsed?.reason === 'string' ? parsed.reason.replace(CONTROL, ' ').trim().slice(0, 500) : '';
  return { version, reason: reason || 'The installer did not finish.' };
}
