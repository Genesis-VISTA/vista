// @ts-check
import { EventEmitter } from 'node:events';
import { PassThrough } from 'node:stream';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { LauncherController } from '../src/launcher-controller.js';

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
