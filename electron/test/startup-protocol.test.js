// @ts-check
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import {
  FAILURE_CODES,
  formatDiagnostics,
  parseProgressLine,
  ProtocolError,
  StartupStateMachine,
} from '../src/startup-protocol.js';

/**
 * @param {string} phase
 * @param {string} state
 * @param {Record<string, unknown>} [extra]
 */
const event = (phase, state, extra = {}) => JSON.stringify({ protocol: 1, phase, state, ...extra });

test('parses and normalizes an allow-listed protocol event', () => {
  assert.deepEqual(parseProgressLine(event('resources', 'complete', { skipped: true })), {
    protocol: 1,
    phase: 'resources',
    state: 'complete',
    skipped: true,
  });
});

test('accepts localhost for the source development server', () => {
  assert.deepEqual(
    parseProgressLine('{"protocol":1,"phase":"ui","state":"ready","url":"http://localhost:3000"}'),
    { protocol: 1, phase: 'ui', state: 'ready', url: 'http://localhost:3000' },
  );
});

test('rejects malformed, unsupported and unexpectedly broad events', () => {
  assert.throws(() => parseProgressLine('{'), ProtocolError);
  assert.throws(() => parseProgressLine('{"protocol":2,"phase":"preflight","state":"running"}'), /unsupported/);
  assert.throws(() => parseProgressLine(event('unknown', 'running')), /phase/);
  assert.throws(
    () => parseProgressLine(JSON.stringify({ protocol: 1, phase: 'preflight', state: 'running', token: 'secret' })),
    /unsupported field/,
  );
  assert.throws(() => parseProgressLine(event('backend', 'failed')), /error code/);
  assert.throws(() => parseProgressLine(event('ui', 'ready', { url: 'https://example.org' })), /local VISTA/);
  assert.throws(() => parseProgressLine(event('backend', 'failed', { code: 'health-timeout', log: '../secret' })), /basename/);
});

test('maps ordered startup activity through UI readiness', () => {
  const machine = new StartupStateMachine();
  machine.acceptLine(event('preflight', 'running', { label: 'Checking this computer' }));
  machine.acceptLine(event('preflight', 'complete'));
  machine.acceptLine(event('resources', 'running'));
  machine.acceptLine(event('resources', 'complete', { skipped: true }));
  machine.acceptLine(event('sandbox', 'running'));
  machine.acceptLine(event('sandbox', 'complete', { skipped: true }));
  machine.acceptLine(event('mcp', 'running'));
  machine.acceptLine(event('mcp', 'ready'));
  machine.acceptLine(event('backend', 'running'));
  machine.acceptLine(event('backend', 'ready'));
  machine.acceptLine(event('ui', 'running'));
  const snapshot = machine.acceptLine(event('ui', 'ready', { url: 'http://127.0.0.1:3000' }));
  assert.equal(snapshot.status, 'ready');
  assert.equal(snapshot.url, 'http://127.0.0.1:3000');
  assert.equal(snapshot.activities.find((activity) => activity.phase === 'resources')?.skipped, true);
});

test('turns safe failure fields into actionable display and diagnostics', () => {
  const machine = new StartupStateMachine();
  machine.acceptLine(event('preflight', 'running'));
  machine.acceptLine(event('preflight', 'complete'));
  machine.acceptLine(event('resources', 'running'));
  const snapshot = machine.acceptLine(event('resources', 'failed', {
    code: 'resource-extraction-failed',
    log: 'setup.log',
  }));
  assert.equal(snapshot.status, 'failed');
  assert.equal(snapshot.canRetry, true);
  assert.match(snapshot.failureMessage, /disk space/);
  assert.equal(
    formatDiagnostics(snapshot, { version: '1.2.3', platform: 'macOS 26 arm64' }),
    'VISTA 1.2.3\nPlatform: macOS 26 arm64\nPhase: resources\nCode: resource-extraction-failed\nLog: setup.log',
  );
});

test('rejects out-of-order activity and supports stopping at any point', () => {
  const machine = new StartupStateMachine();
  assert.throws(() => machine.acceptLine(event('resources', 'running')), /first launcher event/);
  machine.acceptLine(event('preflight', 'running'));
  assert.throws(() => machine.acceptLine(event('backend', 'ready')), /begin with running/);
  const stopped = machine.acceptLine(event('stopping', 'running', { label: 'Stopping VISTA' }));
  assert.equal(stopped.status, 'stopping');
  assert.equal(stopped.canRetry, false);
});

// Both launchers emit codes the application has to explain: one without a
// message of its own would reach the researcher as a bare "could not complete
// startup".
test('every failure code either launcher emits has its own message', () => {
  const read = (/** @type {string} */ name) =>
    readFileSync(new URL(`../../scripts/${name}`, import.meta.url), 'utf8');
  const emitted = new Set([
    ...[...read('package_launcher.sh').matchAll(/(?:die_with_code|failed '' '' '') ([a-z-]+)/g)].map((m) => m[1]),
    ...[...read('package_launcher.ps1').matchAll(/(?:Die-WithCode|-Code) ([a-z-]+)/g)].map((m) => m[1]),
  ]);
  emitted.delete('code'); // die_with_code's own "$code" argument
  assert.ok(emitted.has('port-conflict') && emitted.has('package-path-too-long'), [...emitted].join(' '));
  for (const code of emitted) assert.ok(FAILURE_CODES.includes(code), `no message for ${code}`);
});
