// @ts-check
// Hermetic: the host is faked through the script's test variables, so this runs
// the same on macOS and in PR CI, as root or not (linux-desktop-window D1).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { chmodSync, mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const SCRIPT = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', 'linux', 'window-sandbox');
const HOST = mkdtempSync(path.join(tmpdir(), 'window-sandbox-'));

/**
 * @param {string} name
 * @param {number} status
 */
function fakeUnshare(name, status) {
  const file = path.join(HOST, name);
  writeFileSync(file, `#!/bin/sh\nexit ${status}\n`);
  chmodSync(file, 0o755);
  return file;
}

const ALLOWED = fakeUnshare('unshare-ok', 0);
const BLOCKED = fakeUnshare('unshare-blocked', 1);
const MISSING = path.join(HOST, 'no-unshare');

const RESTRICTING = path.join(HOST, 'restrict-1');
writeFileSync(RESTRICTING, '1\n');
const NOT_RESTRICTING = path.join(HOST, 'restrict-0');
writeFileSync(NOT_RESTRICTING, '0\n');
const ABSENT = path.join(HOST, 'absent');

const PROFILE = path.join(HOST, 'vista-window');
writeFileSync(PROFILE, '');

/**
 * @param {{ uid?: string, probe: string, sysctl: string, profile?: string }} host
 * @returns {{ args: string, reason: string }}
 */
function run({ uid = '1000', probe, sysctl, profile = ABSENT }) {
  const result = spawnSync(SCRIPT, {
    encoding: 'utf8',
    env: {
      ...process.env,
      VISTA_WINDOW_SANDBOX_UID: uid,
      VISTA_WINDOW_SANDBOX_PROBE: probe,
      VISTA_WINDOW_SANDBOX_SYSCTL: sysctl,
      VISTA_WINDOW_SANDBOX_PROFILE: profile,
    },
  });
  // The launchers treat any failure as "no extra arguments", so the script
  // itself must never fail.
  assert.equal(result.status, 0, result.stderr);
  return { args: result.stdout.trim(), reason: result.stderr.trim() };
}

const INSTALL = `sudo install -m 644 '${path.dirname(SCRIPT)}/vista-window.apparmor' /etc/apparmor.d/vista-window`;

test('root: --no-sandbox, because Chromium refuses root otherwise', () => {
  const { args, reason } = run({ uid: '0', probe: ALLOWED, sysctl: ABSENT });
  assert.equal(args, '--no-sandbox');
  assert.match(reason, /running as root/);
});

test('user namespaces allowed (Debian, Fedora, RHEL): sandbox on, nothing said', () => {
  assert.deepEqual(run({ probe: ALLOWED, sysctl: ABSENT }), { args: '', reason: '' });
  assert.deepEqual(run({ probe: ALLOWED, sysctl: NOT_RESTRICTING }), { args: '', reason: '' });
});

test('Ubuntu restricting, profile installed: sandbox on, nothing said', () => {
  assert.deepEqual(run({ probe: BLOCKED, sysctl: RESTRICTING, profile: PROFILE }), { args: '', reason: '' });
});

test('Ubuntu restricting, no profile: --no-sandbox, naming the install command', () => {
  const { args, reason } = run({ probe: BLOCKED, sysctl: RESTRICTING });
  assert.equal(args, '--no-sandbox');
  assert.match(reason, /Ubuntu restricts unprivileged user namespaces/);
  assert.ok(reason.includes(INSTALL), reason);
  assert.ok(reason.includes('sudo apparmor_parser -r /etc/apparmor.d/vista-window'), reason);
});

test('blocked some other way (a container): --no-sandbox, naming no install step', () => {
  const { args, reason } = run({ probe: BLOCKED, sysctl: ABSENT });
  assert.equal(args, '--no-sandbox');
  assert.match(reason, /does not allow unprivileged user namespaces/);
  assert.doesNotMatch(reason, /sudo/);
});

test('no unshare: falls back to Ubuntu\'s setting', () => {
  assert.deepEqual(run({ probe: MISSING, sysctl: ABSENT }), { args: '', reason: '' });
  assert.deepEqual(run({ probe: MISSING, sysctl: NOT_RESTRICTING }), { args: '', reason: '' });
  assert.equal(run({ probe: MISSING, sysctl: RESTRICTING }).args, '--no-sandbox');
  assert.deepEqual(run({ probe: MISSING, sysctl: RESTRICTING, profile: PROFILE }), { args: '', reason: '' });
});
