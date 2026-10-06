// @ts-check
// Hermetic: linux/vista-app, the Linux entry point (desktop-app-startup D9), run
// against a fake window, a fake window-sandbox, and stand-ins for id, ldd and
// notify-send on PATH. Runs the same on macOS and in PR CI, as root or not.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { chmodSync, copyFileSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const SCRIPT = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', 'linux', 'vista-app');

/**
 * @param {string} file
 * @param {string} body
 */
function executable(file, body) {
  writeFileSync(file, `#!/usr/bin/env bash\n${body}`, 'utf8');
  chmodSync(file, 0o755);
}

/**
 * A package's app/window with vista-app in it, and a host to run it on.
 * @param {{ uid?: number, missing?: string[], sandbox?: string, notify?: boolean }} [host]
 */
function fixture({ uid = 1000, missing = [], sandbox = '', notify = true } = {}) {
  const root = mkdtempSync(path.join(tmpdir(), 'vista-app-'));
  const windowDir = path.join(root, 'app', 'window');
  const tools = path.join(root, 'tools');
  const record = path.join(root, 'record');
  mkdirSync(windowDir, { recursive: true });
  mkdirSync(tools);
  mkdirSync(record);
  copyFileSync(SCRIPT, path.join(windowDir, 'vista-app'));
  chmodSync(path.join(windowDir, 'vista-app'), 0o755);

  // Records each start's arguments and notice; exits with FAKE_EXIT_<n>.
  executable(path.join(windowDir, 'VISTA'), `
n=$(( $(cat "$FAKE_RECORD/starts" 2>/dev/null || echo 0) + 1 ))
echo "$n" > "$FAKE_RECORD/starts"
printf '%s\\n' "$*" >> "$FAKE_RECORD/args"
printf '%s' "\${VISTA_SANDBOX_NOTICE:-}" > "$FAKE_RECORD/notice-$n"
echo "window attempt $n"
code="FAKE_EXIT_$n"
exit "\${!code:-0}"
`);
  writeFileSync(path.join(root, 'sandbox-reason'), sandbox, 'utf8');
  executable(path.join(windowDir, 'window-sandbox'), sandbox
    ? `echo --no-sandbox\ncat '${path.join(root, 'sandbox-reason')}' >&2\n`
    : 'exit 0\n');
  executable(path.join(tools, 'id'), `echo ${uid}\n`);
  executable(path.join(tools, 'ldd'),
    missing.map((name) => `echo '\t${name} => not found'`).join('\n') + '\necho "\tlibc.so.6 => /lib/libc.so.6"\n');
  if (notify) executable(path.join(tools, 'notify-send'), 'printf "%s\\n" "$@" > "$FAKE_RECORD/notification"\n');

  /** @param {Record<string, string>} [extra] @param {string[]} [args] */
  const run = (extra = {}, args = []) => {
    const env = { ...process.env };
    for (const key of ['SSH_CONNECTION', 'SSH_TTY', 'WAYLAND_DISPLAY', 'VISTA_SANDBOX_NOTICE']) delete env[key];
    const result = spawnSync(path.join(windowDir, 'vista-app'), args, {
      encoding: 'utf8',
      timeout: 20_000,
      env: {
        ...env,
        PATH: `${tools}:/usr/bin:/bin`,
        DISPLAY: ':0',
        VISTA_HOME: path.join(root, 'state'),
        FAKE_RECORD: record,
        ...extra,
      },
    });
    /** @param {string} name */
    const read = (name) => (existsSync(path.join(record, name)) ? readFileSync(path.join(record, name), 'utf8') : null);
    const logs = path.join(root, 'state', 'logs');
    return {
      status: result.status,
      stderr: result.stderr,
      starts: (read('args') ?? '').split('\n').filter(Boolean),
      notice: /** @param {number} n */ (n) => read(`notice-${n}`),
      notification: read('notification'),
      windowLog: existsSync(path.join(logs, 'window.log')) ? readFileSync(path.join(logs, 'window.log'), 'utf8') : '',
      firstAttempt: existsSync(path.join(logs, 'window-first-attempt.log')),
    };
  };
  return { run, cleanup: () => rmSync(root, { recursive: true, force: true }) };
}

test('refuses as root, with a notification and a line in window.log, and starts no window', () => {
  const { run, cleanup } = fixture({ uid: 0 });
  const r = run();
  assert.equal(r.status, 1);
  assert.deepEqual(r.starts, []);
  assert.match(String(r.notification), /VISTA cannot open\n.*does not run as root/);
  assert.match(r.windowLog, /vista-app: VISTA cannot open: VISTA does not run as root/);
  cleanup();
});

test('refuses with no display, and over a remote shell', () => {
  const { run, cleanup } = fixture();
  let r = run({ DISPLAY: '' });
  assert.equal(r.status, 1);
  assert.match(String(r.notification), /no graphical display/);
  r = run({ SSH_CONNECTION: '10.0.0.1 22 10.0.0.2 5000' });
  assert.equal(r.status, 1);
  assert.match(String(r.notification), /remote shell session/);
  assert.deepEqual(r.starts, []);
  cleanup();
});

test('names missing system libraries rather than starting the window', () => {
  const { run, cleanup } = fixture({ missing: ['libgtk-3.so.0', 'libnss3.so'] });
  const r = run();
  assert.equal(r.status, 1);
  assert.deepEqual(r.starts, []);
  assert.match(String(r.notification), /system libraries this computer lacks \(libgtk-3\.so\.0 libnss3\.so\)/);
  cleanup();
});

test('without notify-send, a refusal still reaches window.log', () => {
  const { run, cleanup } = fixture({ uid: 0, notify: false });
  const r = run();
  assert.equal(r.status, 1);
  assert.equal(r.notification, null);
  assert.match(r.windowLog, /does not run as root/);
  cleanup();
});

test('a host that allows the sandbox: the window starts in startup mode, sandboxed, with no notice', () => {
  const { run, cleanup } = fixture();
  const r = run({}, ['--extra']);
  assert.equal(r.status, 0);
  assert.deepEqual(r.starts, ['--startup --extra']);
  assert.equal(r.notice(1), '');
  assert.match(r.windowLog, /window attempt 1/);
  cleanup();
});

test('a host that blocks it: --no-sandbox from the start, with window-sandbox\'s reason as the notice', () => {
  const { run, cleanup } = fixture({ sandbox: 'The VISTA window is running without Chromium\'s sandbox: it is blocked.' });
  const r = run({ FAKE_EXIT_1: '133' });
  assert.deepEqual(r.starts, ['--startup --no-sandbox']);
  assert.equal(r.notice(1), 'The VISTA window is running without Chromium\'s sandbox: it is blocked.');
  // Already unsandboxed, so a crash is not retried.
  assert.equal(r.status, 133);
  cleanup();
});

test('a window that dies as it starts is started once more without the sandbox', () => {
  const { run, cleanup } = fixture();
  const r = run({ FAKE_EXIT_1: '133' });
  assert.equal(r.status, 0);
  assert.deepEqual(r.starts, ['--startup', '--startup --no-sandbox']);
  assert.match(String(r.notice(2)), /stopped as it started \(exit 133\).*without Chromium's sandbox/);
  assert.equal(r.firstAttempt, true);
  cleanup();
});

test('another VISTA window already open (75), or a clean exit, is not retried', () => {
  const { run, cleanup } = fixture();
  let r = run({ FAKE_EXIT_1: '75' });
  assert.equal(r.status, 75);
  assert.deepEqual(r.starts, ['--startup']);
  cleanup();
  const second = fixture();
  r = second.run({ FAKE_EXIT_1: '0' });
  assert.equal(r.status, 0);
  assert.deepEqual(r.starts, ['--startup']);
  second.cleanup();
});
