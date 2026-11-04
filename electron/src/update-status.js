// @ts-check
//
// The update status window (openspec change in-app-updates, design D9): while
// the installer replaces VISTA, a copy of VISTA's own window, run from the
// update's folder with --update-status, shows the step. Nothing on screen can
// run from the install folder, which the installer replaces and the helper
// waits on.
//
//   <update folder>/window/       the copy (VISTA.app on macOS, app/window elsewhere)
//   <update folder>/status.json   {version, step, detail}, written by the helper
//   VISTA_HOME/updates/status-window.json
//                                 written by the helper before it starts the status
//                                 window, and so before it opens any VISTA; the next
//                                 VISTA to show its startup window removes it, and the
//                                 status window, seeing it gone, exits. The status
//                                 window never writes it: one that started late would
//                                 put it back after that VISTA had looked.
//
// Everything here is plain Node, so the unit tests run it without Electron.

import { execFile } from 'node:child_process';
import { cp, readFile, rm } from 'node:fs/promises';
import { existsSync, rmSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { promisify } from 'node:util';

export const STATUS_FILE = 'status.json';
export const MARKER_FILE = 'status-window.json';
/** After the helper's last step, the next VISTA has this long to take over. */
export const AFTER_LAST_STEP_MS = 2 * 60_000;
/** A helper that writes nothing for this long is gone. */
export const SILENT_MS = 30 * 60_000;
export const POLL_MS = 250;

const STEPS = new Set(['waiting', 'installing', 'opening', 'failed']);
const VERSION = /^[0-9A-Za-z][0-9A-Za-z.+-]{0,63}$/;

/**
 * Where the window is copied from and to, and the copy's executable.
 *
 * @param {{ platform: NodeJS.Platform, packageRoot: string, folder: string }} where
 */
export function windowCopyPaths({ platform, packageRoot, folder }) {
  const target = path.join(folder, 'window');
  if (platform === 'darwin') {
    return {
      source: path.join(packageRoot, 'VISTA.app'),
      target: path.join(target, 'VISTA.app'),
      executable: path.join(target, 'VISTA.app', 'Contents', 'MacOS', 'VISTA'),
    };
  }
  return {
    source: path.join(packageRoot, 'app', 'window'),
    target,
    executable: path.join(target, platform === 'win32' ? 'VISTA.exe' : 'VISTA'),
  };
}

/**
 * Copy VISTA's window into the update's folder. On macOS `cp -c` clones it,
 * so it is instant and takes no space; elsewhere it is a real copy.
 *
 * @param {{ platform: NodeJS.Platform, packageRoot: string, folder: string }} where
 * @param {{ run?: (file: string, args: string[]) => Promise<unknown> }} [deps]
 * @returns {Promise<string>} the copy's executable
 */
export async function copyWindow(where, { run = promisify(execFile) } = {}) {
  const { source, target, executable } = windowCopyPaths(where);
  if (!existsSync(source)) throw new Error(`no window at ${source}`);
  await rm(path.join(where.folder, 'window'), { recursive: true, force: true });
  if (where.platform === 'darwin') {
    await run('/bin/mkdir', ['-p', path.dirname(target)]);
    await run('/bin/cp', ['-cR', source, target]);
  } else {
    await cp(source, target, { recursive: true, verbatimSymlinks: true });
  }
  if (!existsSync(executable)) throw new Error(`the copy has no ${path.basename(executable)}`);
  return executable;
}

/**
 * The helper's last word, or null while it has written none, or nothing usable.
 *
 * @param {string} folder
 * @returns {Promise<{ version: string, step: string, detail: string } | null>}
 */
export async function readStatus(folder) {
  let body;
  try {
    body = JSON.parse(await readFile(path.join(folder, STATUS_FILE), 'utf8'));
  } catch {
    return null;
  }
  if (!body || typeof body !== 'object' || !STEPS.has(body.step)) return null;
  const version = typeof body.version === 'string' && VERSION.test(body.version) ? body.version : '';
  const detail = typeof body.detail === 'string' ? body.detail.replace(/\s+/g, ' ').trim().slice(0, 300) : '';
  return { version, step: body.step, detail };
}

/** @param {string} text */
const sentence = (text) => (text ? text[0].toUpperCase() + text.slice(1) : text);

/**
 * What the window says for a status.
 *
 * @param {{ version: string, step: string, detail: string } | null} status
 * @returns {{ title: string, line: string, failed: boolean }}
 */
export function statusView(status) {
  const name = status?.version ? `VISTA ${status.version}` : 'the update';
  switch (status?.step) {
    case 'waiting':
      return { title: `Updating to ${name}`, line: 'Waiting for VISTA to close…', failed: false };
    case 'installing':
      return { title: `Installing ${name}`, line: sentence(status.detail) || 'Installing…', failed: false };
    case 'opening':
      return { title: `Installed ${name}`, line: `Opening ${name}…`, failed: false };
    case 'failed':
      return {
        title: status.version ? `The update to ${status.version} failed` : 'The update failed',
        line: 'Opening the version you had. It will say why.',
        failed: true,
      };
    default:
      return { title: 'Updating VISTA', line: 'Starting the update…', failed: false };
  }
}

/**
 * Whether the status window should close now.
 *
 * @param {{ changedAt: number, endedAt: number | null, now: number, dismissed: boolean }} state
 *   `changedAt`: when status.json last changed (or the window opened);
 *   `endedAt`: when it first said opening or failed, or null;
 *   `dismissed`: the next VISTA removed the marker.
 */
export function shouldClose({ changedAt, endedAt, now, dismissed }) {
  if (dismissed) return 'the next VISTA is showing';
  if (endedAt !== null && now - endedAt >= AFTER_LAST_STEP_MS) return 'no VISTA took over after the last step';
  if (now - changedAt >= SILENT_MS) return 'the update helper stopped writing';
  return null;
}

/**
 * Say a status window is coming, in VISTA_HOME/updates: what the helpers do
 * (apply-update.sh / .ps1); here for the tests.
 *
 * @param {string} cacheRoot
 * @param {{ pid: number, folder: string }} who
 */
export function writeMarker(cacheRoot, who) {
  writeFileSync(path.join(cacheRoot, MARKER_FILE), `${JSON.stringify(who)}\n`);
}

/** @param {string} cacheRoot */
export function markerPresent(cacheRoot) {
  return existsSync(path.join(cacheRoot, MARKER_FILE));
}

/**
 * Close an update's status window, if one is up: the starting VISTA calls this
 * once its startup window shows. Returns whether there was one.
 *
 * @param {string} cacheRoot
 */
export function dismissStatusWindow(cacheRoot) {
  const marker = path.join(cacheRoot, MARKER_FILE);
  if (!existsSync(marker)) return false;
  rmSync(marker, { force: true });
  return true;
}
