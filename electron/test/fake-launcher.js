#!/usr/bin/env node
// A deterministic supervised launcher used only by Electron e2e tests.
import { appendFileSync, existsSync, writeFileSync } from 'node:fs';

if (!process.argv.includes('--supervised') || !process.argv.includes('--progress=jsonl')) {
  process.exit(2);
}

const scenario = process.env.VISTA_FAKE_SCENARIO ?? 'success';
const uiUrl = process.env.VISTA_FAKE_UI_URL ?? 'http://127.0.0.1:3000';
const marker = process.env.VISTA_FAKE_STOP_MARKER ?? '';
const count = process.env.VISTA_FAKE_START_COUNT ?? '';
const attempt = process.env.VISTA_FAKE_ATTEMPT_FILE ?? '';
if (count) appendFileSync(count, 'start\n', 'utf8');

/**
 * @param {string} phase
 * @param {string} state
 * @param {Record<string, unknown>} [extra]
 */
const emit = (phase, state, extra = {}) => {
  process.stdout.write(`${JSON.stringify({ protocol: 1, phase, state, ...extra })}\n`);
};
/** @param {number} milliseconds */
const pause = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

let stopping = false;
function stop() {
  if (stopping) return;
  stopping = true;
  emit('stopping', 'running', { label: 'Stopping VISTA' });
  if (marker) writeFileSync(marker, 'stopped\n', 'utf8');
  process.exit(0);
}
for (const signal of ['SIGTERM', 'SIGINT', 'SIGHUP']) process.on(signal, stop);
process.stdin.resume();
process.stdin.on('end', stop);

async function run() {
  emit('preflight', 'running', { label: 'Checking this computer' });
  await pause(80);

  if (scenario === 'retry' && attempt && !existsSync(attempt)) {
    writeFileSync(attempt, 'failed-once\n', 'utf8');
    emit('preflight', 'failed', { code: 'port-conflict' });
    await pause(30);
    process.exit(1);
  }

  emit('preflight', 'complete');
  const phases = [
    ['resources', 'Installing bundled resources'],
    ['sandbox', 'Preparing the code-execution sandbox'],
  ];
  for (const [phase, label] of phases) {
    emit(phase, 'running', { label });
    // Long enough for a cold first window to see the running state.
    await pause(300);
    emit(phase, 'complete', scenario === 'skipped' ? { skipped: true } : {});
  }
  for (const [phase, label] of [
    ['mcp', 'Starting scientific tools'],
    ['backend', 'Preparing VISTA'],
  ]) {
    emit(phase, 'running', { label });
    // Long enough for a cold first window to see the running state.
    await pause(300);
    emit(phase, 'ready');
  }
  emit('ui', 'running', { label: 'Starting the interface' });
  if (scenario === 'hold') return;
  await pause(120);
  emit('ui', 'ready', { url: uiUrl });
  if (scenario === 'after-ready-failure') {
    await pause(800);
    process.exit(1);
  }
}

await run();
setInterval(() => {}, 60_000);
