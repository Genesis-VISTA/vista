// @ts-check

export const PROTOCOL_VERSION = 1;
export const PHASES = /** @type {const} */ (
  ['preflight', 'resources', 'sandbox', 'mcp', 'backend', 'ui', 'stopping']
);
export const STATES = /** @type {const} */ (
  ['pending', 'running', 'complete', 'ready', 'failed']
);

const ACTIVITY_LABELS = Object.freeze({
  preflight: 'Checking this computer',
  resources: 'Installing bundled resources',
  sandbox: 'Preparing the code-execution sandbox',
  mcp: 'Starting scientific tools',
  backend: 'Preparing VISTA',
  ui: 'Starting the interface',
});

const FAILURE_MESSAGES = Object.freeze({
  'port-conflict': 'A VISTA network port is already in use. Close the other process, then try again.',
  'invalid-state-path': 'The VISTA data location (VISTA_HOME) has too long a path. Choose a shorter one, then try again.',
  'package-path-too-long': 'VISTA is in too deep a folder for Windows. Move the VISTA folder somewhere shorter, then try again.',
  'missing-component': 'This VISTA package is incomplete. Keep the whole unpacked folder together.',
  'resource-extraction-failed': 'Bundled VISTA resources could not be installed. Check available disk space.',
  'sandbox-image-import-failed': 'The code-execution sandbox could not be prepared.',
  'virtualisation-unavailable': 'This computer cannot run the code-execution sandbox: hardware virtualisation is unavailable. Open Logs says how to enable it.',
  'health-timeout': 'A VISTA service did not become ready in time.',
  'startup-error': 'VISTA could not complete startup.',
  'protocol-error': 'This VISTA application and launcher are not compatible.',
  'execution-policy-all-signed': 'Group Policy on this computer lets PowerShell run only signed scripts (AllSigned), and VISTA\'s launcher is not signed. Ask your IT administrator to allow it.',
});
/** Every failure code with a message of its own; anything else reads as startup-error. */
export const FAILURE_CODES = Object.freeze(Object.keys(FAILURE_MESSAGES));

const ALLOWED_KEYS = new Set(['protocol', 'phase', 'state', 'label', 'skipped', 'url', 'code', 'log']);
const SAFE_TOKEN = /^[a-z][a-z0-9-]{0,63}$/;
const SAFE_LOG = /^[A-Za-z0-9._-]{1,100}$/;
const CONTROL_CHARACTER = /[\u0000-\u001f\u007f]/;

export class ProtocolError extends Error {
  /** @param {string} message */
  constructor(message) {
    super(message);
    this.name = 'ProtocolError';
  }
}

/**
 * @typedef {object} ProgressEvent
 * @property {1} protocol
 * @property {(typeof PHASES)[number]} phase
 * @property {(typeof STATES)[number]} state
 * @property {string | undefined} [label]
 * @property {boolean | undefined} [skipped]
 * @property {string | undefined} [url]
 * @property {string | undefined} [code]
 * @property {string | undefined} [log]
 */

/** @param {unknown} value */
function isRecord(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

/**
 * Parse and normalize one complete launcher line. Unknown fields are rejected
 * so credentials or environment values can never accidentally reach memory,
 * diagnostics, or the startup renderer.
 *
 * @param {string} line
 * @returns {ProgressEvent}
 */
export function parseProgressLine(line) {
  let value;
  try {
    value = JSON.parse(line);
  } catch {
    throw new ProtocolError('launcher output is not valid JSON');
  }
  if (!isRecord(value)) throw new ProtocolError('launcher event must be an object');
  const record = /** @type {Record<string, unknown>} */ (value);
  for (const key of Object.keys(record)) {
    if (!ALLOWED_KEYS.has(key)) throw new ProtocolError(`launcher event has unsupported field "${key}"`);
  }
  if (record.protocol !== PROTOCOL_VERSION) {
    throw new ProtocolError(`unsupported launcher protocol version "${String(record.protocol)}"`);
  }
  if (!PHASES.includes(/** @type {any} */ (record.phase))) {
    throw new ProtocolError(`unsupported launcher phase "${String(record.phase)}"`);
  }
  if (!STATES.includes(/** @type {any} */ (record.state))) {
    throw new ProtocolError(`unsupported launcher state "${String(record.state)}"`);
  }
  if (record.label !== undefined && (
    typeof record.label !== 'string' || record.label.length > 120 || CONTROL_CHARACTER.test(record.label)
  )) {
    throw new ProtocolError('launcher label is not safe display text');
  }
  if (record.skipped !== undefined && typeof record.skipped !== 'boolean') {
    throw new ProtocolError('launcher skipped field must be boolean');
  }
  if (record.skipped !== undefined && record.state !== 'complete') {
    throw new ProtocolError('launcher skipped field is valid only for completed work');
  }
  if (record.code !== undefined && (typeof record.code !== 'string' || !SAFE_TOKEN.test(record.code))) {
    throw new ProtocolError('launcher error code is invalid');
  }
  if (record.log !== undefined && (typeof record.log !== 'string' || !SAFE_LOG.test(record.log))) {
    throw new ProtocolError('launcher log field must be a basename');
  }
  if (record.state === 'failed' && record.code === undefined) {
    throw new ProtocolError('failed launcher event has no error code');
  }
  if (record.url !== undefined) {
    if (typeof record.url !== 'string') throw new ProtocolError('launcher URL must be a string');
    let target;
    try {
      target = new URL(record.url);
    } catch {
      throw new ProtocolError('launcher URL is invalid');
    }
    const localHost = target.hostname === '127.0.0.1' || target.hostname === 'localhost';
    if (target.protocol !== 'http:' || !localHost || target.username || target.password) {
      throw new ProtocolError('launcher URL is not a local VISTA address');
    }
  }
  if (record.state === 'ready' && record.phase === 'ui' && record.url === undefined) {
    throw new ProtocolError('ready UI event has no URL');
  }
  if (record.url !== undefined && (record.phase !== 'ui' || record.state !== 'ready')) {
    throw new ProtocolError('launcher URL is valid only when the UI is ready');
  }

  return /** @type {ProgressEvent} */ ({
    protocol: PROTOCOL_VERSION,
    phase: record.phase,
    state: record.state,
    ...(record.label === undefined ? {} : { label: record.label }),
    ...(record.skipped === undefined ? {} : { skipped: record.skipped }),
    ...(record.url === undefined ? {} : { url: record.url }),
    ...(record.code === undefined ? {} : { code: record.code }),
    ...(record.log === undefined ? {} : { log: record.log }),
  });
}

export class StartupStateMachine {
  constructor() {
    this.started = false;
    this.lastPhaseIndex = -1;
    this.status = 'starting';
    this.title = 'Preparing your workspace';
    this.message = 'Waiting for VISTA to begin.';
    this.failureMessage = '';
    this.canRetry = false;
    this.url = '';
    this.failure = null;
    /** @type {Array<{phase: string, label: string, state: (typeof STATES)[number], skipped: boolean}>} */
    this.activities = Object.entries(ACTIVITY_LABELS).map(([phase, label]) => ({
      phase,
      label,
      state: /** @type {const} */ ('pending'),
      skipped: false,
    }));
  }

  /** @param {string} line */
  acceptLine(line) {
    const event = parseProgressLine(line);
    this.accept(event);
    return this.snapshot();
  }

  /** @param {ProgressEvent} event */
  accept(event) {
    if (!this.started) {
      if (event.phase !== 'preflight' || event.state !== 'running') {
        throw new ProtocolError('first launcher event must start preflight');
      }
      this.started = true;
    }

    if (event.phase === 'stopping') {
      if (event.state !== 'running') throw new ProtocolError('stopping event must be running');
      // Both launchers stop their services on the way out of a failure too, so
      // the failure, which says what went wrong and where, is what stays.
      if (this.status === 'failed') return;
      this.status = 'stopping';
      this.title = 'Stopping VISTA';
      this.message = event.label ?? 'Closing services and sandboxes.';
      this.canRetry = false;
      return;
    }

    const phaseIndex = this.activities.findIndex((activity) => activity.phase === event.phase);
    if (phaseIndex < this.lastPhaseIndex) throw new ProtocolError('launcher phase moved backwards');
    const activity = this.activities[phaseIndex];
    if (!activity) throw new ProtocolError('launcher event has no visible activity');
    if (phaseIndex > this.lastPhaseIndex && event.state !== 'running') {
      throw new ProtocolError('launcher phase did not begin with running');
    }
    if (event.state !== 'running' && event.state !== 'failed' && activity.state !== 'running') {
      throw new ProtocolError('launcher completed a phase that was not running');
    }
    this.lastPhaseIndex = Math.max(this.lastPhaseIndex, phaseIndex);
    activity.state = event.state;
    activity.label = event.label ?? activity.label;
    activity.skipped = event.skipped === true;

    if (event.state === 'failed') {
      this.status = 'failed';
      this.title = 'VISTA needs attention';
      this.message = `${activity.label} did not complete.`;
      const failureCode = event.code ?? 'startup-error';
      this.failureMessage = Object.hasOwn(FAILURE_MESSAGES, failureCode)
        ? FAILURE_MESSAGES[/** @type {keyof typeof FAILURE_MESSAGES} */ (failureCode)]
        : FAILURE_MESSAGES['startup-error'];
      this.failure = { phase: event.phase, code: event.code ?? 'startup-error', log: event.log ?? '' };
      this.canRetry = true;
      return;
    }
    if (event.phase === 'ui' && event.state === 'ready') {
      this.status = 'ready';
      this.title = 'Opening VISTA';
      this.message = 'Your workspace is ready.';
      this.url = event.url ?? '';
      return;
    }
    this.message = event.state === 'running'
      ? activity.label
      : `${activity.label}${activity.skipped ? ' was already prepared' : ' is ready'}.`;
  }

  /** @param {string} detail */
  failProtocol(detail) {
    this.status = 'failed';
    this.title = 'VISTA needs attention';
    this.message = 'The startup launcher sent an invalid response.';
    this.failureMessage = FAILURE_MESSAGES['protocol-error'];
    this.failure = { phase: 'preflight', code: 'protocol-error', log: 'window.log', detail };
    this.canRetry = false;
    return this.snapshot();
  }

  markStopping() {
    this.status = 'stopping';
    this.title = 'Stopping VISTA';
    this.message = 'Closing services and sandboxes.';
    this.canRetry = false;
    return this.snapshot();
  }

  /**
   * A preflight failure found by the application itself, before the launcher
   * could report anything (on Windows, an AllSigned execution policy).
   *
   * @param {keyof typeof FAILURE_MESSAGES} code
   */
  failPreflight(code) {
    this.acceptLine(JSON.stringify({ protocol: PROTOCOL_VERSION, phase: 'preflight', state: 'running' }));
    return this.acceptLine(JSON.stringify({ protocol: PROTOCOL_VERSION, phase: 'preflight', state: 'failed', code }));
  }

  /** @param {string} message */
  failLauncher(message) {
    this.status = 'failed';
    this.title = 'VISTA needs attention';
    this.message = 'The startup launcher stopped unexpectedly.';
    this.failureMessage = message;
    const phase = this.activities[this.lastPhaseIndex]?.phase ?? 'preflight';
    this.failure = { phase, code: 'startup-error', log: 'window.log' };
    this.canRetry = true;
    const activity = this.activities[this.lastPhaseIndex];
    if (activity) activity.state = 'failed';
    return this.snapshot();
  }

  snapshot() {
    return {
      status: this.status,
      title: this.title,
      message: this.message,
      failureMessage: this.failureMessage,
      canRetry: this.canRetry,
      url: this.url,
      failure: this.failure ? { ...this.failure } : null,
      activities: this.activities.map((activity) => ({ ...activity })),
    };
  }
}

/**
 * A copyable diagnostic summary made only from normalized, allow-listed state.
 * @param {ReturnType<StartupStateMachine['snapshot']>} snapshot
 * @param {{ version: string, platform: string }} context
 */
export function formatDiagnostics(snapshot, context) {
  const failure = snapshot.failure;
  return [
    `VISTA ${context.version}`,
    `Platform: ${context.platform}`,
    `Phase: ${failure?.phase ?? 'unknown'}`,
    `Code: ${failure?.code ?? 'unknown'}`,
    `Log: ${failure?.log || 'window.log'}`,
  ].join('\n');
}
