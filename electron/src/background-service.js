// @ts-check
//
// Background mode (slack-channel D17): the login item runs the package launcher
// with --service, which writes <state>/service.json while it runs. Opened while
// it runs, VISTA shows a window onto that service instead of starting a second
// stack on the same ports, and closing the window leaves the service running.
//
// The file is only believed when its process is alive and its address answers:
// a crash, or Windows' `schtasks /End`, leaves it behind. Only a loopback http
// address is accepted, so the file cannot send the window to another origin.

import { readFile } from 'node:fs/promises';
import path from 'node:path';

export const SERVICE_FILE = 'service.json';
const ANSWER_TIMEOUT_MS = 2_000;

/** @param {number} pid */
function processAlive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    // EPERM too: alive, but another user's, so not our service.
    return false;
  }
}

/** @param {string} url */
async function answers(url) {
  try {
    const response = await fetch(url, {
      redirect: 'manual',
      signal: AbortSignal.timeout(ANSWER_TIMEOUT_MS),
    });
    await response.body?.cancel();
    return true;
  } catch {
    return false;
  }
}

/**
 * The address a service.json describes, if it is one this window may open.
 * @param {unknown} value
 * @returns {string | null}
 */
export function loopbackUrl(value) {
  if (typeof value !== 'string') return null;
  try {
    const url = new URL(value);
    if (url.protocol !== 'http:') return null;
    if (url.hostname !== '127.0.0.1' && url.hostname !== 'localhost') return null;
    if (url.username || url.password) return null;
    return url.origin;
  } catch {
    return null;
  }
}

/**
 * The URL of VISTA's running background service, or null to start VISTA as
 * usual.
 *
 * @param {string} stateDirectory
 * @param {{
 *   read?: (file: string) => Promise<string>,
 *   isAlive?: (pid: number) => boolean,
 *   isAnswering?: (url: string) => Promise<boolean>,
 * }} [io]
 * @returns {Promise<string | null>}
 */
export async function findRunningService(stateDirectory, io = {}) {
  const read = io.read ?? ((file) => readFile(file, 'utf8'));
  const isAlive = io.isAlive ?? processAlive;
  const isAnswering = io.isAnswering ?? answers;
  let described;
  try {
    described = JSON.parse(await read(path.join(stateDirectory, SERVICE_FILE)));
  } catch {
    return null;
  }
  const pid = described?.pid;
  const url = loopbackUrl(described?.url);
  if (!Number.isInteger(pid) || pid <= 0 || !url) return null;
  if (!isAlive(pid)) return null;
  return (await isAnswering(url)) ? url : null;
}
