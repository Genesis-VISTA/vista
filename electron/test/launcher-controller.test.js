// @ts-check
import { EventEmitter } from 'node:events';
import { PassThrough } from 'node:stream';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { LauncherController, launchCommand } from '../src/launcher-controller.js';

/**
 * @param {string} phase
 * @param {string} state
 * @param {Record<string, unknown>} [extra]
 */
const line = (phase, state, extra = {}) => `${JSON.stringify({ protocol: 1, phase, state, ...extra })}\n`;

/** @returns {any} */
function fakeChild() {
  const child = /** @type {any} */ (new EventEmitter());
  child.stdout = new PassThrough();
  child.stdin = new PassThrough();
  child.kills = [];
  /** @param {NodeJS.Signals} signal */
  child.kill = (signal) => {
    child.kills.push(signal);
    queueMicrotask(() => child.emit('exit', signal === 'SIGKILL' ? null : 0, signal));
    return true;
  };
  return child;
}

test('normalizes launcher events and reports readiness', async () => {
  const directory = mkdtempSync(path.join(tmpdir(), 'vista-controller-'));
  const child = fakeChild();
  /** @type {any[]} */
  const states = [];
  /** @type {string[]} */
  const ready = [];
  const controller = new LauncherController({
    launcherPath: '/fake/vista',
    logPath: path.join(directory, 'window.log'),
    onState: (state) => states.push(state),
    onReady: (url) => ready.push(url),
    spawnProcess: () => /** @type {any} */ (child),
  });
  controller.start();
  child.stdout.write(line('preflight', 'running'));
  child.stdout.write(line('preflight', 'complete'));
  child.stdout.write(line('resources', 'running'));
  child.stdout.write(line('resources', 'complete', { skipped: true }));
  child.stdout.write(line('sandbox', 'running'));
  child.stdout.write(line('sandbox', 'complete', { skipped: true }));
  child.stdout.write(line('mcp', 'running'));
  child.stdout.write(line('mcp', 'ready'));
  child.stdout.write(line('backend', 'running'));
  child.stdout.write(line('backend', 'ready'));
  child.stdout.write(line('ui', 'running'));
  child.stdout.write(line('ui', 'ready', { url: 'http://127.0.0.1:3000' }));
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(ready, ['http://127.0.0.1:3000']);
  assert.equal(states.at(-1)?.status, 'ready');
  await controller.stop();
  rmSync(directory, { recursive: true, force: true });
});

test('retry remains disabled until a failed launcher exits', async () => {
  const directory = mkdtempSync(path.join(tmpdir(), 'vista-controller-'));
  const children = [fakeChild(), fakeChild()];
  /** @type {any[]} */
  const states = [];
  const controller = new LauncherController({
    launcherPath: '/fake/vista',
    logPath: path.join(directory, 'window.log'),
    onState: (state) => states.push(state),
    onReady: () => {},
    spawnProcess: () => /** @type {any} */ (children.shift()),
  });
  controller.start();
  const first = /** @type {ReturnType<typeof fakeChild>} */ (controller.child);
  first.stdout.write(line('preflight', 'running'));
  first.stdout.write(line('preflight', 'failed', { code: 'port-conflict' }));
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(states.at(-1)?.canRetry, false);
  assert.equal(controller.retry(), false);
  first.emit('exit', 1, null);
  assert.equal(states.at(-1)?.canRetry, true);
  assert.equal(controller.retry(), true);
  await controller.stop();
  rmSync(directory, { recursive: true, force: true });
});

test('malformed protocol terminates the launcher without retaining raw output', async () => {
  const directory = mkdtempSync(path.join(tmpdir(), 'vista-controller-'));
  const child = fakeChild();
  /** @type {any[]} */
  const states = [];
  const controller = new LauncherController({
    launcherPath: '/fake/vista',
    logPath: path.join(directory, 'window.log'),
    onState: (state) => states.push(state),
    onReady: () => assert.fail('malformed launcher became ready'),
    spawnProcess: () => /** @type {any} */ (child),
  });
  controller.start();
  child.stdout.write('{"secret":"must-not-survive"}\n');
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(child.kills[0], 'SIGTERM');
  assert.equal(states.at(-1)?.failure?.code, 'protocol-error');
  assert.doesNotMatch(JSON.stringify(states), /must-not-survive/);
  rmSync(directory, { recursive: true, force: true });
});

test('an unexpected launcher exit after readiness becomes retryable failure', async () => {
  const directory = mkdtempSync(path.join(tmpdir(), 'vista-controller-'));
  const child = fakeChild();
  /** @type {any[]} */
  const states = [];
  const controller = new LauncherController({
    launcherPath: '/fake/vista',
    logPath: path.join(directory, 'window.log'),
    onState: (state) => states.push(state),
    onReady: () => {},
    spawnProcess: () => /** @type {any} */ (child),
  });
  controller.start();
  /** @type {Array<[string, string, Record<string, unknown>?]>} */
  const events = [
    ['preflight', 'running'],
    ['preflight', 'complete'],
    ['resources', 'running'],
    ['resources', 'complete'],
    ['sandbox', 'running'],
    ['sandbox', 'complete'],
    ['mcp', 'running'],
    ['mcp', 'ready'],
    ['backend', 'running'],
    ['backend', 'ready'],
    ['ui', 'running'],
    ['ui', 'ready', { url: 'http://127.0.0.1:3000' }],
  ];
  for (const [phase, state, extra] of events) {
    child.stdout.write(line(phase, state, extra));
  }
  await new Promise((resolve) => setImmediate(resolve));
  child.emit('exit', 1, null);
  assert.equal(states.at(-1)?.status, 'failed');
  assert.equal(states.at(-1)?.canRetry, true);
  assert.equal(states.at(-1)?.activities.at(-1)?.state, 'failed');
  rmSync(directory, { recursive: true, force: true });
});

test('runs vista directly on macOS and Linux, and vista.ps1 hidden under Windows PowerShell', () => {
  assert.deepEqual(launchCommand('/pkg/vista'), {
    command: '/pkg/vista',
    args: ['--supervised', '--progress=jsonl'],
    windowsHide: false,
    preflight: null,
  });
  const windows = launchCommand('C:\\VISTA\\app\\vista.ps1');
  assert.equal(windows.command, 'powershell.exe');
  assert.deepEqual(windows.args, ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
    '-File', 'C:\\VISTA\\app\\vista.ps1', '-Supervised', '-Progress', 'jsonl']);
  assert.equal(windows.windowsHide, true);
  assert.match(String(windows.preflight?.at(-1)), /Unblock-File -LiteralPath \$env:VISTA_PS1/);
  assert.match(String(windows.preflight?.at(-1)), /AllSigned/);
});

/**
 * A controller for vista.ps1, recording what it spawns.
 * @param {any[]} children
 */
function windowsController(children) {
  const directory = mkdtempSync(path.join(tmpdir(), 'vista-controller-'));
  /** @type {{ command: string, args: string[], options: any }[]} */
  const spawned = [];
  /** @type {any[]} */
  const states = [];
  const controller = new LauncherController({
    launcherPath: 'C:\\VISTA\\app\\vista.ps1',
    logPath: path.join(directory, 'window.log'),
    onState: (state) => states.push(state),
    onReady: () => {},
    platform: 'win32',
    spawnProcess: /** @type {any} */ ((/** @type {string} */ command, /** @type {string[]} */ args,
      /** @type {any} */ options) => {
      spawned.push({ command, args, options });
      return children.shift();
    }),
  });
  return { controller, spawned, states, cleanup: () => rmSync(directory, { recursive: true, force: true }) };
}

test('on Windows, the preflight runs first and the launcher starts hidden once it passes', async () => {
  const preflight = fakeChild();
  const launcher = fakeChild();
  const { controller, spawned, cleanup } = windowsController([preflight, launcher]);
  controller.start();
  assert.equal(spawned.length, 1);
  assert.equal(spawned[0].command, 'powershell.exe');
  assert.equal(spawned[0].options.env.VISTA_PS1, 'C:\\VISTA\\app\\vista.ps1');
  assert.equal(spawned[0].options.windowsHide, true);
  preflight.emit('exit', 0, null);
  assert.equal(spawned.length, 2);
  assert.ok(spawned[1].args.includes('-Supervised'));
  assert.equal(spawned[1].options.windowsHide, true);
  assert.equal(controller.child, launcher);
  launcher.emit('exit', 0, null);
  cleanup();
});

test('on Windows, an AllSigned policy is its own preflight failure, and starts no launcher', () => {
  const preflight = fakeChild();
  const { controller, spawned, states, cleanup } = windowsController([preflight]);
  controller.start();
  preflight.emit('exit', 3, null);
  assert.equal(spawned.length, 1);
  assert.equal(states.at(-1)?.status, 'failed');
  assert.deepEqual(states.at(-1)?.failure, { phase: 'preflight', code: 'execution-policy-all-signed', log: '' });
  assert.match(states.at(-1)?.failureMessage, /AllSigned/);
  assert.equal(states.at(-1)?.canRetry, true);
  cleanup();
});

test('on Windows, stop closes the launcher\'s stdin, and kills it only after the grace period', async () => {
  const preflight = fakeChild();
  const launcher = fakeChild();
  let stdinClosed = false;
  launcher.stdin.on('finish', () => { stdinClosed = true; });
  const { controller, cleanup } = windowsController([preflight, launcher]);
  controller.start();
  preflight.emit('exit', 0, null);
  await controller.stop(50);
  assert.equal(stdinClosed, true);
  assert.deepEqual(launcher.kills, ['SIGKILL']);
  cleanup();
});

test('on Windows, a launcher that exits on stdin closing is never killed', async () => {
  const preflight = fakeChild();
  const launcher = fakeChild();
  launcher.stdin.on('finish', () => queueMicrotask(() => launcher.emit('exit', 0, null)));
  const { controller, cleanup } = windowsController([preflight, launcher]);
  controller.start();
  preflight.emit('exit', 0, null);
  await controller.stop(5_000);
  assert.deepEqual(launcher.kills, []);
  cleanup();
});
