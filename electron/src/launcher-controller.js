// @ts-check
import { spawn } from 'node:child_process';
import { closeSync, mkdirSync, openSync } from 'node:fs';
import path from 'node:path';
import readline from 'node:readline';

import { ProtocolError, StartupStateMachine } from './startup-protocol.js';

const STOP_TIMEOUT_MS = 12_000;

/** @typedef {ReturnType<StartupStateMachine['snapshot']>} StartupSnapshot */

export class LauncherController {
  /**
   * @param {object} options
   * @param {string} options.launcherPath
   * @param {string} options.logPath
   * @param {(snapshot: StartupSnapshot) => void} options.onState
   * @param {(url: string) => void} options.onReady
   * @param {typeof spawn} [options.spawnProcess]
   */
  constructor({ launcherPath, logPath, onState, onReady, spawnProcess = spawn }) {
    this.launcherPath = launcherPath;
    this.logPath = logPath;
    this.onState = onState;
    this.onReady = onReady;
    this.spawnProcess = spawnProcess;
    this.machine = new StartupStateMachine();
    this.current = this.machine.snapshot();
    /** @type {import('node:child_process').ChildProcess | null} */
    this.child = null;
    /** @type {readline.Interface | null} */
    this.lines = null;
    /** @type {Promise<void>} */
    this.exitPromise = Promise.resolve();
    /** @type {(() => void) | null} */
    this.resolveExit = null;
    this.readySent = false;
    this.stopping = false;
  }

  start() {
    if (this.child) throw new Error('launcher is already running');
    this.machine = new StartupStateMachine();
    this.current = this.machine.snapshot();
    this.readySent = false;
    this.stopping = false;

    mkdirSync(path.dirname(this.logPath), { recursive: true });
    const log = openSync(this.logPath, 'a');
    let child;
    try {
      child = this.spawnProcess(
        this.launcherPath,
        ['--supervised', '--progress=jsonl'],
        { stdio: ['pipe', 'pipe', log] },
      );
    } finally {
      closeSync(log);
    }
    this.child = child;
    this.exitPromise = new Promise((resolve) => { this.resolveExit = resolve; });
    this.emit();

    if (!child.stdout) {
      this.protocolFailure('launcher stdout is unavailable');
      return;
    }
    this.lines = readline.createInterface({ input: child.stdout, crlfDelay: Infinity });
    this.lines.on('line', (line) => this.acceptLine(line));
    child.once('error', (error) => {
      const errorCode = /** @type {NodeJS.ErrnoException} */ (error).code ?? 'process error';
      this.launcherFailure(`The launcher could not be started (${errorCode}).`);
      this.handleExit(child, -1, null);
    });
    child.once('exit', (code, signal) => this.handleExit(child, code, signal));
  }

  /** @param {string} line */
  acceptLine(line) {
    if (!this.child) return;
    try {
      this.current = this.machine.acceptLine(line);
      this.emit();
      if (this.current.status === 'ready' && !this.readySent) {
        this.readySent = true;
        this.onReady(this.current.url);
      }
    } catch (error) {
      this.protocolFailure(error instanceof ProtocolError ? error.message : 'unknown protocol error');
    }
  }

  /** @param {string} detail */
  protocolFailure(detail) {
    if (!this.child) return;
    this.current = this.machine.failProtocol(detail);
    this.emit();
    this.child.kill('SIGTERM');
  }

  /** @param {string} message */
  launcherFailure(message) {
    this.current = this.machine.failLauncher(message);
    this.emit();
  }

  /**
   * @param {import('node:child_process').ChildProcess} child
   * @param {number | null} code
   * @param {NodeJS.Signals | null} signal
   */
  handleExit(child, code, signal) {
    if (this.child !== child) return;
    this.lines?.close();
    this.lines = null;
    this.child = null;
    this.resolveExit?.();
    this.resolveExit = null;
    if (!this.stopping && this.current.status !== 'failed') {
      const reason = signal ? `signal ${signal}` : `exit ${code ?? 'unknown'}`;
      this.current = this.machine.failLauncher(`The launcher stopped during startup (${reason}).`);
    }
    this.emit();
  }

  emit() {
    const snapshot = this.machine.snapshot();
    snapshot.canRetry = snapshot.canRetry && this.child === null;
    this.current = snapshot;
    this.onState(snapshot);
  }

  retry() {
    if (this.child || this.current.status !== 'failed' || !this.current.canRetry) return false;
    this.start();
    return true;
  }

  /** @param {number} [timeoutMs] */
  async stop(timeoutMs = STOP_TIMEOUT_MS) {
    const child = this.child;
    if (!child) return;
    this.stopping = true;
    this.current = this.machine.markStopping();
    this.emit();
    child.kill('SIGTERM');
    let timer;
    await Promise.race([
      this.exitPromise,
      new Promise((resolve) => { timer = setTimeout(resolve, timeoutMs); }),
    ]);
    clearTimeout(timer);
    if (this.child === child) {
      child.kill('SIGKILL');
      await this.exitPromise;
    }
  }
}
